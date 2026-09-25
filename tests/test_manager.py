import asyncio
from datetime import datetime, timezone

import pytest

from log_viewer.config import ConfigStore
from log_viewer.incidents import IncidentStore
from log_viewer.manager import SourceManager
from log_viewer.models import FilterGroup, SourceConfig, SourceStatus
from log_viewer.storage import EventStore


@pytest.fixture
def manager_parts(tmp_path):
    config = ConfigStore(tmp_path / "config.json")
    store = EventStore(tmp_path / "cache", 10_000_000)
    manager = SourceManager(config, store)
    yield config, store, manager
    store.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_starts_missing_source_and_stops_cleanly(manager_parts, tmp_path):
    config, _, manager = manager_parts
    source = SourceConfig(name="missing", path=str(tmp_path / "missing.log"), poll_interval_ms=100)
    config.update(lambda cfg: cfg.sources.append(source))
    await manager.start_all()
    await asyncio.sleep(0.05)
    assert manager.get_statuses()[0].state == "missing"
    await manager.stop_all()
    assert manager.tasks == {}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_publishes_event_to_subscriber_and_store(manager_parts, event_factory):
    _, store, manager = manager_parts
    stream = manager.subscribe(["source-a"])
    pending = asyncio.create_task(anext(stream))
    event = event_factory()
    await manager._on_events([event], True)
    received = await asyncio.wait_for(pending, 1)
    assert received.id == event.id
    assert store.count() == 1
    await stream.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_applies_configured_rolling_source_limit(manager_parts, event_factory):
    config, store, manager = manager_parts
    source = SourceConfig(id="source-a", name="a", path="/unused", history_events=2)
    config.update(lambda cfg: cfg.sources.append(source))

    await manager._on_events(
        [
            event_factory(id=f"event-{index}", sequence=index, message=f"event-{index}")
            for index in range(3)
        ],
        True,
    )

    assert [event.message for event in store.query(["source-a"], FilterGroup())] == [
        "event-1",
        "event-2",
    ]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_transition_preserves_last_event_metadata(manager_parts, event_factory):
    _, _, manager = manager_parts
    await manager._on_status(SourceStatus(source_id="source-a", state="running", timestamp_seen=False))
    await manager._on_events([event_factory()], True)
    await manager._on_status(SourceStatus(source_id="source-a", state="error", detail="lost"))
    status = manager.statuses["source-a"]
    assert status.state == "error"
    assert status.timestamp_seen is True
    assert status.last_event_at is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sync_sources_starts_and_removes_tasks(manager_parts, tmp_path):
    config, _, manager = manager_parts
    source = SourceConfig(name="x", path=str(tmp_path / "none"), enabled=True, poll_interval_ms=100)
    config.update(lambda cfg: cfg.sources.append(source))
    await manager.sync_sources()
    assert source.id in manager.tasks
    config.update(lambda cfg: setattr(cfg, "sources", []))
    await manager.sync_sources()
    assert source.id not in manager.tasks


@pytest.mark.integration
@pytest.mark.asyncio
async def test_manager_records_incident_at_the_shared_ingestion_point(tmp_path, event_factory):
    config = ConfigStore(tmp_path / "config.json")
    event_store = EventStore(tmp_path / "cache", 10_000_000)
    incident_store = IncidentStore(tmp_path / "incidents.sqlite3")
    source = SourceConfig(id="source-a", name="API service", path="/unused")
    config.update(lambda cfg: cfg.sources.append(source))
    manager = SourceManager(config, event_store, incident_store)
    now = datetime.now(timezone.utc)
    event = event_factory(
        display_timestamp=now,
        original_timestamp=now,
        received_at=now,
        level="ERROR",
        message="database unavailable",
        raw="database unavailable",
    )
    try:
        await manager._on_events([event], True)
        incident = incident_store.list_for_sources(["source-a"], 24, now=now)[0]
        assert incident.source_name == "API service"
        assert incident.message == "database unavailable"
    finally:
        incident_store.close()
        event_store.close()
