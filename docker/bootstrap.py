"""One-time fixture import. Admin credentials are never given to the application."""

import csv
import io
import os
from pathlib import Path
import urllib.request
import zipfile

import psycopg
from psycopg import sql
from scripts.import_data import SCHEMA


def password(name):
    value = os.environ.get(name, "")
    if len(value) < 24 or value == "CHANGE_ME":
        raise ValueError(f"Generate {name} with scripts/docker_start.sh before starting")
    return value


def archive_path(supplied=Path("/imports/puzzle-data.zip"), cache=Path("/cache/puzzle-data.zip")):
    if supplied.is_file():
        return supplied
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.is_file():
        url = os.environ.get("BI_DATA_URL", "")
        if not url.startswith("https://"):
            raise ValueError("BI_DATA_URL must be HTTPS, or provide data/puzzle-data.zip")
        temporary = cache.with_suffix(".download")
        print("Downloading published Puzzle RPA fixture…", flush=True)
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open("wb") as dest:
                while block := response.read(1024 * 1024):
                    dest.write(block)
            with zipfile.ZipFile(temporary) as archive:
                if not all(f"data/{name}.csv" in archive.namelist() for name in SCHEMA):
                    raise ValueError("Archive does not contain all eight source tables")
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
    return cache


def main():
    admin = password("BI_DB_ADMIN_PASSWORD")
    reader = password("BI_DB_READER_PASSWORD")
    dsn = psycopg.conninfo.make_conninfo(host="postgres", user="bi_admin", password=admin, dbname="postgres")
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(7042026)")
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname='rpa_reader'").fetchone():
            conn.execute("CREATE ROLE rpa_reader LOGIN")
        conn.execute(sql.SQL("ALTER ROLE rpa_reader PASSWORD {}").format(sql.Literal(reader)))
        conn.execute("ALTER ROLE rpa_reader SET default_transaction_read_only=on")
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='puzzle_rpa'").fetchone():
            conn.execute("CREATE DATABASE puzzle_rpa")
        with psycopg.connect(psycopg.conninfo.make_conninfo(dsn, dbname="puzzle_rpa")) as target:
            imported = target.execute("SELECT to_regclass('public.bi_fixture_import')").fetchone()[0]
            tables = {
                row[0] for row in target.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
            }
            if imported:
                if not set(SCHEMA) <= tables:
                    raise ValueError("Existing source is incomplete; restore it without overwriting data")
                print("Source already initialized; existing data preserved.", flush=True)
            else:
                if tables:
                    raise ValueError("Source already contains tables; refusing to overwrite it")
                with zipfile.ZipFile(archive_path()) as archive:
                    for name, fields in SCHEMA.items():
                        target.execute(
                            sql.SQL("CREATE TABLE {} ({})").format(sql.Identifier(name), sql.SQL(fields))
                        )
                        with target.cursor().copy(
                            sql.SQL("COPY {} FROM STDIN").format(sql.Identifier(name))
                        ) as copy:
                            rows = csv.reader(
                                io.TextIOWrapper(archive.open(f"data/{name}.csv"), encoding="utf-8-sig")
                            )
                            next(rows)
                            count = 0
                            for row in rows:
                                copy.write_row([None if value == "\\N" else value for value in row])
                                count += 1
                        print(f"{name}: {count} rows", flush=True)
                target.execute("CREATE INDEX ON sales(sale_date)")
                target.execute("CREATE INDEX ON sales_plan(plan_month)")
                target.execute("CREATE INDEX ON discounts(sale_id)")
                target.execute("CREATE TABLE bi_fixture_import(created timestamptz NOT NULL DEFAULT now())")
                target.execute("INSERT INTO bi_fixture_import DEFAULT VALUES")
                target.execute("ANALYZE")
            target.execute("GRANT CONNECT ON DATABASE puzzle_rpa TO rpa_reader")
            target.execute("GRANT USAGE ON SCHEMA public TO rpa_reader")
            target.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO rpa_reader")
    print("Read-only source is ready.", flush=True)


if __name__ == "__main__":
    main()
