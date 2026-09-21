
import pytest
import time
from fastapi.testclient import TestClient

from log_viewer.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "config.json", tmp_path / "cache")
    with TestClient(app) as test_client:
        yield test_client, app


@pytest.mark.acceptance
def test_health_and_root_are_available(client):
    test_client, _ = client
    assert test_client.get("/api/health").json()["status"] == "ok"
    assert "Log Viewer" in test_client.get("/").text


@pytest.mark.acceptance
def test_source_crud_does_not_require_existing_path(client):
    test_client, _ = client
    source = {"id":"s1","name":"Missing allowed","kind":"local","path":"/definitely/missing.log","color":"#6ea8fe","timezone_offset_hours":0,"history_events":30,"poll_interval_ms":500,"enabled":False,"parser":{"kind":"auto","timestamp_fields":["timestamp"],"level_fields":["level"]},"ssh":None}
    created = test_client.post("/api/sources", json=source)
    assert created.status_code == 201
    source["timezone_offset_hours"] = 2
    assert test_client.put("/api/sources/s1", json=source).status_code == 200
    assert test_client.delete("/api/sources/s1").status_code == 204


@pytest.mark.acceptance
def test_invalid_source_offset_returns_validation_error(client):
    test_client, _ = client
    source = {"name":"x","path":"/x","timezone_offset_hours":24}
    assert test_client.post("/api/sources", json=source).status_code == 422


@pytest.mark.acceptance
def test_query_and_aggregation_endpoints(client, event_factory):
    test_client, app = client
    app.state.event_store.insert_many([
        event_factory(source_id="a", level="INFO", message="ok"),
        event_factory(source_id="a", level="ERROR", message="failed id=12"),
    ])
    response = test_client.post("/api/query", json={"source_ids":["a"],"filter":{"logic":"AND","conditions":[{"field":"message","operator":"contains","value":"failed"}],"groups":[]},"limit":100})
    assert response.status_code == 200
    assert len(response.json()) == 1
    stats = test_client.post("/api/aggregations", json={"source_ids":["a"],"filter":{"logic":"AND","conditions":[],"groups":[]},"apply_filter":True,"interval":"minute","top_n":10})
    assert stats.json()["by_level"]["ERROR"] == 1


@pytest.mark.acceptance
def test_invalid_regex_is_reported_to_ui(client):
    test_client, _ = client
    response = test_client.post("/api/query", json={"filter":{"logic":"AND","conditions":[{"field":"message","operator":"regex","value":"["}],"groups":[]}})
    assert response.status_code == 422
    assert "regular expression" in response.json()["detail"]


@pytest.mark.acceptance
def test_workspace_lifecycle_and_default_protection(client):
    test_client, _ = client
    workspace = {"id":"qa","name":"QA","panels":[],"grid_columns":2}
    assert test_client.post("/api/workspaces", json=workspace).status_code == 201
    assert test_client.put("/api/active-workspace/qa").status_code == 200
    assert test_client.delete("/api/workspaces/default").status_code == 400
    assert test_client.delete("/api/workspaces/qa").status_code == 204


@pytest.mark.acceptance
def test_diagnostics_contains_no_log_content(client, event_factory):
    test_client, app = client
    app.state.event_store.insert(event_factory(message="SECRET LOG CONTENT"))
    response = test_client.get("/api/diagnostics")
    assert response.status_code == 200
    assert "SECRET LOG CONTENT" not in response.text
    assert "attachment" in response.headers["content-disposition"]


@pytest.mark.acceptance
def test_websocket_endpoint_accepts_connection(client):
    test_client, _ = client
    with test_client.websocket_connect("/ws?source_ids=none") as websocket:
        assert websocket is not None


@pytest.mark.acceptance
def test_websocket_delivers_new_local_file_event(client, tmp_path):
    test_client, _ = client
    settings = test_client.get("/api/config").json()["settings"]
    settings["sort_buffer_seconds"] = 0
    assert test_client.put("/api/settings", json=settings).status_code == 200
    path = tmp_path / "live.log"
    path.write_text("", encoding="utf-8")
    source = {
        "id": "live",
        "name": "live",
        "path": str(path),
        "history_events": 10,
        "poll_interval_ms": 100,
    }
    assert test_client.post("/api/sources", json=source).status_code == 201
    with test_client.websocket_connect("/ws?source_ids=live") as websocket:
        with path.open("a", encoding="utf-8") as handle:
            handle.write("2026-09-21 10:00:00 INFO streamed\n")
        event = websocket.receive_json()
    assert event["source_id"] == "live"
    assert event["message"].endswith("INFO streamed")


@pytest.mark.acceptance
def test_invalid_timestamp_filter_returns_422(client, event_factory):
    test_client, app = client
    app.state.event_store.insert(event_factory())
    response = test_client.post(
        "/api/query",
        json={
            "filter": {
                "logic": "AND",
                "conditions": [
                    {"field": "timestamp", "operator": "gte", "value": "not-a-date"}
                ],
                "groups": [],
            }
        },
    )
    assert response.status_code == 422
    assert "timestamp value" in response.json()["detail"]


@pytest.mark.acceptance
def test_invalid_saved_filter_is_rejected(client):
    test_client, _ = client
    response = test_client.post(
        "/api/filters",
        json={
            "id": "bad",
            "name": "bad",
            "filter": {
                "logic": "AND",
                "conditions": [{"field": "message", "operator": "regex", "value": "["}],
                "groups": [],
            },
        },
    )
    assert response.status_code == 422


@pytest.mark.acceptance
def test_stop_start_does_not_duplicate_initial_history(client, tmp_path):
    test_client, _ = client
    path = tmp_path / "restart.log"
    path.write_text(
        "2026-09-21 10:00:00 INFO one\n"
        "2026-09-21 10:00:01 INFO two\n"
        "2026-09-21 10:00:02 INFO three\n",
        encoding="utf-8",
    )
    source = {
        "id": "restart",
        "name": "restart",
        "path": str(path),
        "history_events": 3,
    }
    assert test_client.post("/api/sources", json=source).status_code == 201
    time.sleep(0.05)
    assert test_client.get("/api/health").json()["events"] == 3
    assert test_client.post("/api/sources/restart/stop").status_code == 200
    assert test_client.post("/api/sources/restart/start").status_code == 200
    time.sleep(0.05)
    assert test_client.get("/api/health").json()["events"] == 3
