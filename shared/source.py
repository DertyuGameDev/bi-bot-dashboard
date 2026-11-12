"""Only fixed, parameterized read queries. No model SQL reaches this module."""

import time
from contextlib import contextmanager
from datetime import date
import pandas as pd
import numpy as np
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from shared.config import Settings
from shared.client_activity import encode_activity

DATE_FIELDS = {"sales": "sale_date", "sales_plan": "plan_month", "discounts": "discount_date"}
DIMENSIONS = {
    "regions": "SELECT region_id, region_name, regional_center FROM regions",
    "outlets": "SELECT outlet_id, outlet_name, region_id, city, outlet_type, opened_date, closed_date FROM outlets",
    "products": "SELECT product_id, product_name, category, unit_price, unit, launch_date, discontinued_date FROM products",
    "clients": "SELECT client_id, client_name, segment, region_id, is_key_client FROM clients",
    "contracts": "SELECT contract_id, contract_number, client_id, start_date, end_date, status FROM contracts",
}
FACTS = {
    "sales": "SELECT sale_id, sale_date, contract_id, client_id, outlet_id, product_id, quantity, amount FROM sales WHERE sale_date >= %s AND sale_date <= %s ORDER BY sale_id LIMIT %s",
    "sales_plan": "SELECT plan_id, plan_month, outlet_id, product_id, plan_amount, plan_qty FROM sales_plan WHERE plan_month >= %s AND plan_month <= %s ORDER BY plan_id LIMIT %s",
    "discounts": "SELECT discount_id, sale_id, client_id, discount_date, discount_amount, discount_pct, reason FROM discounts WHERE discount_date >= %s AND discount_date <= %s ORDER BY discount_id LIMIT %s",
}


class SourceError(ValueError):
    pass


