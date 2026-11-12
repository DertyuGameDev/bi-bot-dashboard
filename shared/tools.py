"""Validated operations exposed to the model and both platforms."""

import time
from typing import Literal
from pydantic import Field
from shared.models import Strict, Calculation, ChartSpec, DashboardSpec, Filters
from shared.source import Source
from shared.analytics import (
    install_dataset,
    materialize,
    comparison_frame,
    dashboard_kpis,
    default_charts,
    capture_snapshot,
    present,
)
from shared.calculations import dependencies
from shared.record_tools import RecordOperations, ReadRecords, AnalyzeRecords, JoinRecords


class Empty(Strict):
    pass


class Fetch(Strict):
    start: str = "2024-01-01"
    end: str = "2025-12-31"
    kind: Literal["sales", "discounts"] = "sales"
    all_time: bool = Field(
        False,
        description="Вся доступная история: определить реальные границы источника и выбрать полный период, игнорируя start/end",
    )


class Compute(Strict):
    action: Literal["add", "change", "delete", "undo"]
    calculation: Calculation | None = None
    name: str = ""


class NewChart(Strict):
    spec: ChartSpec


class ChangeChart(Strict):
    index: int = Field(ge=1, le=4)
    # Revalidated as complete ChartSpec after merge.
    changes: dict


class SyncZoom(Strict):
    enabled: bool


class NewDashboard(Strict):
    title: str = Field("Обзор продаж", max_length=160, min_length=1)
    description: str = Field("", max_length=2000)
    charts: list[ChartSpec] | None = Field(None, max_length=4)


class DashboardLayout(Strict):
    layout: Literal["one-column", "two-columns"] | None = None
    # Explicit complete order also supports removing charts. It cannot create or duplicate ids.
    chart_ids: list[str] | None = Field(None, max_length=4)


class FilterChange(Strict):
    filters: dict


class Save(Strict):
    title: str = Field("Продажи", max_length=160, min_length=1)
    as_new: bool = False


class Search(Strict):
    search: str = Field("", max_length=200)
    sort: Literal["updated", "created", "title"] = "updated"


class Identifier(Strict):
    id: str = Field(max_length=100, min_length=1)


class Rename(Identifier):
    title: str = Field(max_length=160, min_length=1)


class Export(Strict):
    format: Literal["csv", "xlsx", "png", "html", "svg"] = "csv"
    index: int = Field(1, ge=1, le=4)


class Analyze(Strict):
    source: Literal["period", "structure", "ranking", "table"] = "period"
    x: str = "period"
    columns: list[str] = Field(default_factory=lambda: ["fact", "plan", "completion"], max_length=10)
    sort: str = ""
    descending: bool = True
    limit: int = Field(25, ge=1, le=100)


