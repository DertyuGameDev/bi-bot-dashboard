"""Owner-scoped revisions, drafts, query templates and published snapshots."""

import copy
import hashlib
import json
import secrets
from shared.models import uid

BUILTIN_TEMPLATES = [
    {
        "id": "sales",
        "title": "Стандартный дашборд продаж",
        "prompt": "Собери стандартный дашборд по продажам за последний доступный год: план и факт по месяцам, структура продаж, рейтинг регионов и выполнение плана. До четырёх графиков.",
    },
    {
        "id": "outlets",
        "title": "Сравнить торговые точки",
        "prompt": "Покажи сравнение выручки торговых точек по месяцам за последний доступный год.",
    },
    {
        "id": "regions",
        "title": "План и факт по регионам",
        "prompt": "Покажи план и факт продаж по регионам и динамику выполнения плана за последний доступный год.",
    },
]


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


class LibraryFeatures:
    def init_features(self, c):
        c.executescript("""
            CREATE TABLE IF NOT EXISTS dashboard_blobs(owner TEXT NOT NULL,id TEXT NOT NULL,body BLOB NOT NULL,PRIMARY KEY(owner,id));
            CREATE TABLE IF NOT EXISTS dashboard_versions(owner TEXT NOT NULL,dashboard_id TEXT NOT NULL,seq INTEGER NOT NULL,created TEXT NOT NULL,label TEXT NOT NULL,digest TEXT NOT NULL,state BLOB NOT NULL,PRIMARY KEY(owner,dashboard_id,seq));
            CREATE TABLE IF NOT EXISTS drafts(owner TEXT NOT NULL,context TEXT NOT NULL,text TEXT NOT NULL,updated TEXT NOT NULL,PRIMARY KEY(owner,context));
            CREATE TABLE IF NOT EXISTS user_preferences(owner TEXT PRIMARY KEY,draft_key TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS query_templates(id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,prompt TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publications(token TEXT PRIMARY KEY,owner TEXT NOT NULL,dashboard_id TEXT NOT NULL,title TEXT NOT NULL,created TEXT NOT NULL,updated TEXT NOT NULL,body BLOB NOT NULL,UNIQUE(owner,dashboard_id));
        """)
        if "version" not in {row[1] for row in c.execute("PRAGMA table_info(drafts)")}:
            c.execute("ALTER TABLE drafts ADD COLUMN version INTEGER NOT NULL DEFAULT 0")

    def _version_core(self, c, owner, state):
        from shared.storage import pack

        core = {
            key: copy.deepcopy(state.get(key))
            for key in [
                "filters",
                "dashboard",
                "calculations",
                "calculation_history",
                "saved_id",
                "filter_notice",
            ]
        }
        for key in ["dataset", "snapshot"]:
            value = state.get(key)
            if key == "snapshot" and value:
                core["snapshot_meta"] = {"captured_at": value.get("captured_at")}
            ident = self._blob_ref(c, owner, value, key == "snapshot")
            if ident is None:
                ident = fingerprint(None)
                c.execute("INSERT OR IGNORE INTO dashboard_blobs VALUES(?,?,?)", (owner, ident, pack(None)))
            core[key + "_ref"] = ident
        return core

    def _record_version(self, c, owner, ident, state, label=None):
        from shared.storage import pack, unpack, now

        core = self._version_core(c, owner, state)
        semantic = copy.deepcopy(core)
        semantic["dashboard"].pop("version", None)
        semantic.pop("snapshot_meta", None)
        digest = fingerprint(semantic)
        previous = c.execute(
            "SELECT seq,digest,state FROM dashboard_versions WHERE owner=? AND dashboard_id=? ORDER BY seq DESC LIMIT 1",
            (owner, ident),
        ).fetchone()
        if previous and previous["digest"] == digest:
            return
        if not label:
            label = "Создан дашборд"
            if previous:
                old = unpack(previous["state"])
                label = "Изменены графики"
                if old["dataset_ref"] != core["dataset_ref"]:
                    label = "Обновлены данные"
                elif old.get("calculations") != core.get("calculations"):
                    label = "Изменены вычисления"
                elif old["filters"] != core["filters"]:
                    label = "Изменены фильтры"
                elif old["dashboard"]["title"] != core["dashboard"]["title"]:
                    label = "Изменено название"
        c.execute(
            "INSERT INTO dashboard_versions VALUES(?,?,?,?,?,?,?)",
            (owner, ident, previous["seq"] + 1 if previous else 1, now(), label, digest, pack(core)),
        )

    def versions(self, owner, ident):
        from shared.storage import unpack

        owner = self.account_owner(owner)
        with self.lock, self.connect() as c:
            entry = c.execute(
                "SELECT state FROM dashboards WHERE owner=? AND id=?", (owner, ident)
            ).fetchone()
            if not entry:
                raise ValueError("Дашборд не найден")
            if not c.execute(
                "SELECT 1 FROM dashboard_versions WHERE owner=? AND dashboard_id=?", (owner, ident)
            ).fetchone():
                self._record_version(
                    c,
                    owner,
                    ident,
                    self._unpack_state(owner, entry["state"], c),
                    "Исходная сохранённая версия",
                )
            rows = c.execute(
                "SELECT seq,created,label,state FROM dashboard_versions WHERE owner=? AND dashboard_id=? ORDER BY seq DESC",
                (owner, ident),
            ).fetchall()
            result = []
            for row in rows:
                core = unpack(row["state"])
                f = core["filters"]
                regions = ", ".join(f["regions"]) or "Все регионы"
                period = " — ".join(v for v in [f.get("start"), f.get("end")] if v)
                details = " · ".join(
                    v
                    for v in [
                        period,
                        regions,
                        {"month": "Месяцы", "quarter": "Кварталы", "year": "Годы"}[f["granularity"]],
                    ]
                    if v
                )
                result.append(
                    {
                        "seq": row["seq"],
                        "created": row["created"],
                        "label": row["label"],
                        "details": details,
                        "calculations": len(core.get("calculations") or []),
                    }
                )
            return result

    def version(self, owner, ident, seq):
        from shared.storage import unpack
        from shared.analytics import empty_state

        owner = self.account_owner(owner)
        with self.connect() as c:
            row = c.execute(
                "SELECT v.state FROM dashboard_versions v JOIN dashboards d ON d.id=v.dashboard_id AND d.owner=v.owner WHERE v.owner=? AND v.dashboard_id=? AND v.seq=?",
                (owner, ident, seq),
            ).fetchone()
            if not row:
                raise ValueError("Версия не найдена")
            core = unpack(row["state"])
            for key in ["dataset", "snapshot"]:
                blob = c.execute(
                    "SELECT body FROM dashboard_blobs WHERE owner=? AND id=?", (owner, core.pop(key + "_ref"))
                ).fetchone()
                core[key] = unpack(blob["body"])
            if core.get("snapshot") and core.get("snapshot_meta"):
                core["snapshot"].update(core.pop("snapshot_meta"))
        result = empty_state()
        result.update(core, saved_id=ident)
        return result

    def draft(self, owner, context):
        with self.connect() as c:
            row = c.execute(
                "SELECT text,updated,version FROM drafts WHERE owner=? AND context=?", (owner, context)
            ).fetchone()
            c.execute("INSERT OR IGNORE INTO user_preferences VALUES(?,?)", (owner, secrets.token_hex(16)))
            key = c.execute("SELECT draft_key FROM user_preferences WHERE owner=?", (owner,)).fetchone()[
                "draft_key"
            ]
        return {
            "storage_key": key,
            "text": row["text"] if row else "",
            "updated": row["updated"] if row else None,
            "context": context,
            "version": row["version"] if row else 0,
        }

    def save_draft(self, owner, context, text, version=0):
        from shared.storage import now
        import time

        version = version or int(time.time() * 1000)

        with self.connect() as c:
            c.execute(
                "INSERT INTO drafts(owner,context,text,updated,version) VALUES(?,?,?,?,?) ON CONFLICT(owner,context) DO UPDATE SET text=excluded.text,updated=excluded.updated,version=excluded.version WHERE excluded.version>=drafts.version",
                (owner, context, text, now(), version),
            )
        return self.draft(owner, context)

    def templates(self, owner):
        with self.connect() as c:
            custom = [
                dict(r)
                for r in c.execute(
                    "SELECT id,title,prompt FROM query_templates WHERE owner=? ORDER BY updated DESC",
                    (owner,),
                )
            ]
        return [{**item, "builtin": True} for item in BUILTIN_TEMPLATES] + [
            {**item, "builtin": False} for item in custom
        ]

    def save_template(self, owner, title, prompt, ident=None):
        from shared.storage import now

        timestamp = now()
        with self.connect() as c:
            if ident:
                row = c.execute(
                    "SELECT created FROM query_templates WHERE owner=? AND id=?", (owner, ident)
                ).fetchone()
                if not row:
                    raise ValueError("Шаблон не найден")
            else:
                ident, row = uid(), None
            c.execute(
                "INSERT INTO query_templates VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,prompt=excluded.prompt,updated=excluded.updated WHERE query_templates.owner=excluded.owner",
                (ident, owner, title, prompt, row["created"] if row else timestamp, timestamp),
            )
        return {"id": ident, "title": title, "prompt": prompt}

    def delete_template(self, owner, ident):
        with self.connect() as c:
            if not c.execute("DELETE FROM query_templates WHERE owner=? AND id=?", (owner, ident)).rowcount:
                raise ValueError("Шаблон не найден")

    def publication(self, owner, ident):
        owner = self.account_owner(owner)
        with self.connect() as c:
            row = c.execute(
                "SELECT token,title,created,updated FROM publications WHERE owner=? AND dashboard_id=?",
                (owner, ident),
            ).fetchone()
        return dict(row) if row else None

    def publish(self, owner, ident, title, body):
        from shared.storage import pack, now

        owner = self.account_owner(owner)
        timestamp = now()
        with self.connect() as c:
            if not c.execute("SELECT 1 FROM dashboards WHERE owner=? AND id=?", (owner, ident)).fetchone():
                raise ValueError("Сначала сохраните дашборд")
            old = c.execute(
                "SELECT token,created FROM publications WHERE owner=? AND dashboard_id=?", (owner, ident)
            ).fetchone()
            token = old["token"] if old else secrets.token_urlsafe(32)
            c.execute(
                "INSERT INTO publications VALUES(?,?,?,?,?,?,?) ON CONFLICT(token) DO UPDATE SET title=excluded.title,updated=excluded.updated,body=excluded.body",
                (token, owner, ident, title, old["created"] if old else timestamp, timestamp, pack(body)),
            )
        return self.publication(owner, ident)

    def revoke(self, owner, ident):
        owner = self.account_owner(owner)
        with self.connect() as c:
            c.execute("DELETE FROM publications WHERE owner=? AND dashboard_id=?", (owner, ident))

    def public_snapshot(self, token):
        from shared.storage import unpack

        with self.connect() as c:
            row = c.execute("SELECT body FROM publications WHERE token=?", (token,)).fetchone()
        if not row:
            raise ValueError("Публикация не найдена или отозвана")
        return unpack(row["body"])
