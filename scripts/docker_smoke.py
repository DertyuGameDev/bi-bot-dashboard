"""Verify the running Compose stack against its actual PostgreSQL source."""

import argparse
import http.cookiejar
import json
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:18080")
    parser.add_argument("--model-adapter", default="demo")
    args = parser.parse_args()
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, body=None):
        req = urllib.request.Request(
            args.url.rstrip("/") + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Requested-With": "puzzle-rpa"},
        )
        with client.open(req, timeout=120) as response:
            return response.read()

    health = json.loads(request("/api/health"))
    assert health["ok"] and health["source"] == "postgres"
    assert health["model_adapter"] == args.model_adapter
    request("/")
    state = json.loads(request("/api/state"))
    job = json.loads(
        request(
            "/api/chat/jobs",
            {"message": "Собери дашборд по продажам за 2025 год", "revision": state["revision"]},
        )
    )
    deadline = time.monotonic() + 300
    while job["status"] not in {"done", "error"} and time.monotonic() < deadline:
        time.sleep(0.5)
        job = json.loads(request("/api/chat/jobs/" + job["id"]))
    assert job["status"] == "done", job.get("error") or "Analysis timeout"
    state = json.loads(request("/api/state"))
    reply = state["messages"][-1]
    assert state["dataset"]["rows"] > 0
    assert reply.get("dashboard") and 1 <= len(reply["dashboard"]["charts"]) <= 4
    assert all(c.get("plotly", {}).get("data") for c in reply["dashboard"]["charts"])
    assert state["saved_id"]
    ident = reply["id"]
    table = json.loads(request(f"/api/cards/{ident}/table"))
    assert table["dataset"]["rows"] == state["dataset"]["rows"]
    assert table["dataset"]["preview"]
    with client.open(
        args.url.rstrip("/") + f"/api/download/html?message_id={ident}", timeout=120
    ) as response:
        exported = response.read()
        assert b"plotly" in exported and b"<!doctype html>" in exported.lower()
    print(
        json.dumps(
            {
                "ok": True,
                "source": health["source"],
                "model_adapter": health["model_adapter"],
                "rows": state["dataset"]["rows"],
                "charts": len(reply["dashboard"]["charts"]),
                "autosave": bool(state["saved_id"]),
                "table": True,
                "html_export": True,
            }
        )
    )


if __name__ == "__main__":
    main()
