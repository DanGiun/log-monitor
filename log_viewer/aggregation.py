from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

from .models import LogEvent


UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,36}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
HEX_RE = re.compile(r"\b0x[0-9a-fA-F]+\b")
NUMBER_RE = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def normalize_message(message: str) -> str:
    value = UUID_RE.sub("<UUID>", message)
    value = IP_RE.sub("<IP>", value)
    value = HEX_RE.sub("<HEX>", value)
    return NUMBER_RE.sub("<NUM>", value)


def _bucket(timestamp: datetime, interval: str) -> str:
    if interval == "second":
        return timestamp.replace(microsecond=0).isoformat()
    if interval == "hour":
        return timestamp.replace(minute=0, second=0, microsecond=0).isoformat()
    return timestamp.replace(second=0, microsecond=0).isoformat()


def aggregate(events: Iterable[LogEvent], interval: str = "minute", top_n: int = 10) -> dict:
    materialized = list(events)
    by_level = Counter((event.level or "UNKNOWN") for event in materialized)
    by_source = Counter(event.source_id for event in materialized)
    timeline: Counter[str] = Counter()
    exact = Counter()
    normalized = Counter()
    first_last: dict[str, list[str | None]] = defaultdict(lambda: [None, None])
    for event in materialized:
        exact[event.message] += 1
        normalized[normalize_message(event.message)] += 1
        if event.display_timestamp:
            timeline[_bucket(event.display_timestamp, interval)] += 1
            key = normalize_message(event.message)
            stamp = event.display_timestamp.isoformat()
            if first_last[key][0] is None:
                first_last[key][0] = stamp
            first_last[key][1] = stamp
    errors = [
        event for event in materialized if (event.level or "").upper() in {"ERROR", "CRITICAL", "FATAL", "WARNING"}
    ]
    return {
        "total": len(materialized),
        "by_level": dict(by_level.most_common()),
        "by_source": dict(by_source.most_common()),
        "timeline": dict(sorted(timeline.items())),
        "top_exact": [{"message": key, "count": count} for key, count in exact.most_common(top_n)],
        "top_normalized": [
            {
                "message": key,
                "count": count,
                "first": first_last[key][0],
                "last": first_last[key][1],
            }
            for key, count in normalized.most_common(top_n)
        ],
        "top_errors": [
            {"message": key, "count": count}
            for key, count in Counter(normalize_message(item.message) for item in errors).most_common(top_n)
        ],
    }
