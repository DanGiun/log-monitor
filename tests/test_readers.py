
import pytest

from log_viewer.models import SourceConfig
from log_viewer.readers import LocalFileFollower, tail_utf8_lines


@pytest.mark.unit
def test_tail_utf8_lines_reads_last_region(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("\n".join(f"line-{i}" for i in range(2000)), encoding="utf-8")
    lines = tail_utf8_lines(path, 30)
    assert lines[-1] == "line-1999"
    assert len(lines) >= 30


@pytest.mark.integration
@pytest.mark.asyncio
async def test_missing_file_is_nonfatal_and_reports_status(tmp_path):
    statuses = []
    async def on_events(events, seen): pass
    async def on_status(status): statuses.append(status)
    source = SourceConfig(name="missing", path=str(tmp_path / "none.log"), poll_interval_ms=100)
    follower = LocalFileFollower(source, on_events, on_status)
    await follower.initialize()
    assert statuses[-1].state == "missing"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_initial_history_returns_last_logical_events(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("\n".join(f"2026-07-10 10:00:{i:02d} INFO event-{i}" for i in range(10)), encoding="utf-8")
    received = []
    async def on_events(events, seen): received.extend(events)
    async def on_status(status): pass
    source = SourceConfig(name="a", path=str(path), history_events=3)
    follower = LocalFileFollower(source, on_events, on_status)
    await follower.initialize()
    assert [item.message.rsplit(" ",1)[-1] for item in received] == ["event-7", "event-8", "event-9"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_append_is_read_in_real_time(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("2026-07-10 10:00:00 INFO first\n", encoding="utf-8")
    received = []
    async def on_events(events, seen): received.extend(events)
    async def on_status(status): pass
    follower = LocalFileFollower(SourceConfig(name="a", path=str(path)), on_events, on_status)
    await follower.initialize()
    path.write_text(path.read_text() + "2026-07-10 10:00:01 INFO second\n", encoding="utf-8")
    await follower.poll_once()
    await follower.poll_once()
    assert any("second" in item.message for item in received)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_multiline_event_can_span_poll_cycles(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("", encoding="utf-8")
    received = []

    async def on_events(events, seen):
        received.extend(events)

    async def on_status(status):
        pass

    follower = LocalFileFollower(SourceConfig(name="a", path=str(path)), on_events, on_status)
    await follower.initialize()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("2026-07-10 10:00:01 ERROR failed\n")
    await follower.poll_once()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("stack frame: worker.py:22\n")
    await follower.poll_once()
    await follower.poll_once()
    assert len(received) == 1
    assert received[0].message.endswith("stack frame: worker.py:22")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_partial_continuation_is_not_flushed_during_idle_poll(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("", encoding="utf-8")
    received = []

    async def on_events(events, seen):
        received.extend(events)

    async def on_status(status):
        pass

    follower = LocalFileFollower(SourceConfig(name="a", path=str(path)), on_events, on_status)
    await follower.initialize()
    with path.open("a", encoding="utf-8") as handle:
        handle.write("2026-07-10 10:00:01 ERROR failed\nstack fra")
    await follower.poll_once()
    await follower.poll_once()
    assert received == []
    with path.open("a", encoding="utf-8") as handle:
        handle.write("me: worker.py:22\n")
    await follower.poll_once()
    await follower.poll_once()
    assert len(received) == 1
    assert received[0].message.endswith("stack frame: worker.py:22")


@pytest.mark.unit
def test_tail_does_not_fail_when_window_starts_inside_utf8_character(tmp_path):
    path = tmp_path / "utf8.log"
    path.write_text((("я" * 40) + "\n") * 3000, encoding="utf-8")
    lines = tail_utf8_lines(path, 1)
    assert lines
    assert all(set(line) == {"я"} for line in lines)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_truncation_restarts_from_beginning_and_reports_detail(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("2026-07-10 10:00:00 INFO " + "x"*200 + "\n", encoding="utf-8")
    statuses, received = [], []
    async def on_events(events, seen): received.extend(events)
    async def on_status(status): statuses.append(status)
    follower = LocalFileFollower(SourceConfig(name="a", path=str(path)), on_events, on_status)
    await follower.initialize()
    path.write_text("2026-07-10 10:01:00 INFO new\n", encoding="utf-8")
    await follower.poll_once()
    await follower.poll_once()
    assert any("truncated" in item.detail for item in statuses)
    assert any("new" in item.message for item in received)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_rotation_switches_to_replacement_file(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("2026-07-10 10:00:00 INFO old\n", encoding="utf-8")
    statuses, received = [], []
    async def on_events(events, seen): received.extend(events)
    async def on_status(status): statuses.append(status)
    follower = LocalFileFollower(SourceConfig(name="a", path=str(path)), on_events, on_status)
    await follower.initialize()
    rotated = tmp_path / "a.log.1"
    path.replace(rotated)
    path.write_text("2026-07-10 10:01:00 INFO replacement\n", encoding="utf-8")
    await follower.poll_once()
    await follower.poll_once()
    assert any("rotation" in item.detail.lower() for item in statuses)
    assert any("replacement" in item.message for item in received)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_rotation_drains_remaining_old_inode_before_replacement(tmp_path):
    path = tmp_path / "a.log"
    path.write_text("2026-07-10 10:00:00 INFO initial\n", encoding="utf-8")
    received = []

    async def on_events(events, seen):
        received.extend(events)

    async def on_status(status):
        pass

    follower = LocalFileFollower(SourceConfig(name="a", path=str(path)), on_events, on_status)
    await follower.initialize()
    rotated = tmp_path / "a.log.1"
    path.replace(rotated)
    with rotated.open("a", encoding="utf-8") as handle:
        handle.write("2026-07-10 10:00:01 INFO old-tail\n")
    path.write_text("2026-07-10 10:00:02 INFO replacement\n", encoding="utf-8")
    await follower.poll_once()
    await follower.poll_once()
    messages = [event.message for event in received]
    assert any("old-tail" in message for message in messages)
    assert any("replacement" in message for message in messages)