TOOL_TYPES = {
    "set_sync_zoom": (
        SyncZoom,
        "Связать масштаб временных графиков по календарным периодам. Вертикальные оси и KPI не меняются.",
    ),
    "fetch_records": (
        ReadRecords,
        "Прочитать исходную PostgreSQL-таблицу в именованный набор, не меняя дашборд. Поля бери из inspect_schema; [] = все. Условия параметризованы, SQL только SELECT. Для новых клиентов clients.registered_date; новых контрактов contracts.start_date. Для первого заказа за всю историю загрузи sales без ограничения периода и найди min(sale_date) по client_id через analyze_records. Не ограничивай такие расчёты таблицей продаж fetch_data.",
    ),
    "analyze_records": (
        AnalyzeRecords,
        "Анализ именованного набора в pandas. Условия → группировка/даты → count/nunique/sum/mean/median/min/max/first/last → expressions (безопасная арифметика по колонкам) → calculations. date_column с bucket создаёт date (начало периода) и calendar_days; calendar_start/end дополняют календарь нулями для группировки только date. Среднее за календарный день: count/calendar_days. name сохраняет ВСЕ результаты для следующего инструмента; limit ограничивает только наблюдение. statistics содержит суммы/средние/минимумы/максимумы числовых результатов. Для нового клиента по первой покупке: сначала min(sale_date) по client_id, сохрани, затем группируй дату первой покупки и считай клиентов.",
    ),
    "join_records": (
        JoinRecords,
        "Соединить именованные наборы в pandas по ключам с проверкой кардинальности. Без many_to_many; не соединяй сырые продажи с месячным планом.",
    ),
    "inspect_schema": (Empty, "Прочитать фактическую схему и связи PostgreSQL."),
    "fetch_data": (
        Fetch,
        "Загрузить таблицу для графиков выручки/плана по точкам, регионам, продуктам и месяцам. Для конкретного года start=YYYY-01-01,end=YYYY-12-31; all_time=true для ВСЕЙ доступной истории с реальными границами и полным выбором периода. После загрузки можно create_dashboard/create_chart. SQL без вычислений. Обнуляет прежние вычисления; вызывай только когда периода/полей недостаточно. kind=discounts для клиента/причины скидки.",
    ),
    "profile_table": (Empty, "Показать типы, пропуски, примеры, суммы и текущую таблицу."),
    "analyze_table": (
        Analyze,
        "Получить числовые строки для ответа: period по времени, structure по category, ranking по filters.dimension, table исходные строки с x. columns включают fact,plan,completion,deviation,growth и вычисляемые колонки. Проценты пересчитываются от агрегированных сумм. sort по колонке; limit до 100.",
    ),
    "compute_column": (
        Compute,
        "Добавить/изменить/удалить/отменить вычисление. Имена fact, plan, quantity, discount. kind formula: expression ограниченная арифметика. share/mom/yoy/cumulative/mean/median/min/max/rank: column и group_by. Сохраняй зависимости.",
    ),
    "create_chart": (
        NewChart,
        "Добавить график, максимум 4. Типы: line,bar,grouped_bar,stacked_bar,horizontal_bar,scatter,histogram,heatmap,donut. source=period/structure/ranking/table. Стабильный id. Для heatmap x=period, group=outlet/region/product/client; строки агрегируются в pandas, TOP-N ограничивает число строк с явной подписью.",
    ),
    "update_chart": (
        ChangeChart,
        "Изменить график по индексу 1..4. Только поля ChartSpec, например sort=completion для третьего.",
    ),
    "create_dashboard": (
        NewDashboard,
        "Создать дашборд; charts=null даёт стандартные 4 графика продаж. Для одного графика передай список из одного ChartSpec.",
    ),
    "update_dashboard": (
        DashboardLayout,
        "Изменить структуру ТЕКУЩЕГО дашборда без потери id, таблицы, формул, фильтров и сохранения. layout=one-column/two-columns. chart_ids=null сохраняет все графики; полный список существующих id задаёт новый порядок и удаляет исключённые графики. Новые графики добавляются через create_chart.",
    ),
    "apply_filters": (
        FilterChange,
        "Изменить общие фильтры; regions,outlets,products,clients массивы строк; start/end ISO; dimension=region/outlet/product/client; metric=amount (выручка), discount (скидки), deals (число сделок), completion (отношение сумм факта и плана, %); quantity для совместимых единиц товара. granularity=month/quarter/year; value_mode=absolute/percent (доля выбранного итога до TOP-N)/dynamics (предыдущий календарный период, для completion — п.п.). top_n 1..50. Для лучших N точек задавай top_n и dimension=outlet, не фиксируй outlets. Смена региона сбрасывает точки; несовместимые точки удаляются с пояснением. UI работает без модели, динамика использует загруженную базу предыдущего периода.",
    ),
    "validate_result": (
        Empty,
        "Проверить расчёты KPI, колонки графиков и рендеринг. Вернуть наблюдаемые итоги.",
    ),
    "save_dashboard": (
        Save,
        "Сохранить полный снимок. as_new=true создаёт копию.",
    ),
    "search_dashboards": (Search, "Найти собственные сохранённые дашборды по названию/запросу."),
    "open_dashboard": (Identifier, "Открыть найденный снимок по id без обновления БД."),
    "rename_dashboard": (Rename, "Переименовать собственный снимок."),
    "delete_dashboard": (Identifier, "Удалить собственный снимок по явному запросу пользователя."),
    "export": (Export, "Подготовить ссылку на экспорт таблицы CSV/Excel или дашборда PNG/HTML/SVG."),
}


def catalog():
    return [
        {"name": name, "description": desc, "parameters": typ.model_json_schema()}
        for name, (typ, desc) in TOOL_TYPES.items()
    ]


