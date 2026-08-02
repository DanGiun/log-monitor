from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import LogEvent, ParserConfig, ParserKind


DEFAULT_TIMESTAMP_RE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
SYSLOG_TIMESTAMP_RE = re.compile(
    r"^(?P<timestamp>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
)
LEVEL_RE = re.compile(
    r"(?:^|\s|\[)(TRACE|DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|CRITICAL|FATAL)(?:\]|\s|$)",
    re.IGNORECASE,
)


def _parse_datetime(value: str, explicit_format: str | None = None) -> datetime:
    cleaned = value.strip().replace(",", ".")
    if explicit_format:
        result = datetime.strptime(cleaned, explicit_format)
    else:
        iso = cleaned.replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(iso)
        except ValueError:
            result = datetime.strptime(f"{datetime.now().year} {cleaned}", "%Y %b %d %H:%M:%S")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def _nested_get(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


@dataclass
class ParsedLine:
    timestamp: datetime | None
    level: str | None
    message: str
    fields: dict[str, Any]
    warning: str | None = None


class LogParser:
    def __init__(self, config: ParserConfig) -> None:
        self.config = config
        self._custom_re = re.compile(config.timestamp_regex) if config.timestamp_regex else None

    def parse_line(self, line: str) -> ParsedLine:
        stripped = line.rstrip("\r\n")
        kind = self.config.kind
        if kind in (ParserKind.JSONL, ParserKind.AUTO) and stripped.lstrip().startswith("{"):
            try:
                return self._parse_json(stripped)
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                if kind == ParserKind.JSONL:
                    return ParsedLine(None, None, stripped, {}, f"invalid JSON line: {exc}")
        return self._parse_text(stripped)

    def _parse_json(self, line: str) -> ParsedLine:
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError("JSON line must be an object")
        timestamp = None
        warning = None
        for field in self.config.timestamp_fields:
            value = _nested_get(data, field)
            if value is not None:
                try:
                    timestamp = _parse_datetime(str(value), self.config.timestamp_format)
                except ValueError as exc:
                    warning = f"invalid timestamp in {field}: {exc}"
                break
        level = None
        for field in self.config.level_fields:
            value = _nested_get(data, field)
            if value is not None:
                level = str(value).upper()
                break
        message = str(data.get("message") or data.get("msg") or line)
        return ParsedLine(timestamp, level, message, data, warning)

    def _parse_text(self, line: str) -> ParsedLine:
        match = None
        if self._custom_re:
            match = self._custom_re.search(line)
            if match and "timestamp" not in match.groupdict():
                return ParsedLine(None, self._extract_level(line), line, {}, "custom regex lacks named group 'timestamp'")
        if not match:
            match = DEFAULT_TIMESTAMP_RE.search(line)
        if not match and self.config.kind in (ParserKind.AUTO, ParserKind.SYSLOG):
            match = SYSLOG_TIMESTAMP_RE.search(line)
        timestamp = None
        warning = None
        if match:
            try:
                timestamp = _parse_datetime(match.group("timestamp"), self.config.timestamp_format)
            except ValueError as exc:
                warning = f"invalid timestamp: {exc}"
        return ParsedLine(timestamp, self._extract_level(line), line, {}, warning)

    @staticmethod
    def _extract_level(line: str) -> str | None:
        match = LEVEL_RE.search(line)
        if not match:
            return None
        value = match.group(1).upper()
        return "WARNING" if value == "WARN" else value


class EventAssembler:
    """Combines continuation lines into logical events."""

    def __init__(self, source_id: str, config: ParserConfig, offset_hours: int = 0) -> None:
        self.source_id = source_id
        self.parser = LogParser(config)
        self.offset = timedelta(hours=offset_hours)
        self.current: LogEvent | None = None
        self.sequence = 0
        self.timestamp_seen = False

    def feed(self, line: str) -> list[LogEvent]:
        parsed = self.parser.parse_line(line)
        emitted: list[LogEvent] = []
        if parsed.timestamp is not None:
            self.timestamp_seen = True
            if self.current is not None:
                emitted.append(self.current)
            self.sequence += 1
            self.current = LogEvent(
                source_id=self.source_id,
                original_timestamp=parsed.timestamp,
                display_timestamp=parsed.timestamp + self.offset,
                sequence=self.sequence,
                level=parsed.level,
                message=parsed.message,
                raw=line.rstrip("\r\n"),
                fields=parsed.fields,
                parse_warning=parsed.warning,
            )
        elif self.current is not None:
            continuation = line.rstrip("\r\n")
            self.current.raw += "\n" + continuation
            self.current.message += "\n" + continuation
            if parsed.warning and not self.current.parse_warning:
                self.current.parse_warning = parsed.warning
        else:
            self.sequence += 1
            emitted.append(
                LogEvent(
                    source_id=self.source_id,
                    sequence=self.sequence,
                    level=parsed.level,
                    message=parsed.message,
                    raw=line.rstrip("\r\n"),
                    fields=parsed.fields,
                    parse_warning=parsed.warning,
                )
            )
        return emitted

    def flush(self) -> list[LogEvent]:
        if self.current is None:
            return []
        event = self.current
        self.current = None
        return [event]


def parse_lines(
    lines: list[str], source_id: str, config: ParserConfig, offset_hours: int = 0
) -> tuple[list[LogEvent], bool]:
    assembler = EventAssembler(source_id, config, offset_hours)
    events: list[LogEvent] = []
    for line in lines:
        events.extend(assembler.feed(line))
    events.extend(assembler.flush())
    return events, assembler.timestamp_seen
