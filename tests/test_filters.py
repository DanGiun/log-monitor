
import pytest

from log_viewer.filters import FilterValidationError, matches_filter, validate_filter
from log_viewer.models import FilterCondition, FilterGroup


@pytest.mark.unit
@pytest.mark.parametrize(
    "condition,expected",
    [
        (FilterCondition(operator="contains", value="COMPLETED"), True),
        (FilterCondition(operator="contains", value="COMPLETED", case_sensitive=True), False),
        (FilterCondition(operator="not_contains", value="failed"), True),
        (FilterCondition(operator="regex", value=r"id=\d+"), True),
        (FilterCondition(operator="not_regex", value=r"failed|error"), True),
        (FilterCondition(field="level", operator="equals", value="info"), True),
        (FilterCondition(field="source", operator="not_equals", value="source-b"), True),
        (FilterCondition(field="json", json_path="request.id", operator="equals", value="123"), True),
        (FilterCondition(field="json", json_path="duration", operator="gte", value=4), True),
    ],
)
def test_filter_equivalence_partitions(event_factory, condition, expected):
    assert matches_filter(event_factory(), FilterGroup(conditions=[condition])) is expected


@pytest.mark.unit
def test_and_logic_requires_all_conditions(event_factory):
    group = FilterGroup(conditions=[
        FilterCondition(operator="contains", value="request"),
        FilterCondition(field="level", operator="equals", value="ERROR"),
    ])
    assert matches_filter(event_factory(), group) is False


@pytest.mark.unit
def test_or_logic_requires_any_condition(event_factory):
    group = FilterGroup(logic="OR", conditions=[
        FilterCondition(operator="contains", value="missing"),
        FilterCondition(field="level", operator="equals", value="INFO"),
    ])
    assert matches_filter(event_factory(), group) is True


@pytest.mark.unit
def test_not_negates_group(event_factory):
    group = FilterGroup(negate=True, conditions=[FilterCondition(operator="contains", value="request")])
    assert matches_filter(event_factory(), group) is False


@pytest.mark.unit
def test_nested_filter_group(event_factory):
    child = FilterGroup(logic="OR", conditions=[
        FilterCondition(field="level", operator="equals", value="ERROR"),
        FilterCondition(operator="contains", value="completed"),
    ])
    root = FilterGroup(conditions=[FilterCondition(field="source", operator="equals", value="source-a")], groups=[child])
    assert matches_filter(event_factory(), root) is True


@pytest.mark.unit
def test_invalid_regex_rejected_before_execution():
    group = FilterGroup(conditions=[FilterCondition(operator="regex", value="[")])
    with pytest.raises(FilterValidationError):
        validate_filter(group)


@pytest.mark.unit
def test_timestamp_boundaries_are_inclusive(event_factory):
    lower = FilterCondition(field="timestamp", operator="gte", value="2026-07-10T14:00:00+00:00")
    upper = FilterCondition(field="timestamp", operator="lte", value="2026-07-10T14:00:00+00:00")
    assert matches_filter(event_factory(), FilterGroup(conditions=[lower, upper]))


@pytest.mark.unit
def test_invalid_timestamp_boundary_is_rejected_before_execution():
    group = FilterGroup(
        conditions=[FilterCondition(field="timestamp", operator="gte", value="not-a-date")]
    )
    with pytest.raises(FilterValidationError, match="invalid timestamp value"):
        validate_filter(group)
