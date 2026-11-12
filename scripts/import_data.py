"""Import the published Puzzle RPA CSV fixture into a dedicated PostgreSQL database.
Admin writes happen only here, never in the application. No generated SQL is executed.
"""

import argparse
import csv
import io
import secrets
import zipfile
from pathlib import Path
import psycopg
from psycopg import sql

SCHEMA = {
    "regions": "region_id integer PRIMARY KEY, region_name text, regional_center text",
    "products": "product_id integer PRIMARY KEY, product_name text, category text, unit_price numeric, unit text, launch_date date, discontinued_date date",
    "clients": "client_id integer PRIMARY KEY, client_name text, inn text, segment text, region_id integer REFERENCES regions, registered_date date, is_key_client boolean",
    "contracts": "contract_id integer PRIMARY KEY, contract_number text, client_id integer REFERENCES clients, start_date date, end_date date, status text, payment_terms_days integer, base_discount_pct numeric",
    "outlets": "outlet_id integer PRIMARY KEY, outlet_name text, region_id integer REFERENCES regions, city text, outlet_type text, address text, opened_date date, closed_date date",
    "sales_plan": "plan_id integer PRIMARY KEY, plan_month date, outlet_id integer REFERENCES outlets, product_id integer REFERENCES products, plan_amount numeric, plan_qty numeric",
    "sales": "sale_id integer PRIMARY KEY, sale_date date, contract_id integer REFERENCES contracts, client_id integer REFERENCES clients, outlet_id integer REFERENCES outlets, product_id integer REFERENCES products, quantity numeric, amount numeric",
    "discounts": "discount_id integer PRIMARY KEY, sale_id integer REFERENCES sales, client_id integer REFERENCES clients, discount_date date, discount_amount numeric, discount_pct numeric, reason text",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=".local/puzzle-data.zip")
    parser.add_argument("--admin-dsn", default="host=127.0.0.1 port=55439 dbname=postgres")
    args = parser.parse_args()
    password = secrets.token_urlsafe(32)
    with psycopg.connect(args.admin_dsn, autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname='puzzle_rpa'").fetchone():
            raise SystemExit("Database puzzle_rpa already exists; no data overwritten.")
        conn.execute("CREATE DATABASE puzzle_rpa")
        conn.execute(sql.SQL("CREATE ROLE rpa_reader LOGIN PASSWORD {}").format(sql.Literal(password)))
    dsn = psycopg.conninfo.make_conninfo(args.admin_dsn, dbname="puzzle_rpa")
    with psycopg.connect(dsn) as conn, zipfile.ZipFile(args.archive) as archive:
        for name, fields in SCHEMA.items():
            conn.execute(sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(name), sql.SQL(fields)))
            with conn.cursor().copy(sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(name))) as copy:
                reader = csv.reader(io.TextIOWrapper(archive.open(f"data/{name}.csv"), encoding="utf-8-sig"))
                next(reader)
                count = 0
                for row in reader:
                    copy.write_row([None if v == "\\N" else v for v in row])
                    count += 1
            print(f"{name}: {count} rows")
        conn.execute("CREATE INDEX ON sales(sale_date)")
        conn.execute("CREATE INDEX ON sales_plan(plan_month)")
        conn.execute("CREATE INDEX ON discounts(sale_id)")
        conn.execute("GRANT CONNECT ON DATABASE puzzle_rpa TO rpa_reader")
        conn.execute("GRANT USAGE ON SCHEMA public TO rpa_reader")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO rpa_reader")
        conn.execute("ALTER ROLE rpa_reader SET default_transaction_read_only = on")
        conn.execute("ANALYZE")
    env = Path(".env")
    if env.exists():
        raise SystemExit(
            "Imported, but .env exists: not overwritten. Set reader credentials administratively."
        )
    env.write_text(
        f"RPA_SOURCE=postgres\nRPA_DATABASE_URL=postgresql://rpa_reader:{password}@127.0.0.1:55439/puzzle_rpa\nRPA_MODEL_ADAPTER=codex\nRPA_MODEL=gpt-6.1-terra\nRPA_REASONING=medium\n",
        encoding="utf-8",
    )
    env.chmod(0o600)
    print("Read-only application connection written to ignored .env (mode 0600).")


if __name__ == "__main__":
    main()
