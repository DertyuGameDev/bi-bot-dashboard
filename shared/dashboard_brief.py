"""Small persistent per-conversation dashboard memory, updated by the model."""

import copy
from datetime import date, datetime, timezone
from typing import Literal
from pydantic import Field, field_validator, model_validator
from shared.contracts import Strict

KEY = "dashboard_brief"
FieldName = Literal[
    "goal",
    "metric",
    "dimension",
    "period",
    "granularity",
    "value_mode",
    "regions",
    "outlets",
    "products",
    "clients",
    "chart_type",
    "layout",
    "top_n",
]


class BriefPeriod(Strict):
    mode: Literal["range", "all_time"]
    start: str | None = None
    end: str | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        for value in [self.start, self.end]:
            if value is not None:
                date.fromisoformat(value)
        if self.start and self.end and self.start > self.end:
            raise ValueError("Начальная дата позже конечной")
        if self.mode == "all_time" and (self.start or self.end):
            raise ValueError("Для всей истории границы определяются источником")
        return self


class BriefFields(Strict):
    goal: str | None = Field(None, max_length=1600)
    metric: str | None = Field(None, max_length=100)
    dimension: str | None = Field(None, max_length=100)
    period: BriefPeriod | None = None
    granularity: Literal["month", "quarter", "year"] | None = None
    value_mode: Literal["absolute", "percent", "dynamics"] | None = None
    regions: list[str] | None = Field(None, max_length=100)
    outlets: list[str] | None = Field(None, max_length=1000)
    products: list[str] | None = Field(None, max_length=1000)
    clients: list[str] | None = Field(None, max_length=1000)
    chart_type: str | None = Field(None, max_length=40)
    layout: Literal["one-column", "two-columns"] | None = None
    top_n: int | None = Field(None, ge=1, le=50)

    @field_validator("goal", "metric", "dimension", "chart_type", mode="before")
    @classmethod
    def no_blank_values(cls, value):
        return value.strip() or None if isinstance(value, str) else value


class BriefPatch(BriefFields):
    reset: bool = False
    clear_fields: list[FieldName] = Field(default_factory=list, max_length=13)
    require_dimension: bool | None = None


class DashboardBrief(BriefFields):
    revision: int = 0
    source_request: str = ""
    clarifications: list[str] = Field(default_factory=list)
    require_dimension: bool = False
    pending_question: str | None = None
    bound_dashboard_id: str | None = None
    effective_period: list[str | None] | None = None
    charts: list[dict] = Field(default_factory=list)
    calculations: list[dict] = Field(default_factory=list)
    updated: str | None = None


def missing(brief):
    required = ["goal", "metric", "period"] + (["dimension"] if brief.require_dimension else [])
    result = [key for key in required if getattr(brief, key) is None]
    if brief.period and brief.period.mode == "range" and not (brief.period.start and brief.period.end):
        result.append("period")
    return result


def from_dashboard(state):
    f = state["filters"]
    period = None
    if f.get("start") and f.get("end"):
        period = BriefPeriod(mode="range", start=f["start"], end=f["end"])
    return DashboardBrief(
        goal=((state.get("workspace") or {}).get("query") or state["dashboard"]["title"])[:1600],
        metric=f["metric"],
        dimension=f["dimension"],
        period=period,
        granularity=f["granularity"],
        value_mode=f["value_mode"],
        top_n=f["top_n"],
        regions=f["regions"],
        outlets=f["outlets"],
        products=f["products"],
        clients=f["clients"],
        layout=state["dashboard"]["layout"],
        bound_dashboard_id=state["dashboard"]["id"],
        charts=copy.deepcopy(state["dashboard"]["charts"]),
        calculations=copy.deepcopy(state["calculations"]),
        effective_period=[f.get("start"), f.get("end")],
    )


def prepare(state):
    raw = state.get(KEY)
    brief = (
        DashboardBrief.model_validate(raw)
        if raw is not None
        else from_dashboard(state)
        if state.get("dataset")
        else DashboardBrief()
    )
    state[KEY] = brief.model_dump()
    return brief


def apply_patch(state, patch, text):
    old = prepare(state)
    brief = DashboardBrief(revision=old.revision) if patch.reset else old.model_copy(deep=True)
    for key in BriefFields.model_fields:
        value = getattr(patch, key)
        if value is not None:
            setattr(brief, key, copy.deepcopy(value))
    for key in patch.clear_fields:
        setattr(brief, key, None)
    if patch.require_dimension is not None:
        brief.require_dimension = patch.require_dimension
    if not brief.source_request:
        brief.source_request = text
    elif text != brief.source_request and text not in brief.clarifications:
        brief.clarifications = [*brief.clarifications, text][-8:]
    brief.bound_dashboard_id = None  # Bind only after the actual selection/configuration matches.
    brief.pending_question = None
    brief.revision += 1
    brief.updated = datetime.now(timezone.utc).isoformat()
    state[KEY] = brief.model_dump()
    return brief


def context(state):
    brief = prepare(state)
    result = brief.model_dump(exclude={"charts", "calculations"})
    absent = missing(brief)
    result.update(
        missing=absent,
        status="collecting" if absent else "ready",
        charts=brief.charts,
        calculations=brief.calculations,
    )
    return result


