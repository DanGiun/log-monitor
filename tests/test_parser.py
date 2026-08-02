from datetime import datetime, timezone

import pytest

from log_viewer.models import ParserConfig, ParserKind
from log_viewer.parser import EventAssembler, LogParser, parse_lines


@pytest.mark.unit
@pytest.mark.parametrize(
    "line,expected_level",
    [
        ("2026-07-10 15:58:57.655 INFO  component: started", "INFO"),
        ("2026-07-10 15:58:57.655 DEBUG component: building", "DEBUG"),
        ("2026-07-06 07:50:42.607 [WARNING] tll: deprecated", "WARNING"),
        ("2026-07-06 07:50:42.607 [ERROR] tll: failed", "ERROR"),
    ],
)
def test_parses_required_text_formats(line, expected_level):
    parsed = LogParser(ParserConfig()).parse_line(line)
    assert parsed.timestamp == datetime(2026, 7, 10 if "07-10" in line else 6, 15 if "07-10" in line else 7, 58 if "07-10" in line else 50, 57 if "07-10" in line else 42, 655000 if "07-10" in line else 607000, tzinfo=timezone.utc)
    assert parsed.level == expected_level


@pytest.mark.unit
def test_multiline_continuation_belongs_to_previous_event():
    lines = [
        "2026-07-10 15:58:57.655 INFO instrument registered",
        "        minstep=0.010000, limits=[0.01;10]",
        "",
        "2026-07-10 15:58:58.000 DEBUG next event",
    ]
    events, seen = parse_lines(lines, "s", ParserConfig())
    assert seen is True
    assert len(events) == 2
    assert "minstep" in events[0].message
    assert events[0].message.endswith("\n")


@pytest.mark.unit
def test_lines_before_first_timestamp_stay_in_arrival_order():
    events, seen = parse_lines(["startup banner", "2026-07-10 15:00:00 INFO ready"], "s", ParserConfig())
    assert seen is True
    assert events[0].display_timestamp is None
    assert events[0].message == "startup banner"


@pytest.mark.unit
def test_file_without_any_timestamp_is_not_timestamp_eligible():
    events, seen = parse_lines(["one", "two"], "s", ParserConfig())
    assert seen is False
    assert all(event.display_timestamp is None for event in events)


@pytest.mark.unit
def test_integer_timezone_shift_changes_display_only():
    events, _ = parse_lines(["2026-07-10 13:00:00 INFO x"], "s", ParserConfig(), 2)
    assert events[0].original_timestamp.hour == 13
    assert events[0].display_timestamp.hour == 15


@pytest.mark.unit
def test_negative_timezone_shift_crosses_day_boundary():
    events, _ = parse_lines(["2026-07-10 01:00:00 INFO x"], "s", ParserConfig(), -2)
    assert events[0].display_timestamp.isoformat().startswith("2026-07-09T23:00:00")


@pytest.mark.unit
def test_json_lines_auto_detection():
    line = '{"timestamp":"2026-07-10T15:00:00Z","level":"error","message":"failed","request":{"id":4}}'
    parsed = LogParser(ParserConfig()).parse_line(line)
    assert parsed.timestamp.hour == 15
    assert parsed.level == "ERROR"
    assert parsed.message == "failed"
    assert parsed.fields["request"]["id"] == 4


@pytest.mark.unit
def test_json_nested_timestamp_field():
    config = ParserConfig(kind=ParserKind.JSONL, timestamp_fields=["meta.created"])
    parsed = LogParser(config).parse_line('{"meta":{"created":"2026-07-10 10:00:00"},"msg":"ok"}')
    assert parsed.timestamp.hour == 10


@pytest.mark.unit
def test_invalid_json_is_nonfatal_and_has_warning():
    parsed = LogParser(ParserConfig(kind=ParserKind.JSONL)).parse_line("{bad json}")
    assert parsed.timestamp is None
    assert "invalid JSON" in parsed.warning


@pytest.mark.unit
def test_custom_regex_and_strptime_format():
    config = ParserConfig(
        kind=ParserKind.TEXT,
        timestamp_regex=r"time=(?P<timestamp>\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2})",
        timestamp_format="%d/%m/%Y %H:%M:%S",
    )
    parsed = LogParser(config).parse_line("INFO time=10/07/2026 15:01:02 message")
    assert parsed.timestamp == datetime(2026, 7, 10, 15, 1, 2, tzinfo=timezone.utc)


@pytest.mark.unit
def test_custom_regex_without_named_timestamp_is_reported_not_crash():
    parsed = LogParser(ParserConfig(timestamp_regex=r"\d{4}")).parse_line("2026 something")
    assert parsed.timestamp is None
    assert "named group" in parsed.warning


@pytest.mark.unit
def test_syslog_timestamp_is_supported():
    parsed = LogParser(ParserConfig(kind=ParserKind.SYSLOG)).parse_line("Jul 10 15:01:02 host service[1]: ready")
    assert parsed.timestamp.month == 7
    assert parsed.timestamp.day == 10


@pytest.mark.unit
def test_event_assembler_emits_previous_when_new_timestamp_arrives():
    assembler = EventAssembler("s", ParserConfig())
    assert assembler.feed("2026-07-10 10:00:00 INFO first") == []
    emitted = assembler.feed("2026-07-10 10:00:01 INFO second")
    assert [item.message for item in emitted] == ["2026-07-10 10:00:00 INFO first"]
