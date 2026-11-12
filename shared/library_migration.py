"""Move existing per-chat library data to its authenticated browser account."""


def migrate_shared_library(store):
    from shared.storage import now

    with store.lock, store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "CREATE TABLE IF NOT EXISTS storage_migrations(name TEXT PRIMARY KEY,applied TEXT NOT NULL)"
        )
        if c.execute("SELECT 1 FROM storage_migrations WHERE name='account_library_v1'").fetchone():
            return
        scopes = c.execute("SELECT owner,scope FROM conversations WHERE owner!=scope").fetchall()
        for account, scope in scopes:
            # Content-addressed refs stay unchanged in sessions, cards and versions.
            # Copy first, then remove the old namespace, in the same transaction.
            c.execute(
                "INSERT OR IGNORE INTO dashboard_blobs(owner,id,body) SELECT ?,id,body FROM dashboard_blobs WHERE owner=?",
                (account, scope),
            )
            for table in ["dashboards", "dashboard_versions", "publications"]:
                c.execute(f"UPDATE {table} SET owner=? WHERE owner=?", (account, scope))
            c.execute("DELETE FROM dashboard_blobs WHERE owner=?", (scope,))
        c.execute("INSERT INTO storage_migrations VALUES('account_library_v1',?)", (now(),))
