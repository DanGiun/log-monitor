from __future__ import annotations

from datetime import datetime, timezone

import pytest

from log_viewer.models import LogEvent


@pytest.fixture
def event_factory():
    def make(**overrides):
        values = {
            "source_id": "source-a",
            "original_timestamp": datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
            "display_timestamp": datetime(2026, 7, 10, 14, 0, tzinfo=timezone.utc),
            "level": "INFO",
            "message": "request completed id=123",
            "raw": "request completed id=123",
            "fields": {"request": {"id": 123}, "duration": 4.2},
        }
        values.update(overrides)
        return LogEvent(**values)
    return make
