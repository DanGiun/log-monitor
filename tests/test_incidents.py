from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from log_viewer.incidents import (
    CORRELATION_WINDOW_SECONDS,
    IncidentStore,
    classify_incident,
    message_similarity,
    normalize_incident_message,
)
from log_viewer.models import IncidentSettings, LogEvent


def make_event(
    *,
    source_id: str = "source-a",
    level: str = "ERROR",
    message: str = "Request failed for order 123",
    timestamp: datetime,
    sequence: int = 1,
) -> LogEvent:
    return LogEvent(
        source_id=source_id,
        display_timestamp=timestamp,
        received_at=timestamp,
        level=level,
        message=message,
        raw=message,
        sequence=sequence,
    )


@pytest.mark.unit
def test_error_and_case_insensitive_keyword_are_classified():
    settings = IncidentSettings(keywords=["Reject"])
    stamp = datetime.now(timezone.utc)
    assert classify_incident(
        make_event(level="ERROR", message="plain", timestamp=stamp), settings
    ) == ("error", None)
    assert classify_incident(
        make_event(level="INFO", message="Order was REJECTED", timestamp=stamp), settings
    ) == ("keyword", "Reject")
    assert classify_incident(
        make_event(level="INFO", message="completed", timestamp=stamp), settings
    ) is None


@pytest.mark.unit
def test_normalization_removes_dynamic_values_before_similarity():
    left = normalize_incident_message("Failed id=123 host=8.8.8.8 token=0xdead")
    right = normalize_incident_message("Failed id=987 host=1.1.1.1 token=0xbeef")
    assert left == right
    assert message_similarity(left, right) == 1.0
    assert message_similarity("abcdef", "abcxyz") < 0.70


@pytest.mark.integration
def test_non_incident_is_not_stored(tmp_path):
    store = IncidentStore(tmp_path / "incidents.sqlite3")
    now = datetime.now(timezone.utc)
    try:
        result = store.record(
            make_event(level="INFO", message="healthy", timestamp=now),
            "Application",
            IncidentSettings(keywords=["Reject"]),
            now,
        )
        assert result is None
        assert store.count() == 0
    finally:
        store.close()


@pytest.mark.integration
def test_similar_burst_is_grouped_and_frequency_is_preserved(tmp_path):
    store = IncidentStore(tmp_path / "incidents.sqlite3")
    now = datetime.now(timezone.utc)
    settings = IncidentSettings()
    try:
        events = [
            make_event(
                message=f"Request failed for order {index} from 10.0.0.{index % 250}",
                timestamp=now + timedelta(milliseconds=index * 20),
                sequence=index,
            )
            for index in range(100)
        ]
        store.record_many(events, {"source-a": "Application"}, settings, now + timedelta(seconds=3))
        incidents = store.list_for_sources(["source-a"], 24, now=now + timedelta(seconds=3))
        assert len(incidents) == 1
        assert incidents[0].count == 100
        assert incidents[0].first_seen == now
        assert incidents[0].last_seen == now + timedelta(milliseconds=1980)
    finally:
        store.close()


@pytest.mark.integration
def test_groups_do_not_cross_sources_or_correlation_window(tmp_path):
    store = IncidentStore(tmp_path / "incidents.sqlite3")
    now = datetime.now(timezone.utc)
    settings = IncidentSettings()
    try:
        events = [
            make_event(timestamp=now, sequence=1),
            make_event(source_id="source-b", timestamp=now + timedelta(seconds=1), sequence=2),
            make_event(
                timestamp=now + timedelta(seconds=CORRELATION_WINDOW_SECONDS + 1),
                sequence=3,
            ),
        ]
        store.record_many(
            events,
            {"source-a": "A", "source-b": "B"},
            settings,
            now + timedelta(seconds=12),
        )
        assert len(store.list_for_sources(["source-a"], 24, now=now + timedelta(seconds=12))) == 2
        assert len(store.list_for_sources(["source-b"], 24, now=now + timedelta(seconds=12))) == 1
    finally:
        store.close()


@pytest.mark.integration
def test_replayed_occurrence_does_not_increment_group(tmp_path):
    store = IncidentStore(tmp_path / "incidents.sqlite3")
    now = datetime.now(timezone.utc)
    event = make_event(timestamp=now)
    try:
        assert store.record(event, "A", IncidentSettings(), now) is not None
        assert store.record(event, "A", IncidentSettings(), now) is None
        assert store.list_for_sources(["source-a"], 24, now=now)[0].count == 1
    finally:
        store.close()


@pytest.mark.integration
def test_retention_is_counted_from_last_occurrence(tmp_path):
    store = IncidentStore(tmp_path / "incidents.sqlite3")
    now = datetime.now(timezone.utc)
    try:
        first = make_event(timestamp=now - timedelta(hours=1, seconds=5), sequence=1)
        last = make_event(timestamp=now - timedelta(minutes=59, seconds=56), sequence=2)
        store.record(first, "A", IncidentSettings(retention_hours=3), now)
        store.record(last, "A", IncidentSettings(retention_hours=3), now)
        assert store.cleanup(1, now) == 0
        assert store.count() == 1
        assert store.cleanup(1, now + timedelta(seconds=5)) == 1
        assert store.count() == 0
    finally:
        store.close()


@pytest.mark.integration
def test_store_survives_reopen_and_filters_sources(tmp_path):
    path = tmp_path / "config" / "incidents.sqlite3"
    now = datetime.now(timezone.utc)
    store = IncidentStore(path)
    store.record(make_event(source_id="a", timestamp=now), "A", IncidentSettings(), now)
    store.record(
        make_event(source_id="b", timestamp=now, sequence=2),
        "B",
        IncidentSettings(),
        now,
    )
    store.close()

    reopened = IncidentStore(path)
    try:
        incidents = reopened.list_for_sources(["a"], 24, now=now)
        assert [item.source_name for item in incidents] == ["A"]
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
    finally:
        reopened.close()
