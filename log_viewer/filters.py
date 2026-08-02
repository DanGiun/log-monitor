from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .models import FilterCondition, FilterGroup, LogEvent


class FilterValidationError(ValueError):
    pass


def _json_value(fields: dict[str, Any], path: str | None) -> Any:
    if not path:
        return fields
    value: Any = fields
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def validate_filter(group: FilterGroup) -> None:
    for condition in group.conditions:
        if condition.operator in ("regex", "not_regex"):
            try:
                re.compile(str(condition.value))
            except re.error as exc:
                raise FilterValidationError(f"invalid regular expression: {exc}") from exc
    for child in group.groups:
        validate_filter(child)


def _condition_value(event: LogEvent, condition: FilterCondition) -> Any:
    if condition.field == "message":
        return event.message
    if condition.field == "level":
        return event.level or ""
    if condition.field == "source":
        return event.source_id
    if condition.field == "json":
        return _json_value(event.fields, condition.json_path)
    if condition.field == "timestamp":
        return event.display_timestamp
    return None


def _text_pair(actual: Any, expected: Any, case_sensitive: bool) -> tuple[str, str]:
    left, right = str(actual or ""), str(expected or "")
    if not case_sensitive:
        left, right = left.casefold(), right.casefold()
    return left, right


def matches_condition(event: LogEvent, condition: FilterCondition) -> bool:
    actual = _condition_value(event, condition)
    op = condition.operator
    expected = condition.value
    if op in ("contains", "not_contains", "equals", "not_equals"):
        left, right = _text_pair(actual, expected, condition.case_sensitive)
        if op == "contains":
            return right in left
        if op == "not_contains":
            return right not in left
        if op == "equals":
            return left == right
        return left != right
    if op in ("regex", "not_regex"):
        flags = 0 if condition.case_sensitive else re.IGNORECASE
        found = re.search(str(expected), str(actual or ""), flags) is not None
        return found if op == "regex" else not found
    if op in ("gte", "lte"):
        if isinstance(actual, datetime):
            target = expected if isinstance(expected, datetime) else datetime.fromisoformat(str(expected))
            if actual.tzinfo and target.tzinfo is None:
                target = target.replace(tzinfo=actual.tzinfo)
            return actual >= target if op == "gte" else actual <= target
        try:
            return float(actual) >= float(expected) if op == "gte" else float(actual) <= float(expected)
        except (TypeError, ValueError):
            return False
    return False


def matches_filter(event: LogEvent, group: FilterGroup) -> bool:
    values = [matches_condition(event, item) for item in group.conditions]
    values.extend(matches_filter(event, item) for item in group.groups)
    if not values:
        result = True
    elif group.logic == "AND":
        result = all(values)
    else:
        result = any(values)
    return not result if group.negate else result
