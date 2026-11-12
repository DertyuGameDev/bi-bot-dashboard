"""Enrich legacy snapshots with verified client membership. Stop MY before --apply."""

import argparse
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from shared.config import Settings
from shared.storage import Store
from shared.source import Source
from shared.client_activity import verified_activity
from shared.analytics import dashboard_kpis, dataset_frame
from shared.calculations import filtered
from shared.models import Filters


def enrich_state(state, source, cache):
    dataset = state.get("dataset")
    if not dataset or dataset["kind"] != "sales" or dataset.get("client_activity") is not None:
        return False
    key = (dataset["query_start"], dataset["query_end"])
    if key not in cache:
        result = source.load_records(
            "sales",
            ["sale_date", "outlet_id", "product_id", "client_id", "amount"],
            [
                {"column": "sale_date", "operator": "gte", "value": key[0]},
                {"column": "sale_date", "operator": "lte", "value": key[1]},
            ],
        )
        cache[key] = pd.DataFrame(result["rows"], columns=result["columns"])
    dataset["client_activity"] = verified_activity(dataset, cache[key])
    raw = dataset_frame(state)
    for message in state["messages"]:
        dashboard = message.get("dashboard")
        if dashboard and dashboard.get("table_id") == dataset["id"]:
            shadow = {**state, "filters": dashboard["filters"]}
            frame = filtered(raw, Filters.model_validate(shadow["filters"]), monthly=True)
            dashboard["kpis"] = dashboard_kpis(shadow, frame, raw)
    if state.get("snapshot"):
        state["snapshot"]["kpis"] = dashboard_kpis(state, raw=raw)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--storage")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = Settings(**({"storage": args.storage} if args.storage else {}))
    store, source = Store(settings.storage), Source(settings)
    prepared, cache = [], {}
    with store.connect() as connection:
        for table, primary in [("sessions", "owner"), ("dashboards", "id")]:
            for row in connection.execute(f"SELECT {primary},owner,state FROM {table}"):
                state = store._unpack_state(row["owner"], row["state"], connection)
                if enrich_state(state, source, cache):
                    if table == "sessions":
                        state["revision"] += 1
                    prepared.append((table, primary, row[primary], row["state"], state, row["owner"]))
    backup = None
    if args.apply and prepared:
        backup = (
            Path(settings.storage).parent
            / "backups"
            / ("before-kpi-activity-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3")
        )
        backup.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3

        with store.connect() as connection, sqlite3.connect(backup) as copy:
            connection.backup(copy)
        backup.chmod(0o600)
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table, primary, ident, before, state, owner in prepared:
                latest = connection.execute(
                    f"SELECT state FROM {table} WHERE {primary}=?", (ident,)
                ).fetchone()
                if latest is None or latest["state"] != before:
                    raise ValueError("Состояние изменилось во время обогащения; изменения отменены")
                if table == "sessions":
                    connection.execute(
                        "UPDATE sessions SET state=?,revision=? WHERE owner=?",
                        (store._pack_state(connection, owner, state), state["revision"], ident),
                    )
                else:
                    connection.execute(
                        "UPDATE dashboards SET state=? WHERE id=?",
                        (store._pack_state(connection, owner, state), ident),
                    )
    print(
        {
            "verified_snapshots": len(prepared),
            "source_ranges": len(cache),
            "applied": args.apply,
            "backup_created": backup is not None,
        }
    )


if __name__ == "__main__":
    main()
