"""General source-table analysis in pandas, using structured operations."""

from typing import Any, Literal
import pandas as pd
from pydantic import Field
from shared.models import Strict, Calculation
from shared.source import records
from shared.calculations import formula, apply_calculations


class Condition(Strict):
    column: str
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in"] = "eq"
    value: Any


class ReadRecords(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    table: Literal[
        "clients", "contracts", "sales", "sales_plan", "discounts", "regions", "outlets", "products"
    ]
    columns: list[str] = Field(default_factory=list, max_length=40)
    conditions: list[Condition] = Field(default_factory=list, max_length=10)


class Measure(Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: Literal["count", "nunique", "sum", "mean", "median", "min", "max", "first", "last"]
    column: str = ""


class AnalyzeRecords(Strict):
    source: str
    name: str = Field("", pattern=r"^$|^[a-z][a-z0-9_]{0,39}$")
    group_by: list[str] = Field(default_factory=list, max_length=4)
    date_column: str = ""
    bucket: Literal["day", "week", "month", "quarter", "year"] = "month"
    measures: list[Measure] = Field(min_length=1, max_length=12)
    conditions: list[Condition] = Field(default_factory=list, max_length=10)
    calendar_start: str | None = None
    calendar_end: str | None = None
    expressions: dict[str, str] = Field(default_factory=dict, max_length=10)
    calculations: list[Calculation] = Field(default_factory=list, max_length=6)
    sort: str = ""
    descending: bool = False
    limit: int = Field(40, ge=1, le=200)


class JoinRecords(Strict):
    left: str
    right: str
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    left_on: list[str] = Field(min_length=1, max_length=4)
    right_on: list[str] = Field(min_length=1, max_length=4)
    how: Literal["left", "inner"] = "left"
    cardinality: Literal["one_to_one", "many_to_one", "one_to_many"] = "many_to_one"


def subset(frame, conditions):
    for condition in conditions:
        column, operation, value = condition.column, condition.operator, condition.value
        if column not in frame:
            raise ValueError("Неизвестная колонка " + column)
        series = frame[column]
        if operation == "in":
            if not isinstance(value, list):
                raise ValueError("Для in нужен список")
            mask = series.isin(value)
        elif value is None and operation in {"eq", "ne"}:
            mask = series.isna() if operation == "eq" else series.notna()
        else:
            mask = {
                "eq": series.eq,
                "ne": series.ne,
                "lt": series.lt,
                "lte": series.le,
                "gt": series.gt,
                "gte": series.ge,
            }[operation](value)
        frame = frame.loc[mask]
    return frame.copy()


class RecordOperations:
    def put_records(self, name, data):
        sets = self.state.setdefault("record_sets", {})
        if name not in sets and len(sets) >= 8:
            raise ValueError("Уже загружено 8 наборов. Переиспользуйте имя набора.")
        if len(data["rows"]) > self.settings.row_limit:
            raise ValueError("Результат превышает лимит строк")
        sets[name] = data
        frame = pd.DataFrame(data["rows"], columns=data["columns"])
        date_ranges = {}
        for column in frame:
            if column.endswith(("_date", "_month")) or column == "date":
                dates = pd.to_datetime(frame[column], errors="coerce").dropna()
                if len(dates):
                    date_ranges[column] = [dates.min().date().isoformat(), dates.max().date().isoformat()]
        return {
            "name": name,
            "source_table": data.get("source_table"),
            "columns": data["columns"],
            "types": data.get("types", {}),
            "rows": len(frame),
            "date_ranges": date_ranges,
            "preview": data["rows"][:5],
        }

    def record_frame(self, name):
        data = self.state.get("record_sets", {}).get(name)
        if not data:
            raise ValueError(
                "Нет набора " + name + ". Используй fetch_records или имя уже загруженного набора."
            )
        return pd.DataFrame(data["rows"], columns=data["columns"])

    def fetch_records(self, args):
        return self.put_records(
            args.name,
            self.source.load_records(args.table, args.columns, [c.model_dump() for c in args.conditions]),
        )

    def join_records(self, args):
        left, right = self.record_frame(args.left), self.record_frame(args.right)
        if not set(args.left_on) <= set(left) or not set(args.right_on) <= set(right):
            raise ValueError("Колонок соединения нет в наборах")
        frame = left.merge(
            right,
            how=args.how,
            left_on=args.left_on,
            right_on=args.right_on,
            validate=args.cardinality,
            suffixes=("", "_right"),
        )
        return self.put_records(
            args.name,
            {
                "rows": records(frame),
                "columns": frame.columns.tolist(),
                "source_table": f"{args.left}+{args.right}",
            },
        )

    def analyze_records(self, args):
        frame = subset(self.record_frame(args.source), args.conditions)
        groups = list(args.group_by)
        if args.date_column:
            if args.date_column not in frame:
                raise ValueError("Нет поля даты " + args.date_column)
            period = pd.to_datetime(frame[args.date_column], errors="coerce").dt.to_period(
                {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}[args.bucket]
            )
            frame["date"] = period.dt.start_time.dt.strftime("%Y-%m-%d")
            if "date" not in groups:
                groups.insert(0, "date")
        if not set(groups) <= set(frame):
            raise ValueError("Нет полей группировки: " + ", ".join(sorted(set(groups) - set(frame))))
        for measure in args.measures:
            if measure.operation != "count" and measure.column not in frame:
                raise ValueError("Нет поля меры " + measure.column)
        if len({m.name for m in args.measures}) != len(args.measures) or any(
            m.name in groups for m in args.measures
        ):
            raise ValueError("Имена мер должны быть уникальны и отличаться от полей группировки")

        def calculate(group):
            result = {}
            for measure in args.measures:
                if measure.operation == "count":
                    value = len(group) if not measure.column else group[measure.column].count()
                else:
                    column = group[measure.column]
                    if measure.operation in {"sum", "mean", "median"}:
                        column = pd.to_numeric(column, errors="raise")
                    if measure.operation in {"first", "last"}:
                        value = (
                            column.iloc[0 if measure.operation == "first" else -1] if len(column) else None
                        )
                    elif measure.operation == "sum":
                        value = column.sum(min_count=1)
                    else:
                        value = getattr(column, measure.operation)()
                result[measure.name] = value
            return result

        if groups:
            output = []
            for keys, group in frame.groupby(groups, dropna=False, sort=True):
                output.append(
                    {**dict(zip(groups, keys if isinstance(keys, tuple) else (keys,))), **calculate(group)}
                )
            out = pd.DataFrame(output, columns=groups + [m.name for m in args.measures])
        else:
            out = pd.DataFrame([calculate(frame)])
        if args.calendar_start or args.calendar_end:
            if groups != ["date"] or not args.calendar_start or not args.calendar_end:
                raise ValueError("Полный календарь требует обе границы и только группировку по date")
            if args.calendar_start > args.calendar_end:
                raise ValueError("Начало календаря позже конца")
            frequency = {"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}[args.bucket]
            calendar = pd.period_range(args.calendar_start, args.calendar_end, freq=frequency)
            out = (
                out.set_index("date")
                .reindex(calendar.start_time.strftime("%Y-%m-%d"))
                .rename_axis("date")
                .reset_index()
            )
            for measure in args.measures:
                if measure.operation in {"count", "nunique", "sum"}:
                    out[measure.name] = out[measure.name].fillna(0)
        if "date" in groups:
            out = out.sort_values("date")
            dates = pd.to_datetime(out["date"])
            out["calendar_days"] = {
                "day": lambda: 1,
                "week": lambda: 7,
                "month": lambda: dates.dt.days_in_month,
                "quarter": lambda: (
                    (dates.dt.to_period("Q").dt.end_time - dates.dt.to_period("Q").dt.start_time).dt.days + 1
                ),
                "year": lambda: 365 + dates.dt.is_leap_year.astype(int),
            }[args.bucket]()
        for name, expression in args.expressions.items():
            if name in out:
                raise ValueError("Имя выражения занято: " + name)
            out[name] = formula(expression, out)
        if args.calculations:
            out, _ = apply_calculations(out, [c.model_dump() for c in args.calculations])
        if args.sort:
            if args.sort not in out:
                raise ValueError("Нет колонки сортировки")
            out = out.sort_values(args.sort, ascending=not args.descending)
        if args.name:
            self.put_records(
                args.name,
                {
                    "rows": records(out),
                    "columns": out.columns.tolist(),
                    "source_table": args.source + " (analysis)",
                },
            )
        stats = {}
        for column in out.select_dtypes(include="number"):
            series = out[column]
            stats[column] = {
                "count": int(series.count()),
                "sum": series.sum(min_count=1),
                "mean": series.mean(),
                "min": series.min(),
                "max": series.max(),
            }
        # Normalize numpy values and nulls through the same JSON conversion as source tables.
        normalized_stats = {name: records(pd.DataFrame([values]))[0] for name, values in stats.items()}
        return {
            "name": args.name or None,
            "columns": out.columns.tolist(),
            "rows": records(out.head(args.limit)),
            "total_rows": len(out),
            "statistics": normalized_stats,
        }