class Source:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.last_queries = []

    @contextmanager
    def connect(self):
        try:
            with psycopg.connect(self.settings.database_url, connect_timeout=5, row_factory=dict_row) as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute(
                    "SELECT set_config('statement_timeout', %s, true)", (str(self.settings.query_timeout_ms),)
                )
                yield conn
        except psycopg.Error as e:
            # Driver exceptions may contain passwords/hosts. Expose only class, no DSN.
            raise SourceError(
                f"БД недоступна или запрос отклонён ({type(e).__name__}). Проверьте подключение; последнее состояние сохранено."
            ) from None

    def schema(self):
        if self.settings.source == "demo":
            return {
                "mode": "demo",
                "warning": "Синтетические данные",
                "tables": ["synthetic_monthly_sales"],
                "period": ["2024-01-01", "2025-12-31"],
            }
        with self.connect() as c:
            columns = c.execute(
                "SELECT table_name, column_name, data_type FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name, ordinal_position"
            ).fetchall()
            keys = c.execute(
                "SELECT t.relname AS table_name, a.attname AS column_name, ft.relname AS target_table, b.attname AS target_column FROM pg_constraint con JOIN pg_class t ON t.oid=con.conrelid JOIN pg_namespace ns ON ns.oid=t.relnamespace JOIN pg_class ft ON ft.oid=con.confrelid JOIN LATERAL unnest(con.conkey, con.confkey) AS k(attnum, foreign_attnum) ON true JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum JOIN pg_attribute b ON b.attrelid=ft.oid AND b.attnum=k.foreign_attnum WHERE con.contype='f' AND ns.nspname='public' ORDER BY t.relname, a.attname"
            ).fetchall()
        return {
            "mode": "postgres",
            "tables": columns,
            "relations": keys,
            "units": "amount/plan_amount/discount_amount: RUB; quantity/plan_qty: product.unit",
            "grain": "sales=sale_id; plan=month,outlet_id,product_id; discounts=discount_id",
        }

    def load_records(self, table, columns, conditions):
        """Read existing fields, with bound values. No model SQL or SQL aggregates."""
        if table not in {*DIMENSIONS, *FACTS}:
            raise SourceError("Неизвестная исходная таблица")
        if self.settings.source == "demo":
            raise SourceError(
                "Сырые таблицы доступны в режиме PostgreSQL; демонстрационная таблица синтетическая"
            )
        operators = {"eq": "=", "ne": "<>", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}
        with self.connect() as connection:
            fields = connection.execute(
                "SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                (table,),
            ).fetchall()
            names = [f["column_name"] for f in fields]
            selected = columns or names
            if not selected or not set(selected) <= set(names):
                raise SourceError(
                    "Полей нет в фактической схеме: " + ", ".join(sorted(set(selected) - set(names)))
                )
            clauses, values = [], []
            for condition in conditions:
                column, operator, value = condition["column"], condition["operator"], condition["value"]
                if column not in names:
                    raise SourceError("Неизвестная колонка фильтра " + column)
                if value is None and operator in {"eq", "ne"}:
                    clauses.append(
                        sql.SQL("{} IS " + ("NOT NULL" if operator == "ne" else "NULL")).format(
                            sql.Identifier(column)
                        )
                    )
                    continue
                if operator == "in":
                    if not isinstance(value, list):
                        raise SourceError("Для in нужен список значений")
                    clauses.append(sql.SQL("{} = ANY(%s)").format(sql.Identifier(column)))
                elif operator in operators:
                    clauses.append(
                        sql.SQL("{} " + operators[operator] + " %s").format(sql.Identifier(column))
                    )
                else:
                    raise SourceError("Недопустимый оператор фильтра")
                values.append(value)
            where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("")
            query = sql.SQL("SELECT {} FROM {}{}").format(
                sql.SQL(", ").join(map(sql.Identifier, selected)), sql.Identifier(table), where
            ) + sql.SQL(" ORDER BY {} LIMIT %s").format(sql.Identifier(names[0]))
            cursor = connection.execute(query, (*values, self.settings.row_limit + 1))
            rows = cursor.fetchall()
            if len(rows) > self.settings.row_limit:
                raise SourceError(
                    "Выборка превышает лимит; сузьте условия. Частичный результат не возвращён."
                )
        frame = pd.DataFrame(rows, columns=selected)
        for field in fields:
            if field["column_name"] in frame and field["data_type"] in {
                "numeric",
                "double precision",
                "real",
                "integer",
                "bigint",
                "smallint",
            }:
                frame[field["column_name"]] = pd.to_numeric(frame[field["column_name"]], errors="coerce")
            elif field["column_name"] in frame and field["data_type"] == "date":
                frame[field["column_name"]] = frame[field["column_name"]].map(
                    lambda x: x.isoformat() if x else None
                )
        return {
            "rows": records(frame),
            "columns": selected,
            "types": {f["column_name"]: f["data_type"] for f in fields if f["column_name"] in selected},
            "source_table": table,
            "conditions": conditions,
        }

    def available_period(self, kind="sales"):
        """Read the real date column; find bounds in pandas, without SQL analytics."""
        if self.settings.source == "demo":
            return self.schema()["period"]
        column = DATE_FIELDS[kind]
        data = self.load_records(kind, [column], [])
        dates = pd.to_datetime(
            pd.Series([row[column] for row in data["rows"]], dtype=object), errors="coerce"
        ).dropna()
        if dates.empty:
            raise SourceError("В источнике нет записей за доступную историю")
        return [dates.min().date().isoformat(), dates.max().date().isoformat()]

    def load(self, start: str = "2023-01-01", end: str = "2026-12-31", kind: str = "sales"):
        date.fromisoformat(start)
        date.fromisoformat(end)
        if start > end:
            raise SourceError("Некорректный период")
        if kind == "sales":
            start = str(pd.Timestamp(start).to_period("M").start_time.date())
            end = str(pd.Timestamp(end).to_period("M").end_time.date())
        if self.settings.source == "demo":
            return self.demo(start, end)
        began = time.perf_counter()
        queries = []
        frames = {}
        with self.connect() as c:
            for name, query in DIMENSIONS.items():
                rows = c.execute(query).fetchmany(self.settings.row_limit + 1)
                if len(rows) > self.settings.row_limit:
                    raise SourceError(
                        f"Справочник {name} превышает лимит. Увеличьте RPA_ROW_LIMIT административно."
                    )
                frames[name] = (
                    pd.DataFrame(rows, columns=[x.name for x in c.description])
                    if hasattr(c, "description")
                    else pd.DataFrame(rows)
                )
                queries.append({"sql": query, "parameters": [], "rows": len(rows)})
            for name, query in FACTS.items():
                cursor = c.execute(query, (start, end, self.settings.row_limit + 1))
                rows = cursor.fetchall()
                if len(rows) > self.settings.row_limit:
                    raise SourceError(
                        f"Выборка {name} превышает лимит {self.settings.row_limit}. Результат не показан как полный; сузьте период."
                    )
                frames[name] = pd.DataFrame(rows, columns=[x.name for x in cursor.description])
                queries.append(
                    {"sql": query, "parameters": [start, end, self.settings.row_limit + 1], "rows": len(rows)}
                )
        table, warnings, quality = flatten(frames, kind)
        return {
            "rows": records(table),
            "client_activity": encode_activity(frames["sales"]) if kind == "sales" else None,
            "columns": table.columns.tolist(),
            "source": "PostgreSQL · тестовые данные Puzzle RPA",
            "kind": kind,
            "query_start": start,
            "query_end": end,
            "observed_end": str(frames["sales"]["sale_date"].max()) if len(frames["sales"]) else end,
            "queries": queries,
            "quality": quality,
            "warnings": warnings,
            "fetch_ms": round((time.perf_counter() - began) * 1000, 1),
            "grain": "month × outlet × product" if kind == "sales" else "discount_id × sale_id",
        }

    def demo(self, start, end):
        rows = []
        rng = np.random.default_rng(42)
        for month in pd.date_range("2024-01-01", "2025-12-01", freq="MS"):
            for i, region in enumerate(["Центральный", "Северо-Западный", "Уральский"]):
                for product in ["Напитки", "Бакалея", "Молочные продукты"]:
                    plan = float(rng.integers(300000, 900000))
                    fact = round(plan * float(rng.uniform(0.7, 1.2)), 2)
                    rows.append(
                        {
                            "date": str(month.date()),
                            "region": region,
                            "outlet": f"Демо-точка {i + 1}",
                            "product": product,
                            "category": product,
                            "unit": "шт",
                            "fact": fact,
                            "plan": plan,
                            "quantity": float(rng.integers(1000, 5000)),
                            "plan_quantity": 5000.0,
                            "discount": round(fact * 0.03, 2),
                            "deals": 10.0,
                            "outlet_id": i + 1,
                            "product_id": product,
                            "client": "Не распределён",
                        }
                    )
        df = pd.DataFrame(rows)
        df = df[(df.date >= start) & (df.date <= end)]
        return {
            "rows": records(df),
            "columns": df.columns.tolist(),
            "source": "ДЕМОНСТРАЦИЯ · синтетические данные",
            "kind": "sales",
            "query_start": start,
            "query_end": end,
            "observed_end": "2025-12-31",
            "queries": [],
            "quality": {},
            "warnings": ["Синтетические данные. Подключение реальной БД в этом режиме не проверяется."],
            "fetch_ms": 0,
            "grain": "month × outlet × product",
        }


