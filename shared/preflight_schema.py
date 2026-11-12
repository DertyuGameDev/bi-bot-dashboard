"""Shared intent contract for rules, standalone classifier and first ReAct step."""

from typing import Literal
from pydantic import Field, model_validator
from shared.contracts import Strict

Kind = Literal[
    "chart.create",
    "chart.modify",
    "chart.remove",
    "chart.reorder",
    "dashboard.create",
    "dashboard.layout",
    "calculation.add",
    "calculation.modify",
    "calculation.remove",
    "calculation.undo",
    "data.filter",
    "data.refresh",
    "data.query",
    "data.change",
    "table.show",
    "export",
    "storage.save",
    "storage.open",
    "storage.list",
    "storage.rename",
    "storage.delete",
    "storage.copy",
    "publication.share",
    "publication.revoke",
    "analysis.explain",
    "analysis.compare",
    "conversation",
    "unknown",
]


class Assignment(Strict):
    name: Literal[
        "type",
        "metric",
        "dimension",
        "granularity",
        "value_mode",
        "sort",
        "descending",
        "hidden",
        "layout",
        "expression",
        "column",
        "title",
        "start",
        "end",
        "top_n",
        "format",
        "regions",
        "series",
        "position",
        "source",
        "zoom",
    ]
    value: str = Field(max_length=400)


class RoutedAction(Strict):
    kind: Kind
    chart_ids: list[str]
    parameters: list[Assignment]
    missing: list[str]


class Routing(Strict):
    status: Literal["resolved", "needs_model"]
    confidence: Literal["high", "medium", "low"]
    actions: list[RoutedAction] = Field(max_length=8)
    scope: Literal["selected", "new", "none"]
    question: str

    @model_validator(mode="after")
    def consistent(self):
        if self.status == "resolved" and any(a.missing for a in self.actions):
            # Missing fields are authoritative. An advisory status must not reject a valid clarification.
            self.status = "needs_model"
            self.confidence = "low"
        return self
