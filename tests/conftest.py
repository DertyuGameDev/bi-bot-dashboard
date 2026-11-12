import pytest
from fastapi.testclient import TestClient
from shared.config import Settings
from MY.app import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        source="demo",
        model_adapter="demo",
        preflight_mode="off",
        dashboard_brief_enabled=False,
        storage=str(tmp_path / "state.sqlite3"),
        _env_file=None,
    )


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        c.get("/")
        c.headers["X-Requested-With"] = "puzzle-rpa"
        yield c


def post(client, path, args):
    revision = client.get("/api/state").json()["revision"]
    return client.post(path, json={"revision": revision, "arguments": args})


def build(client):
    revision = client.get("/api/state").json()["revision"]
    r = client.post(
        "/api/chat", json={"revision": revision, "message": "Собери дашборд по продажам за последний год"}
    )
    assert r.status_code == 200, r.text
    return r.json()
