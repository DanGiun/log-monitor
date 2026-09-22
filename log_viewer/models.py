from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class SourceKind(str, Enum):
    LOCAL = "local"
    SSH = "ssh"


class ParserKind(str, Enum):
    AUTO = "auto"
    TEXT = "text"
    JSONL = "jsonl"
    SYSLOG = "syslog"


class ParserConfig(BaseModel):
    kind: ParserKind = ParserKind.AUTO
    timestamp_regex: str | None = None
    timestamp_format: str | None = None
    timestamp_fields: list[str] = Field(
        default_factory=lambda: ["timestamp", "time", "datetime", "date", "@timestamp"]
    )
    level_fields: list[str] = Field(default_factory=lambda: ["level", "severity", "loglevel"])

    @field_validator("timestamp_regex")
    @classmethod
    def valid_timestamp_regex(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            re.compile(value)
        except re.error as exc:
            raise ValueError(f"invalid timestamp regular expression: {exc}") from exc
        return value


class SSHConfig(BaseModel):
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str
    key_path: str | None = None


class SourceConfig(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str = Field(min_length=1, max_length=200)
    kind: SourceKind = SourceKind.LOCAL
    path: str = Field(min_length=1)
    color: str = "#6ea8fe"
    timezone_offset_hours: int = Field(default=0, ge=-23, le=23)
    history_events: int = Field(default=30, ge=1, le=100_000)
    poll_interval_ms: int = Field(default=500, ge=100, le=10_000)
    enabled: bool = True
    parser: ParserConfig = Field(default_factory=ParserConfig)
    ssh: SSHConfig | None = None

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("color must be a #RRGGBB value")
        int(value[1:], 16)
        return value.lower()

    @model_validator(mode="after")
    def validate_kind_specific(self) -> "SourceConfig":
        if self.kind == SourceKind.SSH and self.ssh is None:
            raise ValueError("ssh configuration is required for SSH sources")
        return self


class FilterCondition(BaseModel):
    field: Literal["message", "level", "source", "json", "timestamp"] = "message"
    operator: Literal[
        "contains",
        "not_contains",
        "regex",
        "not_regex",
        "equals",
        "not_equals",
        "gte",
        "lte",
    ] = "contains"
    value: Any = ""
    json_path: str | None = None
    case_sensitive: bool = False


class FilterGroup(BaseModel):
    logic: Literal["AND", "OR"] = "AND"
    negate: bool = False
    conditions: list[FilterCondition] = Field(default_factory=list)
    groups: list["FilterGroup"] = Field(default_factory=list)


class SavedFilter(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    filter: FilterGroup


class PanelConfig(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    title: str = "Log panel"
    source_ids: list[str] = Field(default_factory=list)
    filter: FilterGroup = Field(default_factory=FilterGroup)
    aggregation_after_filter: bool = True
    paused: bool = False
    stopped: bool = False
    wrap_lines: bool = False
    autoscroll: bool = True
    order: int = 0
    min_height: int = Field(default=320, ge=160, le=2000)


class Workspace(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    name: str
    panels: list[PanelConfig] = Field(default_factory=list)
    grid_columns: int = Field(default=2, ge=1, le=4)


class AppSettings(BaseModel):
    disk_buffer_bytes: int = Field(
        default=5 * 1024**3,
        ge=100 * 1024**2,
        le=20 * 1024**3,
    )
    sort_buffer_seconds: float = Field(default=2.0, ge=0.0, le=10.0)
    open_browser: bool = True
    diagnostics_max_bytes: int = Field(default=20 * 1024**2, ge=1 * 1024**2, le=200 * 1024**2)


class AppConfig(BaseModel):
    version: int = 1
    settings: AppSettings = Field(default_factory=AppSettings)
    sources: list[SourceConfig] = Field(default_factory=list)
    workspaces: list[Workspace] = Field(
        default_factory=lambda: [Workspace(id="default", name="default")]
    )
    saved_filters: list[SavedFilter] = Field(default_factory=list)
    active_workspace_id: str = "default"

    @model_validator(mode="after")
    def ensure_default_workspace(self) -> "AppConfig":
        if not any(item.id == "default" for item in self.workspaces):
            self.workspaces.insert(0, Workspace(id="default", name="default"))
        if not any(item.id == self.active_workspace_id for item in self.workspaces):
            self.active_workspace_id = "default"
        return self


class LogEvent(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    source_id: str
    original_timestamp: datetime | None = None
    display_timestamp: datetime | None = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = 0
    level: str | None = None
    message: str
    raw: str
    fields: dict[str, Any] = Field(default_factory=dict)
    parse_warning: str | None = None


class SourceStatus(BaseModel):
    source_id: str
    state: Literal["starting", "running", "missing", "error", "stopped", "connecting"] = "starting"
    detail: str = ""
    last_event_at: datetime | None = None
    parse_errors: int = 0
    timestamp_seen: bool = False
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QueryRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    filter: FilterGroup = Field(default_factory=FilterGroup)
    limit: int = Field(default=1000, ge=1, le=100_000)
    before_timestamp: datetime | None = None
    include_without_timestamp: bool = True


class AggregationRequest(BaseModel):
    source_ids: list[str] = Field(default_factory=list)
    filter: FilterGroup = Field(default_factory=FilterGroup)
    apply_filter: bool = True
    interval: Literal["second", "minute", "hour"] = "minute"
    top_n: int = Field(default=10, ge=1, le=100)


class DiagnosticReport(BaseModel):
    version: str
    generated_at: datetime
    cache_bytes: int
    source_statuses: list[SourceStatus]
    source_count: int
    workspace_count: int
    settings: AppSettings
