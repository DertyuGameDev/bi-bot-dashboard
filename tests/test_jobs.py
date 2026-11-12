import threading
import time
from fastapi.testclient import TestClient


def wait(client, ident):
    for _ in range(150):
        job = client.get("/api/chat/jobs/" + ident).json()
        if job["status"] in ["done", "error"]:
            return job
        time.sleep(0.02)
    raise AssertionError("Background job did not finish")


def test_progress_is_visible_during_analysis_and_completion_returns_dashboard(client):
    entered, release = threading.Event(), threading.Event()
    service = client.app.state.chat_service
    original = service.agent.adapter.step

    def delayed(prompt):
        entered.set()
        assert release.wait(5)
        return original(prompt)

    service.agent.adapter.step = delayed
    request = {"revision": 0, "message": "Собери дашборд продаж", "request_id": "job-progress-1"}
    started = client.post("/api/chat/jobs", json=request)
    assert started.status_code == 202
    ident = started.json()["id"]
    try:
        assert entered.wait(3)
        job = client.get("/api/chat/jobs/" + ident).json()
        assert job["status"] == "running" and job["progress"]["phase"] == "analysis"
        assert job["progress"]["total"] is None
        assert client.get("/api/chat/jobs/active").json()["job"]["id"] == ident
        assert client.post("/api/chat/jobs", json=request).json()["id"] == ident
        # Another browser shares the running server, not a second startup/crash recovery.
        other = TestClient(client.app)
        try:
            other.get("/")
            assert other.get("/api/chat/jobs/" + ident).status_code == 400
        finally:
            other.close()
    finally:
        release.set()
    complete = wait(client, ident)
    assert complete["status"] == "done" and complete["progress"]["steps"] >= 2
    assert complete["progress"]["phase"] == "done"
    assert len(complete["result"]["messages"][-1]["dashboard"]["charts"]) == 4
    assert client.get("/api/chat/jobs/active").json()["job"] is None


def test_job_failure_retains_last_valid_state(client):
    before = client.get("/api/state").json()

    def fail(*args, **kwargs):
        raise ValueError("Проверяемая ошибка модели")

    client.app.state.chat_service.process = fail
    started = client.post(
        "/api/chat/jobs", json={"revision": 0, "message": "test", "request_id": "failed-job"}
    ).json()
    result = wait(client, started["id"])
    assert result["status"] == "error" and result["error"] == "Проверяемая ошибка модели"
    assert client.get("/api/state").json()["revision"] == before["revision"]


def test_sequential_jobs_use_latest_state_instead_of_losing_queued_messages(client):
    for i in range(3):
        started = client.post(
            "/api/chat/jobs", json={"revision": 0, "message": "Привет", "request_id": "greeting-" + str(i)}
        ).json()
        assert wait(client, started["id"])["status"] == "done"
    state = client.get("/api/state").json()
    assert len(state["messages"]) == 6 and state["revision"] == 3