def records(df):
    # pandas JSON converts NaN/Infinity/NaT to null and normalizes numpy/decimal.
    import json

    return json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))


def flatten(frames, kind="sales"):
    f = {k: v.copy() for k, v in frames.items()}
    for name, frame in f.items():
        id_col = {"sales_plan": "plan_id"}.get(name, name[:-1] + "_id")
        if name == "sales":
            id_col = "sale_id"
        if name == "discounts":
            id_col = "discount_id"
        if id_col in frame and frame[id_col].duplicated().any():
            raise SourceError(f"Повторяющийся первичный ключ в {name}; соединение остановлено")
    sales, plans, discounts = f["sales"], f["sales_plan"], f["discounts"]
    for frame, cols in [
        (sales, ["amount", "quantity"]),
        (plans, ["plan_amount", "plan_qty"]),
        (discounts, ["discount_amount", "discount_pct"]),
    ]:
        for col in cols:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    warnings = []
    quality = {
        "raw_sales_rows": len(sales),
        "raw_plan_rows": len(plans),
        "raw_discount_rows": len(discounts),
        "raw_fact_sum": float(sales.amount.sum()),
        "raw_plan_sum": float(plans.plan_amount.sum()),
        "missing_amounts": int(sales.amount.isna().sum() + plans.plan_amount.isna().sum()),
    }
    dims = f["outlets"].merge(
        f["regions"][["region_id", "region_name"]], on="region_id", validate="many_to_one"
    )
    if kind == "discounts":
        table = discounts.merge(
            sales.drop(columns=["client_id"]),
            on="sale_id",
            how="left",
            validate="many_to_one",
            indicator=True,
        )
        if (table["_merge"] != "both").any():
            raise SourceError(
                "Скидки ссылаются на отсутствующие продажи. Расширьте период или проверьте целостность источника."
            )
        table = table.merge(
            f["clients"][["client_id", "client_name"]], on="client_id", validate="many_to_one"
        )
        table["date"] = pd.to_datetime(table.discount_date).dt.strftime("%Y-%m-%d")
        # One sale can have multiple discounts; fact is not duplicated across discount rows.
        table["fact"] = table["amount"].where(~table.sale_id.duplicated(), 0)
        table["quantity"] = table["quantity"].where(~table.sale_id.duplicated(), 0)
        table["deals"] = (~table.sale_id.duplicated()).astype(float)
        table["discount"] = table.discount_amount
        table["client"] = table.client_name
        table["plan"] = np.nan
        table["plan_quantity"] = np.nan
        warnings.append(
            "Клиентский разрез: план не распределён по клиентам; KPI выполнения недоступен. Факт сделки учитывается один раз, скидки суммируются отдельно."
        )
    else:
        sales["date"] = pd.to_datetime(sales.sale_date).dt.to_period("M").astype(str) + "-01"
        disc = discounts.groupby("sale_id", as_index=False).discount_amount.sum(min_count=1)
        sales = sales.merge(disc, on="sale_id", how="left", validate="one_to_one")
        sales["discount_amount"] = sales.discount_amount.fillna(0)
        sales["deals"] = 1.0
        keys = ["date", "outlet_id", "product_id"]
        actual = sales.groupby(keys, as_index=False).agg(
            fact=("amount", lambda s: s.sum(min_count=1)),
            quantity=("quantity", lambda s: s.sum(min_count=1)),
            discount=("discount_amount", "sum"),
            deals=("deals", "sum"),
        )
        plans["date"] = pd.to_datetime(plans.plan_month).dt.strftime("%Y-%m-%d")
        quality["duplicate_plan_grain"] = int(plans.duplicated(keys).sum())
        if quality["duplicate_plan_grain"]:
            raise SourceError(
                "План содержит повтор месяца × точки × продукта; нужна методика суммирования. Соединение остановлено."
            )
        plan = plans.rename(columns={"plan_amount": "plan", "plan_qty": "plan_quantity"})[
            keys + ["plan", "plan_quantity"]
        ]
        table = actual.merge(plan, on=keys, how="outer", validate="one_to_one")
        for col in ["fact", "quantity", "discount", "deals"]:
            table[col] = table[col].fillna(0)
        if not np.isclose(table.fact.sum(), sales.amount.sum()) or not np.isclose(
            table.plan.sum(), plans.plan_amount.sum()
        ):
            raise SourceError("Суммы изменились при соединении — загрузка отменена")
        observed = pd.to_datetime(sales["sale_date"]).max()
        if pd.notna(observed):
            future = pd.to_datetime(table["date"]) > observed.to_period("M").to_timestamp()
            for col in ["fact", "quantity", "discount", "deals"]:
                table.loc[future, col] = np.nan
            if future.any():
                warnings.append(
                    "Будущие плановые месяцы не имеют наблюдений продаж: факт отсутствует, а не равен нулю."
                )
        table["client"] = "Не распределён"
    table = table.merge(
        dims[["outlet_id", "outlet_name", "region_name"]], on="outlet_id", how="left", validate="many_to_one"
    ).merge(
        f["products"][["product_id", "product_name", "category", "unit"]],
        on="product_id",
        how="left",
        validate="many_to_one",
    )
    if table.outlet_name.isna().any() or table.product_name.isna().any():
        raise SourceError("Отсутствуют записи справочника; проверьте связи")
    table = table.rename(
        columns={"outlet_name": "outlet", "region_name": "region", "product_name": "product"}
    )
    cols = [
        "date",
        "region",
        "outlet",
        "product",
        "category",
        "unit",
        "client",
        "fact",
        "plan",
        "quantity",
        "plan_quantity",
        "discount",
        "deals",
        "outlet_id",
        "product_id",
    ]
    cols += [c for c in ["reason", "sale_id", "discount_id", "client_id"] if c in table]
    warnings.append(
        "План помесячный: фильтры дат применяются к месяцам. Частичный месяц нельзя сравнить с полным планом."
    )
    quality["flat_rows"] = len(table)
    quality["flat_fact_sum"] = float(table.fact.sum())
    quality["flat_plan_sum"] = float(table.plan.sum())
    return table[cols], warnings, quality
