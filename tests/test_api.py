import json
import threading
import time

import pytest

from agents.llm import Reply


class AnswersZero:
    model = "fixed"

    def complete(self, messages, tools, system):
        return Reply(text="0", tokens=1)


@pytest.fixture
def client(make_client):
    return make_client()


def drain(client, job_id, last_event_id=None):
    headers = {"last-event-id": str(last_event_id)} if last_event_id is not None else {}
    events = []
    with client.stream("GET", f"/api/jobs/{job_id}/events", headers=headers) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if events[-1]["type"] == "end":
                    break
    return events


def test_meta_describes_the_catalog(client):
    meta = client.get("/api/meta").json()
    assert meta["llm_available"] is False
    assert [c["name"] for c in meta["fault_configs"]][0] == "none"
    assert {a["id"] for a in meta["architectures"]} >= {"single", "retry", "critic", "oracle", "oracle_retry"}
    assert len(meta["tasks"]) == 6 and meta["tasks"][0]["checks"]
    assert meta["fault_types"]["rate_limit"]["phase"] == "request"


def test_playground_trial_streams_and_is_stored(client):
    r = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "oracle",
                                        "fault_config": "stale_data", "seed": 5})
    assert r.status_code == 202
    events = drain(client, r.json()["id"])
    types = [e["type"] for e in events]
    assert types[0] == "start" and types[-2:] == ["result", "end"] and "event" in types
    tool_result = next(e["event"] for e in events if e["type"] == "event" and e["event"]["kind"] == "tool_result")
    assert tool_result["payload"]["fault"] == "stale_data"

    result = events[-2]["result"]
    assert result["outcome"] == "fail_wrong_answer" and result["seed"] == 5
    trial = client.get(f"/api/trials/{result['trial_id']}").json()
    assert trial["sweep_label"] == "playground" and trial["checks"][0]["passed"] is False
    assert [e["kind"] for e in trial["events"]] == ["tool_call", "tool_result"]
    assert client.get("/api/playground/runs").json()[0]["id"] == result["trial_id"]

    replay = drain(client, r.json()["id"], last_event_id=events[-3]["seq"])
    assert [e["type"] for e in replay] == ["result", "end"]


def test_rejects_llm_architectures_without_a_key(client):
    r = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "single"})
    assert r.status_code == 400 and "GEMINI_API_KEY" in r.json()["detail"]


def test_one_job_at_a_time(client):
    gate = threading.Event()
    client.app.state.jobs.start("sweep", "blocker", {"label": "blocker"}, lambda job: gate.wait(5))
    try:
        r = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "oracle"})
        assert r.status_code == 409 and r.json()["job"]["title"] == "blocker"
        assert client.get("/api/jobs/current").json()["status"] == "running"
    finally:
        gate.set()


def test_sweep_lifecycle(client):
    body = {"label": "web-1", "archs": ["oracle", "oracle_retry"], "configs": ["none", "rate_limit"],
            "tasks": ["t1_stock_lookup", "t5_tier_quote"], "repeats": 1, "pace": 0}
    r = client.post("/api/sweeps", json=body)
    assert r.status_code == 202
    events = drain(client, r.json()["id"])
    progress = [e for e in events if e["type"] == "progress"]
    assert len(progress) == 8 and events[-1]["status"] == "done"

    listed = {s["label"]: s for s in client.get("/api/sweeps").json()}
    assert "playground" not in listed
    assert (listed["web-1"]["planned"], listed["web-1"]["trials"], listed["web-1"]["live"]) == (8, 8, False)

    summary = client.get("/api/sweeps/web-1").json()
    assert summary["archs"] == ["oracle", "oracle_retry"] and summary["params"]["repeats"] == 1
    none_cells = [c for c in summary["cells"] if c["config"] == "none"]
    assert all(c["passes"] == c["n"] == 2 for c in none_cells)

    passed = client.get("/api/sweeps/web-1/trials", params={"outcome": "pass", "config": "none"}).json()
    assert len(passed) == 4
    assert client.get("/api/sweeps/nope").status_code == 404


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_sweep_can_be_stopped(make_client):
    # TestClient buffers streamed responses, so this test polls the job instead
    client = make_client(llm_available=True, backend_factory=AnswersZero)
    body = {"label": "stoppable", "archs": ["single"], "configs": ["none"],
            "tasks": ["t1_stock_lookup", "t5_tier_quote"], "repeats": 1, "pace": 30}
    job_id = client.post("/api/sweeps", json=body).json()["id"]

    def job():
        return client.get(f"/api/jobs/{job_id}").json()

    wait_for(lambda: (job()["progress"] or {}).get("done") == 1)
    started = time.monotonic()
    client.post(f"/api/jobs/{job_id}/stop")
    final = wait_for(lambda: job() if job()["status"] != "running" else None)
    assert final["status"] == "stopped" and final["progress"]["done"] == 1
    assert time.monotonic() - started < 5, "stop should interrupt the pacing wait"
    assert client.get("/api/sweeps/stoppable").json()["status"] == "stopped"


@pytest.mark.parametrize("label", ["playground", "bad label", ""])
def test_sweep_labels_are_validated(client, label):
    body = {"label": label, "archs": ["oracle"], "configs": ["none"], "tasks": ["t1_stock_lookup"]}
    assert client.post("/api/sweeps", json=body).status_code == 400


def test_serves_the_built_ui(make_client, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    client = make_client(static_dir=dist)
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/sweeps/web-1").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/nope").status_code == 404


def test_reports_an_unbuilt_ui(client):
    assert client.get("/").status_code == 503


def test_optional_login(make_client):
    client = make_client(login=("demo", "s3cret"))
    denied = client.get("/api/meta")
    assert denied.status_code == 401 and denied.headers["www-authenticate"].startswith("Basic")
    assert client.get("/api/meta", auth=("demo", "wrong")).status_code == 401
    assert client.get("/api/meta", headers={"authorization": "Basic not-base64!"}).status_code == 401
    assert client.get("/api/health").status_code == 200  # platform health checks stay open
    assert client.get("/api/meta", auth=("demo", "s3cret")).status_code == 200

    job = client.post("/api/trials", json={"task_id": "t1_stock_lookup", "architecture_id": "oracle"},
                      auth=("demo", "s3cret")).json()
    with client.stream("GET", f"/api/jobs/{job['id']}/events", auth=("demo", "s3cret")) as r:
        assert r.status_code == 200
        assert any('"type": "end"' in line for line in r.iter_lines())


def test_login_is_off_without_a_password(make_client):
    assert make_client(login=("demo", "")).get("/api/meta").status_code == 200
