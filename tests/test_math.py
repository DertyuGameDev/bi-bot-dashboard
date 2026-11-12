import numpy as np
import pandas as pd
import pytest
from shared.calculations import formula, apply_calculations, filtered, aggregate
from shared.models import Calculation, Filters
from shared.analytics import kpis


def test_weighted_completion_not_mean():
    df = pd.DataFrame(
        {
            "fact": [90.0, 10.0],
            "plan": [100.0, 1000.0],
            "outlet": ["A", "B"],
            "product": ["x", "x"],
            "unit": ["шт", "шт"],
        }
    )
    result = kpis(df, Filters().model_dump())
    assert result["completion"] == pytest.approx(100 / 1100 * 100)
    assert result["completion"] != pytest.approx(45.5)


def test_zero_plan_and_missing():
    df = pd.DataFrame({"fact": [10.0, 20.0, np.nan], "plan": [0.0, None, 10.0]})
    out = formula("(fact - plan) / plan * 100", df)
    assert out.isna().all()
    assert formula("fact - plan", df).iloc[0] == 10


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('true')",
        "fact.__class__",
        "fact[0]",
        "[x for x in fact]",
        "2 ** 2000",
        "unknown + 1",
    ],
)
def test_unsafe_formulas_rejected(expr):
    with pytest.raises(ValueError):
        formula(expr, pd.DataFrame({"fact": [1.0]}))


def test_calendar_gap_mom_not_previous_available_row():
    df = pd.DataFrame(
        {
            "date": ["2025-01-01", "2025-03-01", "2025-04-01"],
            "fact": [100.0, 150.0, 300.0],
            "region": ["A"] * 3,
        }
    )
    recipe = Calculation(name="mom", label="Динамика", kind="mom", group_by=["region"]).model_dump()
    out, warnings = apply_calculations(df, [recipe])
    assert out.mom.isna().iloc[:2].all()
    assert out.mom.iloc[2] == 100
    assert warnings


def test_yoy_exact_calendar_month():
    df = pd.DataFrame({"date": ["2024-01-01", "2025-01-01", "2025-02-01"], "fact": [100.0, 125.0, 200.0]})
    out, _ = apply_calculations(df, [Calculation(name="yoy", label="Год к году", kind="yoy").model_dump()])
    assert out.yoy.iloc[1] == 25
    assert pd.isna(out.yoy.iloc[2])


def test_filtered_share_uses_visible_base():
    df = pd.DataFrame(
        {
            "date": ["2025-01-01"] * 3,
            "region": ["A", "A", "B"],
            "outlet": ["a", "b", "c"],
            "fact": [10.0, 30.0, 60.0],
        }
    )
    subset = filtered(df, Filters(regions=["A"]))
    out, _ = apply_calculations(subset, [Calculation(name="share", label="Доля", kind="share").model_dump()])
    assert out.share.tolist() == [25.0, 75.0]


@pytest.mark.parametrize("kind,expected", [("mean", 20.0), ("median", 20.0), ("min", 10.0), ("max", 30.0)])
def test_group_statistics(kind, expected):
    df = pd.DataFrame({"fact": [10.0, 30.0, 100.0], "region": ["A", "A", "B"]})
    out, _ = apply_calculations(
        df, [Calculation(name="calc", label="Статистика", kind=kind, group_by=["region"]).model_dump()]
    )
    assert out.calc.iloc[0] == expected


def test_cumulative_groups_and_dense_rank():
    df = pd.DataFrame(
        {
            "date": ["2025-02-01", "2025-01-01", "2025-01-01"],
            "fact": [20.0, 10.0, 40.0],
            "region": ["A", "A", "B"],
        }
    )
    out, _ = apply_calculations(
        df,
        [
            Calculation(name="total", label="Итог", kind="cumulative", group_by=["region"]).model_dump(),
            Calculation(name="ranking", label="Ранг", kind="rank", group_by=["region"]).model_dump(),
        ],
    )
    assert out.total.tolist() == [30.0, 10.0, 40.0]
    assert out.ranking.tolist() == [2.0, 2.0, 1.0]


def test_dependencies_and_missing_column():
    df = pd.DataFrame({"fact": [20.0], "plan": [10.0]})
    recipes = [
        Calculation(name="delta", label="Отклонение", expression="fact - plan").model_dump(),
        Calculation(name="pct", label="Процент", expression="delta / plan * 100").model_dump(),
    ]
    out, _ = apply_calculations(df, recipes)
    assert out.pct.iloc[0] == 100
    with pytest.raises(ValueError):
        apply_calculations(df, list(reversed(recipes)))


