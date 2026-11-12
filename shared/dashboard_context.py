"""Independent, owner-scoped working state for each dashboard in the conversation."""

import copy
from shared.analytics import empty_state, dataset_frame
from shared.models import ChartSpec


def active_message(state):
    if not state.get("dataset"):
        return None
    matches = [
        m
        for m in state["messages"]
        if m.get("dashboard", {}).get("table_id") == state["dataset"]["id"]
        and m["dashboard"]["id"] == state["dashboard"]["id"]
    ]
    selected = (state.get("workspace") or {}).get("message_id")
    return next((m for m in matches if m.get("id") == selected), matches[-1] if matches else None)


def select_workspace(state, message_id, entry=None):
    index = next(i for i, message in enumerate(state["messages"]) if message.get("id") == message_id)
    query = next((m["content"] for m in reversed(state["messages"][:index]) if m["role"] == "user"), "")
    state["workspace"] = {
        "saved_id": state.get("saved_id"),
        "message_id": message_id,
        "title": state["dashboard"]["title"],
        "query": (entry or {}).get("query", query),
        "created": (entry or {}).get("created"),
        "updated": (entry or {}).get("updated"),
    }


def card_state(state, message):
    result = empty_state()
    for key in [
        "dataset",
        "snapshot",
        "filters",
        "dashboard",
        "calculations",
        "calculation_history",
        "saved_id",
        "saved_dashboard_version",
        "filter_notice",
    ]:
        if key in state:
            # Source rows and captured tables are immutable in analytics operations.
            # Copy their metadata, not tens of thousands of unchanged dictionaries.
            result[key] = (
                dict(state[key])
                if key in {"dataset", "snapshot"} and state[key]
                else copy.deepcopy(state[key])
            )
    result["messages"] = [copy.deepcopy(message)]
    return result


def filter_options(state):
    frame = dataset_frame(state)
    regions = state["filters"]["regions"]
    outlets = frame.loc[frame.region.isin(regions)] if regions else frame
    return {
        "regions": sorted(frame.region.dropna().unique().tolist()),
        "outlets": sorted(outlets.outlet.dropna().unique().tolist()),
        "products": sorted(frame["product"].dropna().unique().tolist()),
        "clients": sorted(frame.client.dropna().unique().tolist()),
    }


def restore_legacy(base, message):
    """Use only a matching frozen dataset; never fetch a replacement from the source."""
    data = message["dashboard"]
    if not base.get("dataset") or base["dataset"]["id"] != data["table_id"]:
        raise ValueError("Для этого ответа не сохранился снимок данных. Постройте дашборд новым запросом.")
    scope = card_state(base, message)
    scope["filters"] = copy.deepcopy(data["filters"])
    scope["snapshot"] = None
    originals = (
        {c["id"]: c for c in base["dashboard"]["charts"]} if base["dashboard"]["id"] == data["id"] else {}
    )
    specs = data.get("source_specs") or [originals.get(c["id"], c["spec"]) for c in data["charts"]]
    scope["dashboard"].update(
        id=data["id"],
        table_id=data["table_id"],
        title=data["title"],
        charts=[ChartSpec.model_validate({**c, "height": max(120, c["height"])}).model_dump() for c in specs],
    )
    scope["saved_id"] = data.get("saved_id")
    scope["saved_dashboard_version"] = scope["dashboard"]["version"] if data.get("saved") else None
    return scope
