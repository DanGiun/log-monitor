from datetime import datetime, timedelta, timezone

import pytest

from log_viewer.aggregation import aggregate, normalize_message


@pytest.mark.unit
def test_normalization_masks_dynamic_values():
    message = "client 10.1.2.3 id=123 ptr=0x55bf764c66f0 uuid=123e4567-e89b-12d3-a456-426614174000"
    normalized = normalize_message(message)
    assert "<IP>" in normalized
    assert "<NUM>" in normalized
    assert "<HEX>" in normalized
    assert "<UUID>" in normalized


@pytest.mark.unit
def test_aggregation_counts_levels_sources_and_errors(event_factory):
    events = [
        event_factory(level="INFO", source_id="a", message="ok 1"),
        event_factory(level="ERROR", source_id="a", message="failed id=2"),
        event_factory(level="WARNING", source_id="b", message="failed id=3"),
    ]
    result = aggregate(events)
    assert result["total"] == 3
    assert result["by_level"] == {"INFO": 1, "ERROR": 1, "WARNING": 1}
    assert result["by_source"] == {"a": 2, "b": 1}
    assert result["top_errors"][0]["count"] == 2


@pytest.mark.unit
@pytest.mark.parametrize("interval,expected_suffix", [("second", "14:00:00+00:00"), ("minute", "14:00:00+00:00"), ("hour", "14:00:00+00:00")])
def test_timeline_buckets(event_factory, interval, expected_suffix):
    result = aggregate([event_factory()], interval=interval)
    assert next(iter(result["timeline"])).endswith(expected_suffix)


@pytest.mark.unit
def test_repeated_message_has_first_and_last(event_factory):
    first = datetime(2026, 7, 10, 10, tzinfo=timezone.utc)
    second = first + timedelta(minutes=1)
    result = aggregate([
        event_factory(message="failure id=10", display_timestamp=first),
        event_factory(message="failure id=11", display_timestamp=second),
    ])
    item = result["top_normalized"][0]
    assert item["count"] == 2
    assert item["first"] == first.isoformat()
    assert item["last"] == second.isoformat()
