"""Safe vector arithmetic and calendar-aware post-fetch analytics. Never eval/exec."""

import ast
import operator
import numpy as np
import pandas as pd
from shared.models import Calculation, Filters

OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda a, b: a / b.replace(0, np.nan) if isinstance(b, pd.Series) else a / b if b else np.nan,
    ast.Mod: lambda a, b: a % b.replace(0, np.nan) if isinstance(b, pd.Series) else a % b if b else np.nan,
}


def expression_columns(expression):
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise ValueError("Некорректная формула. Проверьте скобки и арифметические операции.") from None
    if len(list(ast.walk(tree))) > 80:
        raise ValueError("Формула слишком сложная")
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})


def formula(expression, df):
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        raise ValueError("Некорректная формула. Проверьте скобки и арифметические операции.") from None
    if len(list(ast.walk(tree))) > 80:
        raise ValueError("Формула слишком сложная")

    def visit(n):
        if isinstance(n, ast.Expression):
            return visit(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float) and abs(n.value) <= 1e12:
            return n.value
        if isinstance(n, ast.Name) and n.id in df.columns and pd.api.types.is_numeric_dtype(df[n.id]):
            return df[n.id]
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return OPS[type(n.op)](visit(n.left), visit(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            return -visit(n.operand) if isinstance(n.op, ast.USub) else visit(n.operand)
        raise ValueError(
            "Разрешены числовые колонки, числа, скобки и операции + − × / %. Вызовы функций запрещены."
        )

    value = visit(tree)
    if np.isscalar(value):
        value = pd.Series(value, index=df.index, dtype=float)
    return value.replace([np.inf, -np.inf], np.nan)


def filtered(base, filters: Filters, monthly=None):
    df = base.copy()
    for col, values in [
        ("region", filters.regions),
        ("outlet", filters.outlets),
        ("product", filters.products),
        ("client", filters.clients),
    ]:
        if values:
            if col not in df:
                raise ValueError(f"Нет поля {col}")
            df = df[df[col].isin(values)]
    dates = pd.to_datetime(df["date"])
    # Monthly source: inclusive month boundaries; no fake daily plan allocation.
    monthly = bool(len(df) and (dates.dt.day == 1).all()) if monthly is None else monthly
    if filters.start:
        start = pd.Timestamp(filters.start).replace(day=1) if monthly else pd.Timestamp(filters.start)
        df = df[dates >= start]
        dates = dates.loc[df.index]
    if filters.end:
        end = pd.Timestamp(filters.end).to_period("M").end_time if monthly else pd.Timestamp(filters.end)
        df = df[dates <= end]
    if filters.min_amount is not None:
        df = df[df.fact >= filters.min_amount]
    if filters.max_amount is not None:
        df = df[df.fact <= filters.max_amount]
    return df.reset_index(drop=True)


def apply_calculations(base, recipes):
    df = base.copy()
    warnings = []
    for recipe in recipes:
        r = Calculation.model_validate(recipe)
        if r.name in base.columns:
            raise ValueError(f"Вычисление {r.name} заменяет исходную колонку")
        if r.name in r.dependencies:
            raise ValueError("Циклическая зависимость формулы")
        if r.kind == "formula":
            df[r.name] = formula(r.expression, df)
        else:
            if r.column not in df or not pd.api.types.is_numeric_dtype(df[r.column]):
                raise ValueError(f"Неизвестная числовая колонка {r.column}")
            if any(g not in df for g in r.group_by):
                raise ValueError("Неизвестная группировка")
            col = r.column
            groups = r.group_by
            if r.kind == "share":
                denom = df[col].sum(min_count=1)
                numerator = df.groupby(groups)[col].transform("sum") if groups else df[col]
                df[r.name] = numerator / denom * 100 if pd.notna(denom) and denom else np.nan
            elif r.kind in {"mean", "median", "min", "max", "rank"}:
                if groups and r.kind == "rank":
                    agg = (
                        df.groupby(groups, dropna=False)[col]
                        .sum(min_count=1)
                        .rank(ascending=False, method="dense")
                    )
                    mapping = agg.rename(r.name).reset_index()
                    df = df.merge(mapping, on=groups, how="left", validate="many_to_one")
                elif groups:
                    df[r.name] = df.groupby(groups)[col].transform(r.kind)
                else:
                    df[r.name] = (
                        df[col].rank(ascending=False, method="dense")
                        if r.kind == "rank"
                        else getattr(df[col], r.kind)()
                    )
            else:
                df["_month"] = pd.to_datetime(df.date).dt.to_period("M").astype(str)
                keys = [*groups, "_month"]
                agg = df.groupby(keys, dropna=False, as_index=False)[col].sum(min_count=1).sort_values(keys)
                if r.kind == "cumulative":
                    agg[r.name] = agg.groupby(groups)[col].cumsum() if groups else agg[col].cumsum()
                else:
                    prior = agg[keys + [col]].copy()
                    prior["_month"] = (
                        pd.PeriodIndex(prior["_month"], freq="M") + (12 if r.kind == "yoy" else 1)
                    ).astype(str)
                    prior = prior.rename(columns={col: "_previous"})
                    agg = agg.merge(prior, on=keys, how="left", validate="one_to_one")
                    agg[r.name] = (agg[col] - agg["_previous"]) / agg["_previous"].replace(0, np.nan) * 100
                df = df.merge(agg[keys + [r.name]], on=keys, how="left", validate="many_to_one").drop(
                    columns="_month"
                )
        missing = int(df[r.name].isna().sum())
        if missing:
            warnings.append(
                f"{r.label}: {missing} значений не определены (пропуск, нулевой знаменатель или отсутствующий календарный период)."
            )
    return df, warnings


def dependencies(recipe):
    r = Calculation.model_validate(recipe)
    return expression_columns(r.expression) if r.kind == "formula" else [r.column, *r.group_by]


def aggregate(df, key, metric="amount"):
    if df.empty:
        return pd.DataFrame(
            {
                **{name: pd.Series(dtype=str) for name in (key if isinstance(key, list) else [key])},
                **{
                    x: pd.Series(dtype=float)
                    for x in [
                        "fact",
                        "plan",
                        "discount",
                        "quantity",
                        "deals",
                        "completion",
                        "deviation",
                        "growth",
                    ]
                },
            }
        )
    x = df.copy()
    if metric == "quantity":
        units = x["unit"].dropna().unique() if "unit" in x else []
        if len(units) > 1:
            raise ValueError("Количество имеет разные единицы измерения. Выберите продукты с одной единицей.")
        x["fact"] = x.quantity
        x["plan"] = x.plan_quantity
    elif metric in {"discount", "deals"}:
        x["fact"] = x[metric]
        x["plan"] = np.nan
    keys = key if isinstance(key, list) else [key]
    nums = [column for column in x.select_dtypes(include="number").columns if column not in keys]
    out = x.groupby(key, dropna=False)[list(nums)].sum(min_count=1).reset_index()
    out["completion"] = out.fact / out.plan.replace(0, np.nan) * 100
    out["deviation"] = out.fact - out.plan
    out["growth"] = (out.fact - out.fact.shift(1)) / out.fact.shift(1).replace(0, np.nan) * 100
    if metric == "completion":
        out["fact"] = out.completion
        out["plan"] = np.where(out.completion.notna(), 100.0, np.nan)
        out["deviation"] = out.fact - out.plan
    return out