def test_cannot_sum_incompatible_units():
    df = pd.DataFrame(
        {
            "fact": [1.0, 2.0],
            "plan": [1.0, 2.0],
            "quantity": [1.0, 2.0],
            "plan_quantity": [1.0, 2.0],
            "unit": ["кг", "шт"],
            "region": ["A", "B"],
        }
    )
    with pytest.raises(ValueError):
        aggregate(df, "region", "quantity")


def test_chart_custom_percentage_recomputed_from_grouped_sums():
    from shared.analytics import chart_frame
    from shared.models import ChartSpec

    df = pd.DataFrame(
        {
            "date": ["2025-01-01"] * 2,
            "region": ["A", "A"],
            "outlet": ["a", "b"],
            "product": ["x", "x"],
            "category": ["x", "x"],
            "unit": ["шт", "шт"],
            "fact": [90.0, 10.0],
            "plan": [100.0, 1000.0],
            "quantity": [1.0, 2.0],
            "plan_quantity": [2.0, 3.0],
            "discount": [0.0, 0.0],
            "deals": [1.0, 1.0],
        }
    )
    recipe = Calculation(name="custom_ratio", label="Процент", expression="fact / plan * 100").model_dump()
    recipe["dependencies"] = ["fact", "plan"]
    calculated, _ = apply_calculations(df, [recipe])
    out, spec = chart_frame(
        calculated,
        ChartSpec(source="ranking", x="region", y=["custom_ratio"]),
        Filters().model_dump(),
        [recipe],
    )
    assert out.custom_ratio.iloc[0] == pytest.approx(100 / 1100 * 100)


def test_invalid_formula_syntax_is_user_error():
    with pytest.raises(ValueError, match="Некорректная формула"):
        formula("fact +", pd.DataFrame({"fact": [1.0]}))


def test_outlet_month_heatmap_aggregates_products_and_recovers_legacy_axis():
    import pandas as pd
    from shared.analytics import chart_frame
    from shared.models import ChartSpec, Filters

    frame = pd.DataFrame(
        {
            "date": ["2025-01-01", "2025-01-01", "2025-02-01", "2025-01-01", "2025-02-01"],
            "outlet": ["A", "A", "A", "B", "B"],
            "fact": [10.0, 20.0, 40.0, 50.0, 60.0],
            "plan": [20.0, 30.0, 50.0, 60.0, 70.0],
        }
    )
    data, spec = chart_frame(
        frame, ChartSpec(type="heatmap", source="table", x="outlet", y=["fact"]), Filters().model_dump()
    )
    assert spec.group == "outlet" and spec.x == "period"
    assert len(data) == 4 and data.fact.sum() == frame.fact.sum()
    assert data.set_index(["outlet", "period"]).loc[("A", "2025-01"), "fact"] == 30


def test_empty_heatmap_preserves_both_axis_columns():
    import pandas as pd
    from shared.analytics import chart_frame
    from shared.models import ChartSpec, Filters
    from shared.charts import render

    frame = pd.DataFrame(
        {
            "date": pd.Series(dtype=str),
            "outlet": pd.Series(dtype=str),
            "fact": pd.Series(dtype=float),
            "plan": pd.Series(dtype=float),
        }
    )
    data, spec = chart_frame(
        frame, ChartSpec(type="heatmap", source="table", x="outlet", y=["fact"]), Filters().model_dump()
    )
    assert {"outlet", "period", "fact"} <= set(data)
    assert render(data, spec)["status"] == "empty"


def test_heatmap_top_rows_are_explicitly_disclosed():
    import pandas as pd
    from shared.analytics import chart_frame
    from shared.models import ChartSpec, Filters

    frame = pd.DataFrame(
        [
            {"date": month, "outlet": str(outlet), "fact": float(outlet + 1), "plan": 100.0}
            for month in ["2025-01-01", "2025-02-01"]
            for outlet in range(12)
        ]
    )
    data, spec = chart_frame(
        frame,
        ChartSpec(type="heatmap", source="table", x="period", group="outlet", y=["fact"]),
        Filters(top_n=5).model_dump(),
    )
    assert data.outlet.nunique() == 5 and set(data.outlet) == {"7", "8", "9", "10", "11"}
    assert "5 из 12" in data.attrs["notice"]
