import gzip
import copy
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from shared.models import uid
from shared.analytics import empty_state, capture_snapshot
from shared.library_features import LibraryFeatures
from shared.conversations import Conversations
from shared.state_codec import StateCodec, serializing


def now():
    return datetime.now(timezone.utc).isoformat()


def pack(value):
    return gzip.compress(json.dumps(value, ensure_ascii=False, allow_nan=False).encode(), compresslevel=1)


def unpack(value):
    return json.loads(gzip.decompress(value))


class Conflict(ValueError):
    pass


class Store(LibraryFeatures, Conversations, StateCodec):
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript(
                "PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS sessions(owner TEXT PRIMARY KEY, revision INTEGER NOT NULL, state BLOB NOT NULL); CREATE TABLE IF NOT EXISTS dashboards(id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, query TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL, state BLOB NOT NULL); CREATE INDEX IF NOT EXISTS dashboard_owner ON dashboards(owner);"
            )
            c.execute(
                "CREATE TABLE IF NOT EXISTS chat_media(id TEXT PRIMARY KEY, owner TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL, body BLOB NOT NULL, created TEXT NOT NULL)"
            )
            c.execute("CREATE INDEX IF NOT EXISTS chat_media_owner ON chat_media(owner)")
            c.execute(
                "CREATE TABLE IF NOT EXISTS dashboard_cards(owner TEXT NOT NULL, message_id TEXT NOT NULL, state BLOB NOT NULL, PRIMARY KEY(owner,message_id))"
            )
            self.init_features(c)
            self.init_conversations(c)
            c.execute(
                "CREATE TABLE IF NOT EXISTS card_revisions(owner TEXT NOT NULL,message_id TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(owner,message_id))"
            )
        Path(self.path).chmod(0o600)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        return c

    def load(self, owner, hydrate=True):
        with self.connect() as c:
            row = c.execute("SELECT state FROM sessions WHERE owner=?", (owner,)).fetchone()
            return self._unpack_state(owner, row["state"], c, hydrate) if row else empty_state()

    def commit(self, owner, state, expected, *, autosave=False, card_update=None, sync_card=False):
        with serializing(), self.lock, self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT revision FROM sessions WHERE owner=?", (owner,)).fetchone()
            current = row["revision"] if row else 0
            if current != expected:
                raise Conflict("Состояние уже изменилось. Получите последнюю версию и повторите действие.")
            state["revision"] = current + 1
            if autosave and state.get("dataset") and (state["dashboard"]["charts"] or state.get("workspace")):
                capture_snapshot(state)
                # Commit the library entry and session together, after the revision check.
                self._save(c, owner, state, state["dashboard"]["title"], update_caption=False)
            if card_update:
                message_id, scope, save = card_update
                capture_snapshot(scope)
                self._save(c, owner, scope, **save)
                target = next(m for m in state["messages"] if m.get("id") == message_id)
                self._put_card(c, owner, message_id, scope)
                target["dashboard"] = copy.deepcopy(scope["messages"][-1]["dashboard"])
                if sync_card:
                    for key in [
                        "dataset",
                        "snapshot",
                        "filters",
                        "dashboard",
                        "calculations",
                        "calculation_history",
                        "saved_id",
                        "saved_dashboard_version",
                        "filter_notice",
                    ]:
                        if key in scope:
                            state[key] = scope[key]
                    if state.get("workspace"):
                        state["workspace"].update(
                            saved_id=scope["saved_id"], title=scope["dashboard"]["title"]
                        )
                    from shared.dashboard_brief import sync_bound

                    sync_bound(state)
            from shared.dashboard_context import active_message

            message = active_message(state)
            if (
                message
                and state.get("dataset")
                and message["dashboard"]["id"] == state["dashboard"]["id"]
                and message["dashboard"]["table_id"] == state["dataset"]["id"]
                and (not card_update or message["id"] != card_update[0])
            ):
                from shared.dashboard_context import card_state

                if not card_update:
                    scope = card_state(state, message)
                    self._put_card(c, owner, message["id"], scope)
                    message["dashboard"]["settings_revision"] = scope["messages"][-1]["dashboard"][
                        "settings_revision"
                    ]
            c.execute(
                "INSERT INTO sessions(owner,revision,state) VALUES(?,?,?) ON CONFLICT(owner) DO UPDATE SET revision=excluded.revision,state=excluded.state",
                (owner, state["revision"], self._pack_state(c, owner, state)),
            )
            self.touch_conversation(c, owner, state)
        return state

    def _put_card(self, connection, owner, message_id, state):
        connection.execute(
            "INSERT INTO card_revisions VALUES(?,?,1) ON CONFLICT(owner,message_id) DO UPDATE SET revision=revision+1",
            (owner, message_id),
        )
        revision = connection.execute(
            "SELECT revision FROM card_revisions WHERE owner=? AND message_id=?", (owner, message_id)
        ).fetchone()[0]
        state["messages"][-1]["dashboard"]["settings_revision"] = revision
        connection.execute(
            "INSERT INTO dashboard_cards VALUES(?,?,?) ON CONFLICT(owner,message_id) DO UPDATE SET state=excluded.state",
            (owner, message_id, self._pack_state(connection, owner, state)),
        )

    def card(self, owner, state, message_id):
        from shared.dashboard_context import restore_legacy

        message = next(
            (m for m in state["messages"] if m.get("id") == message_id and m.get("dashboard")), None
        )
        if not message:
            raise ValueError("Дашборд не найден в этой переписке")
        with self.connect() as c:
            library_owner = self.account_owner(owner, c)
            row = c.execute(
                "SELECT state FROM dashboard_cards WHERE owner=? AND message_id=?", (owner, message_id)
            ).fetchone()
            if row:
                scope = self._unpack_state(owner, row["state"], c)
                scope["messages"] = [copy.deepcopy(message)]
                name = c.execute(
                    "SELECT title FROM dashboards WHERE owner=? AND id=?",
                    (library_owner, scope.get("saved_id")),
                ).fetchone()
                if name:
                    scope["dashboard"]["title"] = name["title"]
                scope["messages"][-1]["dashboard"]["settings_revision"] = self.card_revision(
                    owner, message_id, c
                )
                return scope
            candidates = c.execute(
                "SELECT state FROM dashboards WHERE owner=? ORDER BY updated DESC", (library_owner,)
            ).fetchall()
        for base in [state, *(self._unpack_state(owner, r["state"]) for r in candidates)]:
            if base.get("dataset") and base["dataset"]["id"] == message["dashboard"]["table_id"]:
                if "__data_blob__" in base["dataset"]:
                    base = self._unpack_state(owner, pack(base))
                return restore_legacy(base, message)
        raise ValueError("Для этого ответа не сохранился снимок данных. Постройте дашборд новым запросом.")

    def prepare_cards(self, owner, state):
        """Populate old cards once; new cards already carry their own options and state."""
        from shared.dashboard_context import filter_options

        with self.connect() as c:
            revisions = {
                r["message_id"]: r["revision"]
                for r in c.execute("SELECT message_id,revision FROM card_revisions WHERE owner=?", (owner,))
            }
        for message in state["messages"]:
            data = message.get("dashboard")
            if data:
                data["settings_revision"] = revisions.get(message["id"], 0)
            if not data or "editable" in data:
                continue
            try:
                scope = self.card(owner, state, message["id"])
                data.update(editable=True, options=filter_options(scope))
                scope["messages"] = [copy.deepcopy(message)]
                with self.connect() as c:
                    self._put_card(c, owner, message["id"], scope)
            except ValueError:
                data.update(
                    editable=False,
                    edit_notice="Снимок данных этого старого ответа не сохранился. Дашборд можно построить новым запросом.",
                )

    def save(self, owner, state, title, as_new=False, query=None):
        with serializing(), self.connect() as c:
            return self._save(c, owner, state, title, as_new, query)

    def _save(self, c, owner, state, title, as_new=False, query=None, *, update_caption=True):
        owner = self.account_owner(owner, c)
        previous = state.get("saved_id")
        ident = uid() if as_new or not previous else previous
        timestamp = now()
        version_label = state.pop("_version_label", None)
        old = c.execute(
            "SELECT created,query,state FROM dashboards WHERE id=? AND owner=?", (ident, owner)
        ).fetchone()
        if (
            old
            and not c.execute(
                "SELECT 1 FROM dashboard_versions WHERE owner=? AND dashboard_id=?", (owner, ident)
            ).fetchone()
        ):
            self._record_version(c, owner, ident, self._unpack_state(owner, old["state"], c))
        if previous and not as_new and not old:
            ident = uid()
        if query is None:
            origin = (
                old
                or c.execute(
                    "SELECT query FROM dashboards WHERE id=? AND owner=?", (previous, owner)
                ).fetchone()
            )
            query = (
                origin["query"]
                if origin
                else (state.get("workspace") or {}).get("query")
                or next((x["content"] for x in reversed(state["messages"]) if x["role"] == "user"), "")
            )
            if not origin and not state.get("workspace"):
                from shared.dashboard_brief import archive_query

                query = archive_query(state, query)
        state["saved_id"] = ident
        state["saved_dashboard_version"] = state["dashboard"]["version"]
        state["dashboard"]["title"] = title
        if state.get("workspace"):
            state["workspace"].update(
                saved_id=ident,
                title=title,
                query=query,
                created=old["created"] if old else timestamp,
                updated=timestamp,
            )
        from shared.dashboard_context import active_message

        message = active_message(state)
        if message and message["dashboard"]["id"] == state["dashboard"]["id"]:
            message["dashboard"].update(saved_id=ident, saved=True, saved_title=title)
            if update_caption:
                message["dashboard"].update(title=title, title_filters=copy.deepcopy(state["filters"]))
        c.execute(
            "INSERT INTO dashboards VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,query=excluded.query,updated=excluded.updated,state=excluded.state WHERE dashboards.owner=excluded.owner",
            (
                ident,
                owner,
                title,
                query,
                old["created"] if old else timestamp,
                timestamp,
                self._pack_state(c, owner, state),
            ),
        )
        self._record_version(c, owner, ident, state, version_label)
        return {"id": ident, "title": title, "updated": timestamp}

    def card_revision(self, owner, message_id, connection=None):
        if connection is not None:
            row = connection.execute(
                "SELECT revision FROM card_revisions WHERE owner=? AND message_id=?", (owner, message_id)
            ).fetchone()
            return row[0] if row else 0
        with self.connect() as c:
            return self.card_revision(owner, message_id, c)

    def list(self, owner, search="", sort="updated"):
        import unicodedata

        owner = self.account_owner(owner)

        def normalize(value):
            return unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")

        ordering = {
            "updated": "updated DESC",
            "created": "created DESC",
            "title": "title COLLATE NOCASE ASC",
        }[sort]
        with self.connect() as c:
            rows = c.execute(
                "SELECT id,title,query,created,updated FROM dashboards WHERE owner=? ORDER BY " + ordering,
                (owner,),
            ).fetchall()
        terms = normalize(search).split()
        items = [
            dict(r) for r in rows if all(term in normalize(r["title"] + " " + r["query"]) for term in terms)
        ]
        if sort == "title":
            items.sort(key=lambda item: normalize(item["title"]))
        return items

    def open(self, owner, ident):
        owner = self.account_owner(owner)
        with self.connect() as c:
            row = c.execute("SELECT state FROM dashboards WHERE owner=? AND id=?", (owner, ident)).fetchone()
        if not row:
            raise ValueError("Дашборд не найден или недоступен")
        state = self._unpack_state(owner, row["state"])
        state["saved_id"] = ident
        state["saved_dashboard_version"] = state["dashboard"]["version"]
        return state

    def rename(self, owner, ident, title):
        owner = self.account_owner(owner)
        timestamp = now()
        with self.connect() as c:
            row = c.execute("SELECT state FROM dashboards WHERE id=? AND owner=?", (ident, owner)).fetchone()
            if not row:
                raise ValueError("Дашборд не найден")
            state = self._unpack_state(owner, row["state"], c)
            self._record_version(c, owner, ident, state)
            state["dashboard"]["title"] = title
            if state.get("workspace"):
                state["workspace"].update(title=title, updated=timestamp)
            c.execute(
                "UPDATE dashboards SET title=?,updated=?,state=? WHERE id=? AND owner=?",
                (title, timestamp, self._pack_state(c, owner, state), ident, owner),
            )
            self._record_version(c, owner, ident, state)
        return {"id": ident, "title": title, "updated": timestamp}

    def delete(self, owner, ident):
        owner = self.account_owner(owner)
        with self.connect() as c:
            result = c.execute("DELETE FROM dashboards WHERE id=? AND owner=?", (ident, owner))
            if not result.rowcount:
                raise ValueError("Дашборд не найден")
            c.execute("DELETE FROM dashboard_versions WHERE owner=? AND dashboard_id=?", (owner, ident))
            c.execute("DELETE FROM publications WHERE owner=? AND dashboard_id=?", (owner, ident))

    def add_media(self, owner, body, filename, mime):
        ident = uid()
        with self.connect() as c:
            c.execute(
                "INSERT INTO chat_media VALUES(?,?,?,?,?,?)", (ident, owner, filename, mime, body, now())
            )
        return {"id": ident, "filename": filename, "mime": mime, "url": f"/api/chat/media/{ident}"}

    def media(self, owner, ident):
        with self.connect() as c:
            row = c.execute(
                "SELECT filename,mime,body FROM chat_media WHERE id=? AND owner=?", (ident, owner)
            ).fetchone()
        if not row:
            raise ValueError("Вложение не найдено или недоступно")
        return dict(row)
