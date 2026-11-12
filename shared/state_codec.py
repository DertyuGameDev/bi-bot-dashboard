"""Store immutable source/snapshot blobs once; accept existing inline states unchanged."""

from contextlib import contextmanager
from contextvars import ContextVar
from shared.library_features import fingerprint

_memo = ContextVar("state_blob_memo", default=None)


@contextmanager
def serializing():
    token = _memo.set({})
    try:
        yield
    finally:
        _memo.reset(token)


class StateCodec:
    def _blob_ref(self, c, owner, value, snapshot=False):
        from shared.storage import pack

        owner = self.account_owner(owner, c)
        if value is None:
            return None
        if "__data_blob__" in value:
            return value["__data_blob__"]
        cache = _memo.get()
        key = (owner, id(value), snapshot)
        if cache is not None and key in cache and cache[key][0] is value:
            return cache[key][1]
        content = {k: v for k, v in value.items() if k != "captured_at"} if snapshot else value
        ident = fingerprint(content)
        if not c.execute("SELECT 1 FROM dashboard_blobs WHERE owner=? AND id=?", (owner, ident)).fetchone():
            c.execute("INSERT INTO dashboard_blobs VALUES(?,?,?)", (owner, ident, pack(content)))
        if cache is not None:
            cache[key] = (value, ident)
        return ident

    def _pack_state(self, c, owner, state):
        from shared.storage import pack

        encoded = dict(state)
        for key in ["dataset", "snapshot"]:
            value = state.get(key)
            if value and "__data_blob__" not in value:
                encoded[key] = {"__data_blob__": self._blob_ref(c, owner, value, key == "snapshot")}
                if key == "dataset":
                    encoded[key].update(id=value["id"], version=value["version"])
                elif "captured_at" in value:
                    encoded[key]["captured_at"] = value["captured_at"]
        return pack(encoded)

    def _unpack_state(self, owner, body, c=None, hydrate=True):
        from shared.storage import unpack

        state = unpack(body)
        if not hydrate:
            return state
        if c is None:
            with self.connect() as connection:
                return self._unpack_state(owner, body, connection)
        for key in ["dataset", "snapshot"]:
            value = state.get(key)
            if value and "__data_blob__" in value:
                account = self.account_owner(owner, c)
                row = c.execute(
                    "SELECT body FROM dashboard_blobs WHERE owner=? AND id=?",
                    (account, value["__data_blob__"]),
                ).fetchone()
                if not row and account != owner:
                    row = c.execute(
                        "SELECT body FROM dashboard_blobs WHERE owner=? AND id=?",
                        (owner, value["__data_blob__"]),
                    ).fetchone()
                if not row:
                    raise ValueError("Сохранённый набор данных недоступен")
                state[key] = unpack(row["body"])
                if key == "snapshot" and "captured_at" in value:
                    state[key]["captured_at"] = value["captured_at"]
        return state
