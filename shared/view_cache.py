"""Bounded owner/card-scoped rendered variants, reusable after a restart."""

import hashlib
import json
from pathlib import Path
from shared.storage import pack, unpack, now
from shared.charts.registry import RENDER_DASHBOARD, RENDER_WIDTH, RENDER_VERSION


class ViewCache:
    def __init__(self, store):
        self.store = store
        root = Path(__file__).parent
        engine = hashlib.sha256()
        for path in sorted(
            [
                root / name
                for name in ["analytics.py", "chat.py", "calculations.py", "view_modes.py", "kpi_summary.py"]
            ]
            + list((root / "charts").glob("*.py"))
        ):
            engine.update(path.read_bytes())
        self.engine = engine.hexdigest()
        with store.connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS rendered_views(owner TEXT NOT NULL,message_id TEXT NOT NULL,cache_key TEXT NOT NULL,updated TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,message_id,cache_key))"
            )

    def key(self, state, message, indexes):
        source = hashlib.sha256(
            json.dumps(
                [state["dataset"], state["calculations"]], ensure_ascii=False, sort_keys=True, allow_nan=False
            ).encode()
        ).hexdigest()
        snapshot = dict(state["snapshot"]) if state.get("snapshot") else None
        if snapshot:
            snapshot.pop("captured_at", None)
        dashboard = {k: v for k, v in state["dashboard"].items() if k != "version"}
        value = [
            self.engine,
            RENDER_VERSION,
            RENDER_DASHBOARD.get(),
            RENDER_WIDTH.get(),
            source,
            snapshot,
            state["filters"],
            state["calculations"],
            dashboard,
            state.get("saved_id"),
            message.get("analysis_filters"),
            message.get("dashboard", {}).get("title_filters"),
            indexes,
        ]
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(), source

    def get(self, owner, ident, key):
        with self.store.connect() as c:
            row = c.execute(
                "SELECT body FROM rendered_views WHERE owner=? AND message_id=? AND cache_key=?",
                (owner, ident, key),
            ).fetchone()
        return unpack(row["body"]) if row else None

    def put(self, owner, ident, key, value):
        with self.store.connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO rendered_views VALUES(?,?,?,?,?)",
                (owner, ident, key, now(), pack(value)),
            )
            c.execute(
                "DELETE FROM rendered_views WHERE owner=? AND message_id=? AND cache_key NOT IN (SELECT cache_key FROM rendered_views WHERE owner=? AND message_id=? ORDER BY updated DESC LIMIT 12)",
                (owner, ident, owner, ident),
            )
            c.execute(
                "DELETE FROM rendered_views WHERE owner=? AND rowid NOT IN (SELECT rowid FROM rendered_views WHERE owner=? ORDER BY updated DESC LIMIT 48)",
                (owner, owner),
            )