def check_tool(state, tool, args):
    analytical = {
        "fetch_data",
        "fetch_records",
        "analyze_table",
        "analyze_records",
        "join_records",
        "profile_table",
        "compute_column",
        "create_chart",
        "update_chart",
        "update_dashboard",
        "apply_filters",
        "validate_result",
        "export",
        "save_dashboard",
    }
    if tool not in analytical:
        return
    brief = prepare(state)
    absent = missing(brief)
    if absent:
        raise ValueError(
            "В скрытой спецификации не заполнены обязательные поля: "
            + ", ".join(absent)
            + ". Обнови brief_update по сообщениям. Не подставляй даты по умолчанию. Если сведений нет, заверши intent=clarify и задай свой вопрос без загрузки данных и графиков."
        )
    if tool in {"create_dashboard", "create_chart"} and state.get("dataset"):
        period = brief.period
        if period.mode == "range" and (
            state["filters"].get("start") != period.start or state["filters"].get("end") != period.end
        ):
            raise ValueError(
                "Период спецификации отличается от текущих фильтров. Сначала загрузи нужный период или примени согласованные фильтры."
            )


def bind(state):
    brief = prepare(state)
    if not state.get("dataset") or missing(brief):
        return
    current = from_dashboard(state)
    for key in BriefFields.model_fields:
        if key not in {"goal", "chart_type"}:
            value = getattr(current, key)
            # Keep explicit all-time intent; the source supplies actual boundaries elsewhere.
            if key == "period" and brief.period and brief.period.mode == "all_time":
                continue
            setattr(brief, key, value)
    brief.charts, brief.calculations = current.charts, current.calculations
    brief.bound_dashboard_id = current.bound_dashboard_id
    brief.effective_period = current.effective_period
    state[KEY] = brief.model_dump()


def sync_bound(state):
    if state.get(KEY) and state.get("dataset"):
        brief = DashboardBrief.model_validate(state[KEY])
        if brief.bound_dashboard_id == state["dashboard"]["id"] and not missing(brief):
            if brief.period.mode == "all_time" and brief.effective_period != [
                state["filters"].get("start"),
                state["filters"].get("end"),
            ]:
                brief.period = from_dashboard(state).period
                state[KEY] = brief.model_dump()
            bind(state)
            changed = DashboardBrief.model_validate(state[KEY])
            changed.revision += 1
            changed.updated = datetime.now(timezone.utc).isoformat()
            state[KEY] = changed.model_dump()
            state["debug"][KEY] = context(state)


def adopt_workspace(state, previous=None):
    old_revision = (previous or {}).get(KEY, {}).get("revision", 0)
    brief = from_dashboard(state) if state.get("dataset") else DashboardBrief()
    brief.revision = old_revision + 1
    state[KEY] = brief.model_dump()
    state["debug"][KEY] = context(state)


def archive_query(state, text):
    raw = state.get(KEY)
    if raw and raw.get("source_request") and raw.get("clarifications"):
        return (raw["source_request"] + " · Уточнения: " + "; ".join(raw["clarifications"]))[:4000]
    return text


INSTRUCTION = """В context.dashboard_brief находится скрытая таблица параметров ТОЛЬКО ЭТОГО чата. Это постоянная память, не текст для пользователя. В первом обычном шаге обнови её через brief_update, если запрос касается аналитики/дашборда или отвечает на ожидаемое уточнение. Новое сообщение дополняет исходный goal, а не заменяет его голой датой. null в полях патча сохраняет прежнее значение; reset=true начинает действительно новый запрос, clear_fields явно стирает поля. На приветствие, разговор, объяснение или вопрос о возможностях brief_update=null: не теряй ожидающие параметры.
Обязательно нужны конкретный goal (что анализируем), metric и period. Для сравнения без названного разреза require_dimension=true и уточни объекты сравнения. Не выдумывай обязательные сведения. «Продажи/выручка» означает metric=amount, «по точкам» — dimension=outlet; это информация запроса. Период без указания пользователя или известного состояния не подставляй из default инструментов и не заменяй на последний год. «За всё время» — period.mode=all_time. Относительные периоды переводи в даты по today. Если указан только год, это весь календарный год; если только месяц без понятного года — уточни год. Известный период выбранного дашборда можно использовать при продолжении работы. Для нового анализа без периода спроси период.
Когда missing непустой, не загружай данные и не рисуй дашборд: kind=final, intent=clarify, без charts/table/exports, answer — короткий конкретный вопрос своими словами (например, за какой период сравнить выручку). Сохрани уже известные поля. Когда ответ пользователя заполнил обязательное, продолжи первоначальную задачу инструментами, не требуй повторять весь запрос и не ограничивайся «принято». Не требуй необязательные цвет, раскладку, тип графика, top_n, список всех точек или подробный план: подходящий вид выбирай сам. После первого обновления в обычных ReAct-шагах brief_update=null, кроме исправления обнаруженной ошибки спецификации."""
INSTRUCTION += """ Смена метрики, разреза или вида в том же чате сохраняет согласованный период. reset=true нужен при явном начале с нуля/сбросе, не при любой новой формулировке. Если после «Новый анализ» goal пуст и pending_question=null, не переноси цель из прежних сообщений: заполняй новую спецификацию по новому запросу."""
