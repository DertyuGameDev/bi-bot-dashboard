"""Owner-scoped background analysis and truthful stage/step progress."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from shared.models import uid
from shared.storage import pack, unpack


class AnalysisJobs:
    def __init__(self, store, worker):
        self.store, self.worker = store, worker
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="my-analysis")
        self.lock = threading.Lock()
        with store.connect() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS analysis_jobs(id TEXT PRIMARY KEY,owner TEXT NOT NULL,request_id TEXT NOT NULL,request BLOB NOT NULL,status TEXT NOT NULL,progress BLOB NOT NULL,result BLOB,error TEXT NOT NULL,created TEXT NOT NULL, UNIQUE(owner,request_id))"
            )

    def interrupt_unfinished(self):
        with self.store.connect() as c:
            c.execute(
                "UPDATE analysis_jobs SET status='error',error='Обработка прервана перезапуском сервера. Повторите запрос.' WHERE status IN ('queued','running')"
            )

    def submit(self, owner, request):
        request_id = request.get("request_id") or uid()
        with self.lock, self.store.connect() as c:
            existing = c.execute(
                "SELECT id FROM analysis_jobs WHERE owner=? AND request_id=?", (owner, request_id)
            ).fetchone()
            if existing:
                return self.get(owner, existing["id"])
            ident = uid()
            progress = {
                "phase": "queued",
                "label": "Запрос в очереди",
                "steps": 0,
                "completed": None,
                "total": None,
            }
            c.execute(
                "INSERT INTO analysis_jobs VALUES(?,?,?,?,?,?,NULL,'',?)",
                (
                    ident,
                    owner,
                    request_id,
                    pack(request),
                    "queued",
                    pack(progress),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        self.executor.submit(self.run, owner, ident, request)
        return self.get(owner, ident)

    def run(self, owner, ident, request):
        progress = {
            "phase": "analysis",
            "label": "Анализирую запрос",
            "steps": 0,
            "completed": None,
            "total": None,
        }

        def report(**event):
            progress.update(event)
            with self.store.connect() as c:
                c.execute(
                    "UPDATE analysis_jobs SET status='running',progress=? WHERE id=?", (pack(progress), ident)
                )

        try:
            report()
            result = self.worker(owner, request, report)
            progress.update(phase="done", label="Готово", completed=1, total=1)
            with self.store.connect() as c:
                c.execute(
                    "UPDATE analysis_jobs SET status='done',progress=?,result=? WHERE id=?",
                    (pack(progress), pack(result), ident),
                )
        except Exception as exc:
            detail = (
                str(exc)[:600]
                if isinstance(exc, ValueError)
                else "Не удалось обработать запрос. Повторите отправку."
            )
            with self.store.connect() as c:
                c.execute(
                    "UPDATE analysis_jobs SET status='error',error=?,progress=? WHERE id=?",
                    (detail, pack(progress), ident),
                )

    def get(self, owner, ident):
        with self.store.connect() as c:
            row = c.execute("SELECT * FROM analysis_jobs WHERE owner=? AND id=?", (owner, ident)).fetchone()
        if not row:
            raise ValueError("Запрос не найден")
        return {
            "id": ident,
            "status": row["status"],
            "progress": unpack(row["progress"]),
            "result": unpack(row["result"]) if row["result"] else None,
            "error": row["error"],
            "message": unpack(row["request"])["message"],
            "request_id": row["request_id"],
            "created": row["created"],
        }

    def active(self, owner):
        with self.store.connect() as c:
            row = c.execute(
                "SELECT id FROM analysis_jobs WHERE owner=? AND status IN ('queued','running') ORDER BY created LIMIT 1",
                (owner,),
            ).fetchone()
        return self.get(owner, row["id"]) if row else None

    def close(self):
        self.executor.shutdown(wait=True)
