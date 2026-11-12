"""Independent conversation scopes behind the browser owner's credential."""

from shared.models import uid


class Conversations:
    def init_conversations(self, c):
        c.execute("""CREATE TABLE IF NOT EXISTS conversations(
            owner TEXT NOT NULL,id TEXT NOT NULL,scope TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,automatic INTEGER NOT NULL,created TEXT NOT NULL,
            updated TEXT NOT NULL,preview TEXT NOT NULL DEFAULT '',
            messages INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner,id))""")

    def conversation_scope(self, owner, ident):
        if ident == "main":
            return owner
        with self.connect() as c:
            row = c.execute(
                "SELECT scope FROM conversations WHERE owner=? AND id=?", (owner, ident)
            ).fetchone()
        if not row:
            raise ValueError("Чат не найден")
        return row["scope"]

    def account_owner(self, scope, connection=None):
        if connection is None:
            with self.connect() as c:
                return self.account_owner(scope, c)
        row = connection.execute("SELECT owner FROM conversations WHERE scope=?", (scope,)).fetchone()
        return row["owner"] if row else scope

    def conversations(self, owner):
        from shared.storage import now, unpack

        with self.connect() as c:
            timestamp = now()
            created = c.execute(
                "INSERT OR IGNORE INTO conversations(owner,id,scope,title,automatic,created,updated) VALUES(?,?,?,?,0,?,?)",
                (owner, "main", owner, "Основной чат", timestamp, timestamp),
            ).rowcount
            if created:
                row = c.execute("SELECT state FROM sessions WHERE owner=?", (owner,)).fetchone()
                if row:
                    self.touch_conversation(c, owner, unpack(row["state"]), change_time=False)
            rows = c.execute(
                "SELECT id,title,created,updated,preview,messages FROM conversations WHERE owner=? ORDER BY updated DESC,id",
                (owner,),
            ).fetchall()
            running = set()
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_jobs'").fetchone():
                running = {
                    r[0]
                    for r in c.execute(
                        "SELECT DISTINCT c.id FROM conversations c JOIN analysis_jobs j ON j.owner=c.scope WHERE c.owner=? AND j.status IN ('queued','running')",
                        (owner,),
                    )
                }
        return [{**dict(row), "running": row["id"] in running} for row in rows]

    def new_conversation(self, owner, title="Новый чат"):
        from shared.storage import now

        self.conversations(owner)
        ident, timestamp = uid(), now()
        with self.connect() as c:
            c.execute(
                "INSERT INTO conversations(owner,id,scope,title,automatic,created,updated) VALUES(?,?,?,?,1,?,?)",
                (owner, ident, owner + ":chat:" + ident, title, timestamp, timestamp),
            )
        return next(row for row in self.conversations(owner) if row["id"] == ident)

    def rename_conversation(self, owner, ident, title):
        from shared.storage import now

        title = title.strip()
        if not title:
            raise ValueError("Введите название чата")
        self.conversations(owner)
        with self.connect() as c:
            if not c.execute(
                "UPDATE conversations SET title=?,automatic=0,updated=? WHERE owner=? AND id=?",
                (title, now(), owner, ident),
            ).rowcount:
                raise ValueError("Чат не найден")
        return {"ok": True}

    def touch_conversation(self, c, scope, state, change_time=True):
        from shared.storage import now

        row = c.execute(
            "SELECT title,automatic,updated FROM conversations WHERE scope=?", (scope,)
        ).fetchone()
        if not row:
            return
        messages = state.get("messages", [])
        first = next((m["content"] for m in messages if m["role"] == "user" and m.get("content")), "")
        title = " ".join(first.split())[:70] if first and row["automatic"] else row["title"]
        preview = next((m.get("content", "") for m in reversed(messages) if m.get("content")), "")
        c.execute(
            "UPDATE conversations SET title=?,automatic=?,updated=?,preview=?,messages=? WHERE scope=?",
            (
                title,
                0 if first else row["automatic"],
                now() if change_time else row["updated"],
                " ".join(preview.split())[:160],
                len(messages),
                scope,
            ),
        )
