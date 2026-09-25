from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from uuid import uuid4

from .aggregation import normalize_message
from .models import IncidentRecord, IncidentSettings, LogEvent


SIMILARITY_THRESHOLD = 0.70
CORRELATION_WINDOW_SECONDS = 10
WHITESPACE_RE = re.compile(r"\s+")


def incident_timestamp(event: LogEvent) -> datetime:
    value = event.display_timestamp or event.original_timestamp or event.received_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_incident_message(message: str) -> str:
    normalized = normalize_message(message).casefold()
    return WHITESPACE_RE.sub(" ", normalized).strip()


def message_similarity(left: str, right: str) -> float:
    if left == right:
        return 1.0
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def classify_incident(
    event: LogEvent, settings: IncidentSettings
) -> tuple[str, str | None] | None:
    if (event.level or "").upper() == "ERROR":
        return "error", None
    message = event.message.casefold()
    for keyword in settings.keywords:
        if keyword.casefold() in message:
            return "keyword", keyword
    return None


def occurrence_key(event: LogEvent) -> str:
    stamp = incident_timestamp(event).isoformat()
    material = "\0".join(
        (event.source_id, stamp, str(event.sequence), event.level or "", event.raw)
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class IncidentStore:
    """Persistent, retention-controlled incident groups."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()
        os.chmod(self.path, 0o600)

    def _initialize(self) -> None:
        with self._connection:
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    level TEXT,
                    message TEXT NOT NULL,
                    normalized_message TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    match_kind TEXT NOT NULL CHECK(match_kind IN ('error', 'keyword')),
                    matched_keyword TEXT
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incidents_source_last
                ON incidents(source_id, last_seen DESC)
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incident_occurrences (
                    occurrence_key TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY(incident_id) REFERENCES incidents(id) ON DELETE CASCADE
                )
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incident_occurrences_incident
                ON incident_occurrences(incident_id)
                """
            )

    @staticmethod
    def _cutoff(retention_hours: int, now: datetime) -> datetime:
        return now.astimezone(timezone.utc) - timedelta(hours=retention_hours)

    def _cleanup_locked(self, retention_hours: int, now: datetime) -> int:
        cursor = self._connection.execute(
            "DELETE FROM incidents WHERE last_seen < ?",
            (self._cutoff(retention_hours, now).isoformat(),),
        )
        deleted = max(0, cursor.rowcount)
        if deleted:
            self._connection.execute("PRAGMA incremental_vacuum")
        return deleted

    def cleanup(self, retention_hours: int, now: datetime | None = None) -> int:
        current = now or datetime.now(timezone.utc)
        with self._lock, self._connection:
            return self._cleanup_locked(retention_hours, current)

    def record_many(
        self,
        events: Iterable[LogEvent],
        source_names: Mapping[str, str],
        settings: IncidentSettings,
        now: datetime | None = None,
    ) -> list[IncidentRecord]:
        current = now or datetime.now(timezone.utc)
        recorded: list[IncidentRecord] = []
        with self._lock, self._connection:
            self._cleanup_locked(settings.retention_hours, current)
            cutoff = self._cutoff(settings.retention_hours, current)
            for event in events:
                incident = self._record_locked(
                    event,
                    source_names.get(event.source_id, event.source_id),
                    settings,
                    cutoff,
                )
                if incident is not None:
                    recorded.append(incident)
        return recorded

    def record(
        self,
        event: LogEvent,
        source_name: str,
        settings: IncidentSettings,
        now: datetime | None = None,
    ) -> IncidentRecord | None:
        recorded = self.record_many(
            [event], {event.source_id: source_name}, settings, now=now
        )
        return recorded[0] if recorded else None

    def _record_locked(
        self,
        event: LogEvent,
        source_name: str,
        settings: IncidentSettings,
        retention_cutoff: datetime,
    ) -> IncidentRecord | None:
        classification = classify_incident(event, settings)
        if classification is None:
            return None
        occurred_at = incident_timestamp(event)
        if occurred_at < retention_cutoff:
            return None
        replay_key = occurrence_key(event)
        replay = self._connection.execute(
            "SELECT incident_id FROM incident_occurrences WHERE occurrence_key = ?",
            (replay_key,),
        ).fetchone()
        if replay is not None:
            return None

        match_kind, matched_keyword = classification
        normalized = normalize_incident_message(event.message)
        window_start = occurred_at - timedelta(seconds=CORRELATION_WINDOW_SECONDS)
        candidates = self._connection.execute(
            """
            SELECT * FROM incidents
            WHERE source_id = ? AND last_seen >= ?
            ORDER BY last_seen DESC, id DESC
            LIMIT 200
            """,
            (event.source_id, window_start.isoformat()),
        ).fetchall()
        best: sqlite3.Row | None = None
        best_score = 0.0
        for candidate in candidates:
            candidate_last = datetime.fromisoformat(candidate["last_seen"])
            if abs((candidate_last - occurred_at).total_seconds()) > CORRELATION_WINDOW_SECONDS:
                continue
            score = message_similarity(normalized, candidate["normalized_message"])
            if score >= SIMILARITY_THRESHOLD and score > best_score:
                best = candidate
                best_score = score

        if best is None:
            incident_id = uuid4().hex
            self._connection.execute(
                """
                INSERT INTO incidents(
                    id, source_id, source_name, level, message, normalized_message,
                    first_seen, last_seen, count, match_kind, matched_keyword
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    incident_id,
                    event.source_id,
                    source_name,
                    event.level,
                    event.message,
                    normalized,
                    occurred_at.isoformat(),
                    occurred_at.isoformat(),
                    match_kind,
                    matched_keyword,
                ),
            )
        else:
            incident_id = best["id"]
            first_seen = min(datetime.fromisoformat(best["first_seen"]), occurred_at)
            last_seen = max(datetime.fromisoformat(best["last_seen"]), occurred_at)
            promoted_to_error = best["match_kind"] == "error" or match_kind == "error"
            self._connection.execute(
                """
                UPDATE incidents
                SET source_name = ?, level = ?, first_seen = ?, last_seen = ?,
                    count = count + 1, match_kind = ?, matched_keyword = ?
                WHERE id = ?
                """,
                (
                    source_name,
                    "ERROR" if promoted_to_error else (best["level"] or event.level),
                    first_seen.isoformat(),
                    last_seen.isoformat(),
                    "error" if promoted_to_error else "keyword",
                    None if promoted_to_error else (best["matched_keyword"] or matched_keyword),
                    incident_id,
                ),
            )

        self._connection.execute(
            """
            INSERT INTO incident_occurrences(occurrence_key, incident_id, occurred_at)
            VALUES (?, ?, ?)
            """,
            (replay_key, incident_id, occurred_at.isoformat()),
        )
        row = self._connection.execute(
            "SELECT * FROM incidents WHERE id = ?", (incident_id,)
        ).fetchone()
        return self._row_to_record(row)

    def list_for_sources(
        self,
        source_ids: list[str],
        retention_hours: int,
        limit: int = 1000,
        now: datetime | None = None,
    ) -> list[IncidentRecord]:
        if not source_ids:
            return []
        current = now or datetime.now(timezone.utc)
        placeholders = ",".join("?" for _ in source_ids)
        with self._lock, self._connection:
            self._cleanup_locked(retention_hours, current)
            rows = self._connection.execute(
                f"""
                SELECT * FROM incidents
                WHERE source_id IN ({placeholders})
                ORDER BY last_seen DESC, first_seen DESC, id DESC
                LIMIT ?
                """,
                [*source_ids, limit],
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM incidents").fetchone()[0])

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> IncidentRecord:
        return IncidentRecord(
            id=row["id"],
            source_id=row["source_id"],
            source_name=row["source_name"],
            level=row["level"],
            message=row["message"],
            first_seen=datetime.fromisoformat(row["first_seen"]),
            last_seen=datetime.fromisoformat(row["last_seen"]),
            count=row["count"],
            match_kind=row["match_kind"],
            matched_keyword=row["matched_keyword"],
        )

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