def validate_call(name, args):
    if name not in TOOL_TYPES:
        raise ValueError("Модель запросила неизвестный инструмент")
    return TOOL_TYPES[name][0].model_validate(args)


class Tools(RecordOperations):
    def __init__(self, settings, store, owner, state):
        self.settings = settings
        self.store = store
        self.owner = owner
        self.state = state
        self.source = Source(settings)
        self.outputs = []

    def invoke(self, name, args):
        parsed = validate_call(name, args)
        start = time.perf_counter()
        result = getattr(self, name)(parsed)
        self.state["debug"].setdefault("tools", []).append(
            {
                "tool": name,
                "status": "ok",
                "ms": round((time.perf_counter() - start) * 1000, 1),
                "arguments": parsed.model_dump(exclude_none=True),
            }
        )
        self.state["debug"]["tools"] = self.state["debug"]["tools"][-40:]
        self.outputs.append({"tool": name, "result": result})
        return result

    def need_data(self):
        if not self.state["dataset"]:
            raise ValueError("Сначала получите данные инструментом fetch_data")

    def inspect_schema(self, args):
        return self.source.schema()

    def fetch_data(self, args):
        start, end = self.source.available_period(args.kind) if args.all_time else (args.start, args.end)
        result = self.source.load(start, end, args.kind)
        install_dataset(self.state, result)
        if args.all_time:
            self.state["filters"].update(start=result["query_start"], end=result["query_end"])
        return {
            "rows": len(result["rows"]),
            "source": result["source"],
            "quality": result["quality"],
            "period": [result["query_start"], result["query_end"]],
            "observed_end": result.get("observed_end"),
            "filters": self.state["filters"],
            "warnings": result.get("warnings", []),
        }

    def profile_table(self, args):
        self.need_data()
        view = present(self.state, False, limit=5)
        return {"table": view["dataset"], "kpis": view["kpis"]}

    def analyze_table(self, args):
        from shared.analytics import chart_frame
        from shared.source import records

        self.need_data()
        df, warnings, _ = materialize(self.state)
        spec = ChartSpec(
            source=args.source, x=args.x, y=args.columns, sort=args.sort, descending=args.descending
        )
        frame, effective = chart_frame(
            df, spec, self.state["filters"], self.state["calculations"], comparison_frame(self.state)
        )
        selected = list(dict.fromkeys([effective.x] + args.columns))
        missing = set(selected) - set(frame.columns)
        if missing:
            raise ValueError("Нет колонок: " + ", ".join(sorted(missing)))
        if args.sort:
            if args.sort not in frame:
                raise ValueError("Нет колонки сортировки " + args.sort)
            frame = frame.sort_values(args.sort, ascending=not args.descending)
        return {
            "rows": records(frame[selected].head(args.limit)),
            "total_groups": len(frame),
            "columns": selected,
            "warnings": warnings,
            "filters": self.state["filters"],
        }

    def chart_result(self, spec, allow_metric=False):
        from shared.analytics import chart_frame
        from shared.charts import render
        from shared.source import records

        df, _, _ = materialize(self.state)
        frame, effective = chart_frame(
            df, spec, self.state["filters"], self.state["calculations"], comparison_frame(self.state)
        )
        rendered = render(frame, effective)
        if not allow_metric and rendered.get("status") == "empty":
            raise ValueError(
                "Для графика недостаточно данных: "
                + rendered["notice"]
                + " Проверь фильтры или выбранный период, сохраняя смысл вопроса."
            )
        columns = list(dict.fromkeys([effective.x] + effective.y))
        return {
            "id": spec.id,
            "title": effective.title,
            "rows": records(frame[columns].head(30)),
            "groups": len(frame),
            "display": rendered.get("status", "ready"),
            "notice": rendered.get("notice", ""),
        }

    def compute_column(self, args):
        self.need_data()
        current = self.state["calculations"]
        if args.action == "undo":
            if not self.state["calculation_history"]:
                raise ValueError("Нет вычисления для отмены")
            self.state["calculations"] = self.state["calculation_history"].pop()
        else:
            import copy

            self.state["calculation_history"].append(copy.deepcopy(current))
            self.state["calculation_history"] = self.state["calculation_history"][-20:]
            if args.action == "delete":
                if not any(c["name"] == args.name for c in current):
                    raise ValueError("Вычисляемая колонка не найдена")
                if any(args.name in c["dependencies"] for c in current):
                    raise ValueError("На колонку ссылаются другие вычисления. Сначала удалите зависимости.")
                self.state["calculations"] = [c for c in current if c["name"] != args.name]
            else:
                if not args.calculation:
                    raise ValueError("Не задана формула")
                recipe = args.calculation.model_dump()
                recipe["dependencies"] = dependencies(recipe)
                exists = next((i for i, c in enumerate(current) if c["name"] == recipe["name"]), None)
                if args.action == "add" and exists is not None:
                    raise ValueError("Колонка уже существует. Используйте change.")
                if args.action == "change" and exists is None:
                    raise ValueError("Изменяемая колонка не найдена")
                if exists is not None:
                    current[exists] = recipe
                else:
                    current.append(recipe)
        df, warnings, _ = materialize(self.state)
        self.state["debug"]["warnings"] = warnings
        self.state["dataset"]["version"] += 1
        self.state["dashboard"]["table_version"] = self.state["dataset"]["version"]
        self.state["dashboard"]["version"] += 1
        return {
            "columns": self.state["calculations"],
            "table_version": self.state["dataset"]["version"],
            "warnings": warnings,
            "rows": len(df),
        }

    def create_chart(self, args):
        self.need_data()
        if len(self.state["dashboard"]["charts"]) >= 4:
            raise ValueError("Максимум 4 графика. Измените существующий.")
        from shared.charts import REGISTRY

        if args.spec.type not in REGISTRY:
            raise ValueError("Неизвестный renderer")
        if any(c["id"] == args.spec.id for c in self.state["dashboard"]["charts"]):
            raise ValueError("Идентификатор графика уже существует")
        result = self.chart_result(args.spec)
        self.state["dashboard"]["charts"].append(args.spec.model_dump())
        self.state["dashboard"]["version"] += 1
        return result

    def update_chart(self, args):
        self.need_data()
        charts = self.state["dashboard"]["charts"]
        if args.index > len(charts):
            raise ValueError("Такого графика нет")
        old = charts[args.index - 1]
        if "id" in args.changes and args.changes["id"] != old["id"]:
            raise ValueError("Идентификатор графика стабилен")
        changes = dict(args.changes)
        axes_changed = any(
            changes.get(key, old.get(key)) != old.get(key)
            for key in ["type", "source", "x", "y", "group", "sort", "descending"]
        )
        if axes_changed and "zoom" not in changes:
            changes["zoom"] = None
        spec = ChartSpec.model_validate({**old, **changes})
        if "hidden" in args.changes and all(y in spec.hidden for y in spec.y):
            raise ValueError("Хотя бы одна серия графика должна оставаться видимой")
        result = self.chart_result(spec, allow_metric=True)
        if "zoom" in args.changes:
            from shared.zoom import synchronized_updates

            for index, changed in synchronized_updates(self.state, args.index - 1, spec):
                charts[index] = changed
        charts[args.index - 1] = spec.model_dump()
        self.state["dashboard"]["version"] += 1
        return {"index": args.index, "spec": charts[args.index - 1], **result}

    def set_sync_zoom(self, args):
        self.need_data()
        self.state["dashboard"]["sync_zoom"] = args.enabled
        self.state["dashboard"]["version"] += 1
        return {"sync_zoom": args.enabled}

    def create_dashboard(self, args):
        self.need_data()
        charts = [s.model_dump() for s in args.charts] if args.charts is not None else default_charts()
        results = [self.chart_result(ChartSpec.model_validate(c), allow_metric=True) for c in charts]
        self.state["dashboard"] = DashboardSpec(
            title=args.title,
            description=args.description,
            table_id=self.state["dataset"]["id"],
            table_version=self.state["dataset"]["version"],
            charts=charts,
        ).model_dump()
        self.state["saved_id"] = None
        return {"id": self.state["dashboard"]["id"], "charts": len(charts), "results": results}

    def update_dashboard(self, args):
        self.need_data()
        dashboard = self.state["dashboard"]
        available = {c["id"]: c for c in dashboard["charts"]}
        if args.chart_ids is not None:
            if len(set(args.chart_ids)) != len(args.chart_ids):
                raise ValueError("Графики не должны повторяться")
            if set(args.chart_ids) - available.keys():
                raise ValueError("Можно переставлять только существующие графики")
        if args.layout is not None:
            dashboard["layout"] = args.layout
        if args.chart_ids is not None:
            dashboard["charts"] = [available[i] for i in args.chart_ids]
        dashboard["version"] += 1
        return {
            "id": dashboard["id"],
            "layout": dashboard["layout"],
            "chart_ids": [c["id"] for c in dashboard["charts"]],
        }

    def apply_filters(self, args):
        self.need_data()
        patch = dict(args.filters)
        if "dimension" in patch:
            for chart in self.state["dashboard"]["charts"]:
                if chart["source"] == "ranking" and chart["y"] == ["fact"]:
                    chart["y"] = ["plan", "fact"]
                if chart["type"] == "heatmap":
                    chart["group"], chart["x"] = patch["dimension"], "period"
                elif chart["source"] == "table" and chart["x"] in {"region", "outlet", "product", "client"}:
                    chart["x"] = patch["dimension"]
        if "regions" in patch and "outlets" not in patch:
            patch["outlets"] = []
        filters = Filters.model_validate({**self.state["filters"], **patch})
        if filters.clients and self.state["dataset"]["kind"] != "discounts":
            raise ValueError(
                "Клиентский разрез требует fetch_data(kind=discounts); план не распределяется на клиентов"
            )
        if filters.dimension == "client" and self.state["dataset"]["kind"] != "discounts":
            raise ValueError("Клиентский разрез недоступен в плане. Получите детализацию скидок.")
        dataset = self.state["dataset"]
        if (filters.start and filters.start < dataset["query_start"]) or (
            filters.end and filters.end > dataset["query_end"]
        ):
            raise ValueError(
                "Период вне загруженной выборки. Попросите ассистента получить данные за нужный диапазон."
            )
        self.state["filters"] = filters.model_dump()
        self.state["filter_notice"] = ""
        for chart in self.state["dashboard"]["charts"]:
            chart["zoom"] = None
        self.state["dashboard"]["version"] += 1
        df, _, _ = materialize(self.state)
        return {
            "filters": self.state["filters"],
            "kpis": dashboard_kpis(self.state, df),
            "filter_notice": self.state.get("filter_notice", ""),
        }

    def validate_result(self, args):
        self.need_data()
        view = present(self.state)
        return {
            "valid": True,
            "kpis": view["kpis"],
            "charts": len(view["charts"]),
            "summary": view["summary"],
            "warnings": view["debug"]["warnings"],
        }

    def save_dashboard(self, args, query=None):
        self.need_data()
        capture_snapshot(self.state)
        return self.store.save(self.owner, self.state, args.title, args.as_new, query=query)

    def search_dashboards(self, args):
        return self.store.list(self.owner, args.search, args.sort)

    def open_dashboard(self, args):
        revision = self.state["revision"]
        replacement = self.store.open(self.owner, args.id)
        conversation = self.state["messages"]
        actions = self.state.get("chat_actions", {})
        self.state.clear()
        self.state.update(replacement)
        self.state["messages"] = conversation
        self.state["chat_actions"] = actions
        self.state["revision"] = revision
        entry = next(d for d in self.store.list(self.owner) if d["id"] == args.id)
        self.state["workspace"] = {
            "saved_id": args.id,
            "message_id": None,
            "title": entry["title"],
            "query": entry["query"],
            "created": entry["created"],
            "updated": entry["updated"],
        }
        return {"id": args.id, "title": self.state["dashboard"]["title"]}

    def rename_dashboard(self, args):
        self.store.rename(self.owner, args.id, args.title)
        if self.state["saved_id"] == args.id:
            self.state["dashboard"]["title"] = args.title
            if self.state.get("workspace"):
                self.state["workspace"]["title"] = args.title
        return {"id": args.id, "title": args.title}

    def delete_dashboard(self, args):
        self.store.delete(self.owner, args.id)
        if self.state["saved_id"] == args.id:
            self.state["saved_id"] = None
            self.state["workspace"] = None
        for message in self.state["messages"]:
            if message.get("dashboard", {}).get("saved_id") == args.id:
                message["dashboard"].update(saved_id=None, saved=False)
        return {"id": args.id, "deleted": True}

    def export(self, args):
        self.need_data()
        return {"url": f"/api/export/{args.format}?index={args.index}", "format": args.format}
