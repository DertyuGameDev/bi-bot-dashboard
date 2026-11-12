from typing import Literal
from uuid import uuid4
from pydantic import Field, model_validator
from shared.contracts import Strict as Strict
from shared.preflight_schema import Routing
from shared.dashboard_brief import BriefPatch


def uid():
    return uuid4().hex


class Filters(Strict):
    regions: list[str] = Field(default_factory=list, max_length=100)
    outlets: list[str] = Field(default_factory=list, max_length=1000)
    products: list[str] = Field(default_factory=list, max_length=1000)
    clients: list[str] = Field(default_factory=list, max_length=1000)
    start: str | None = None
    end: str | None = None
    min_amount: float | None = None
    max_amount: float | None = None
    top_n: int = Field(10, ge=1, le=50)
    dimension: Literal["region", "outlet", "product", "client"] = "region"
    metric: Literal["amount", "quantity", "discount", "deals", "completion"] = "amount"
    granularity: Literal["month", "quarter", "year"] = "month"
    value_mode: Literal["absolute", "percent", "dynamics"] = "absolute"

    @model_validator(mode="after")
    def dates_valid(self):
        from datetime import date

        for v in (self.start, self.end):
            if v:
                date.fromisoformat(v)
        if self.start and self.end and self.start > self.end:
            raise ValueError("Начальная дата позже конечной")
        if self.min_amount is not None and self.max_amount is not None and self.min_amount > self.max_amount:
            raise ValueError("Минимальная сумма больше максимальной")
        return self


class Calculation(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["formula", "share", "mom", "yoy", "cumulative", "mean", "median", "min", "max", "rank"] = (
        "formula"
    )
    expression: str = Field("fact / plan * 100", max_length=300)
    column: str = "fact"
    group_by: list[str] = Field(default_factory=list, max_length=5)
    dependencies: list[str] = Field(default_factory=list)
    base: str = "Отфильтрованная таблица; предыдущий календарный период для динамики"


class ChartSpec(Strict):
    id: str = Field(default_factory=uid, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    type: str = "grouped_bar"
    title: str = Field("План и факт", max_length=160)
    source: str = "period"
    x: str = "period"
    group: str = Field(
        "", description="Строки тепловой карты; x задаёт столбцы, например group=outlet, x=period"
    )
    y: list[str] = Field(default_factory=lambda: ["plan", "fact"], min_length=1, max_length=4)
    sort: str = ""
    descending: bool = True
    number_format: Literal["money", "percent", "points", "number"] = "money"
    xlabel: str = ""
    ylabel: str = ""
    width: int = Field(640, ge=320, le=1200)
    height: int = Field(310, ge=120, le=800)
    hidden: list[str] = Field(default_factory=list)
    zoom: list[float] | None = Field(None, min_length=4, max_length=4)


class DashboardSpec(Strict):
    id: str = Field(default_factory=uid)
    version: int = 1
    table_id: str = ""
    table_version: int = 1
    title: str = Field("Обзор продаж", max_length=160)
    description: str = ""
    layout: Literal["two-columns", "one-column"] = "two-columns"
    sync_zoom: bool = False
    kpis: list[str] = Field(default_factory=lambda: ["plan", "fact", "completion", "deviation"])
    controls: list[str] = Field(
        default_factory=lambda: [
            "regions",
            "outlets",
            "products",
            "clients",
            "dates",
            "amount",
            "top_n",
            "dimension",
            "metric",
            "granularity",
            "value_mode",
        ]
    )
    charts: list[ChartSpec] = Field(default_factory=list, max_length=4)


class ToolCall(Strict):
    tool: str
    arguments: str = "{}"


class AgentPlan(Strict):
    intent: Literal["text", "table", "calculation", "chart", "dashboard", "modify", "storage", "clarify"]
    clarification: str
    calls: list[ToolCall] = Field(max_length=10)


class AgentStep(Strict):
    """One ReAct decision. Only a final decision contains the user-facing answer."""

    preflight: Routing | None = Field(
        None,
        description="Структурированный разбор намерений в первом шаге; далее null. Это действия и параметры, не скрытые рассуждения.",
    )
    brief_update: BriefPatch | None = Field(
        None,
        description="Обновление скрытой спецификации этого чата по запросу/уточнению. null — сохранить; неизвестные обязательные параметры не выдумывать.",
    )
    kind: Literal["act", "final"]
    decision: str = Field(description="Краткое наблюдаемое действие, без скрытых рассуждений")
    calls: list[ToolCall] = Field(max_length=1)
    answer: str
    intent: Literal["text", "table", "calculation", "chart", "dashboard", "modify", "storage", "clarify"]
    charts: list[str] = Field(
        description="IDs графиков для показа; * означает все; пустой список — без графиков"
    )
    table: bool = Field(description="Показать текущую таблицу прямо в сообщении MY со страницами и CSV")
    exports: list[Literal["csv", "xlsx", "png", "html", "svg"]]
    controls: list[Literal["regions", "dimension", "granularity", "csv", "save", "saved"]]
