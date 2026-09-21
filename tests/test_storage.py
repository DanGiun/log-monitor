from datetime import datetime, timezone

import pytest

from log_viewer.models import FilterCondition, FilterGroup
from log_viewer.storage import EventStore


@pytest.fixture
def store(tmp_path):
    item = EventStore(tmp_path / "cache", max_bytes=10_000_000)
    yield item
    item.close(cleanup=True)


@pytest.mark.integration
def test_insert_and_query_keeps_chronological_order(store, event_factory):
    later = event_factory(message="later", display_timestamp=datetime(2026,7,10,12,1,tzinfo=timezone.utc))
    earlier = event_factory(message="earlier", display_timestamp=datetime(2026,7,10,12,0,tzinfo=timezone.utc))
    store.insert_many([later, earlier])
    assert [item.message for item in store.query([], FilterGroup())] == ["earlier", "later"]


@pytest.mark.integration
def test_query_by_source_and_filter(store, event_factory):
    store.insert_many([event_factory(source_id="a", message="needle"), event_factory(source_id="b", message="other")])
    result = store.query(["a"], FilterGroup(conditions=[FilterCondition(value="needle")]))
    assert len(result) == 1
    assert result[0].source_id == "a"


@pytest.mark.integration
def test_timestamp_less_event_can_be_excluded(store, event_factory):
    store.insert(event_factory(display_timestamp=None, original_timestamp=None))
    assert store.query([], FilterGroup(), include_without_timestamp=False) == []


@pytest.mark.integration
def test_shift_recalculates_buffered_timestamps(store, event_factory):
    store.insert(event_factory(source_id="a"))
    store.shift_source("a", -2)
    result = store.query(["a"], FilterGroup())[0]
    assert result.original_timestamp.hour == 12
    assert result.display_timestamp.hour == 10
    assert result.display_timestamp.tzinfo is not None


@pytest.mark.integration
def test_delete_source_removes_only_its_events(store, event_factory):
    store.insert_many([event_factory(source_id="a"), event_factory(source_id="b")])
    store.delete_source("a")
    assert [item.source_id for item in store.query([], FilterGroup())] == ["b"]


@pytest.mark.integration
def test_logical_buffer_prunes_oldest_events(tmp_path, event_factory):
    store = EventStore(tmp_path / "small", max_bytes=2500)
    try:
        for index in range(20):
            store.insert(event_factory(message=f"event-{index}-" + "x" * 400))
        assert store.logical_bytes() <= store.max_bytes
        messages = [item.message for item in store.query([], FilterGroup(), limit=100)]
        assert all("event-0-" not in item for item in messages)
    finally:
        store.close()


@pytest.mark.integration
def test_cache_permissions_are_private(store):
    assert oct(store.cache_dir.stat().st_mode & 0o777) == "0o700"
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"


@pytest.mark.integration
def test_selective_filter_scans_past_initial_prefetch(store, event_factory):
    events = [event_factory(id="needle", sequence=0, message="needle")]
    events.extend(
        event_factory(id=f"noise-{index}", sequence=index, message="noise")
        for index in range(1, 6001)
    )
    store.insert_many(events)
    result = store.query(
        [], FilterGroup(conditions=[FilterCondition(value="needle")]), limit=1000
    )
    assert [item.message for item in result] == ["needle"]


@pytest.mark.integration
def test_delete_latest_preserves_older_source_events(store, event_factory):
    store.insert_many(
        event_factory(id=f"event-{index}", sequence=index, message=f"event-{index}")
        for index in range(5)
    )
    store.delete_latest("source-a", 2)
    assert [item.message for item in store.query(["source-a"], FilterGroup())] == [
        "event-0",
        "event-1",
        "event-2",
    ]
