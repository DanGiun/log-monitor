from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .filters import matches_filter, validate_filter
from .models import FilterGroup, LogEvent


class EventStore:
    """Ephemeral SQLite-backed event buffer with a configurable logical byte limit."""

    def __init__(self, cache_dir: Path, max_bytes: int) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.cache_dir, 0o700)
        self.path = self.cache_dir / "events.sqlite3"
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()
        os.chmod(self.path, 0o600)

    def _initialize(self) -> None:
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute("PRAGMA temp_store=FILE")
            self._connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    original_ts TEXT,
                    display_ts TEXT,
                    received_at TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    level TEXT,
                    message TEXT NOT NULL,
                    raw TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    parse_warning TEXT,
                    size_bytes INTEGER NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_time ON events(display_ts, received_at, sequence)"
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_source ON events(source_id, display_ts)"
            )
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )
            self._connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES ('logical_bytes', 0)"
            )

    @staticmethod
    def _size(event: LogEvent) -> int:
        return len(event.model_dump_json().encode("utf-8"))

    def insert(self, event: LogEvent) -> None:
        self.insert_many([event])

    def insert_many(self, events: Iterable[LogEvent]) -> None:
        rows = []
        total = 0
        for event in events:
            size = self._size(event)
            total += size
            rows.append(
                (
                    event.id,
                    event.source_id,
                    event.original_timestamp.isoformat() if event.original_timestamp else None,
                    event.display_timestamp.isoformat() if event.display_timestamp else None,
                    event.received_at.isoformat(),
                    event.sequence,
                    event.level,
                    event.message,
                    event.raw,
                    json.dumps(event.fields, ensure_ascii=False, default=str),
                    event.parse_warning,
                    size,
                )
            )
        if not rows:
            return
        with self._lock, self._connection:
            self._connection.executemany(
                """
                INSERT OR REPLACE INTO events(
                    id, source_id, original_ts, display_ts, received_at, sequence,
                    level, message, raw, fields_json, parse_warning, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            self._connection.execute(
                "UPDATE metadata SET value = value + ? WHERE key = 'logical_bytes'", (total,)
            )
            self._prune_locked()

    def _prune_locked(self) -> None:
        current = self.logical_bytes()
        target = int(self.max_bytes * 0.9)
        while current > self.max_bytes:
            victims = self._connection.execute(
                "SELECT id, size_bytes FROM events ORDER BY received_at ASC LIMIT 1000"
            ).fetchall()
            if not victims:
                break
            reclaimed = sum(int(row["size_bytes"]) for row in victims)
            self._connection.executemany("DELETE FROM events WHERE id = ?", [(row["id"],) for row in victims])
            self._connection.execute(
                "UPDATE metadata SET value = MAX(0, value - ?) WHERE key = 'logical_bytes'",
                (reclaimed,),
            )
            current -= reclaimed
            if current <= target:
                break
        self._connection.execute("PRAGMA incremental_vacuum(256)")

    def logical_bytes(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM metadata WHERE key = 'logical_bytes'"
        ).fetchone()
        return int(row[0]) if row else 0

    def physical_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(self.path) + suffix)
            if candidate.exists():
                total += candidate.stat().st_size
        return total

    def query(
        self,
        source_ids: list[str],
        filter_group: FilterGroup,
        limit: int = 1000,
        before_timestamp: datetime | None = None,
        include_without_timestamp: bool = True,
    ) -> list[LogEvent]:
        validate_filter(filter_group)
        clauses: list[str] = []
        params: list[object] = []
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            clauses.append(f"source_id IN ({placeholders})")
            params.extend(source_ids)
        if before_timestamp:
            clauses.append("display_ts < ?")
            params.append(before_timestamp.isoformat())
        if not include_without_timestamp:
            clauses.append("display_ts IS NOT NULL")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        fetch_limit = min(max(limit * 5, 1000), 100_000)
        sql = f"""
            SELECT * FROM events {where}
            ORDER BY
                CASE WHEN display_ts IS NULL THEN 1 ELSE 0 END,
                display_ts DESC,
                received_at DESC,
                sequence DESC,
                id DESC
            LIMIT ? OFFSET ?
        """
        filtered: list[LogEvent] = []
        offset = 0
        with self._lock:
            while len(filtered) < limit:
                rows = self._connection.execute(
                    sql, [*params, fetch_limit, offset]
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    event = self._row_to_event(row)
                    if matches_filter(event, filter_group):
                        filtered.append(event)
                        if len(filtered) == limit:
                            break
                if len(rows) < fetch_limit:
                    break
                offset += len(rows)
        filtered.sort(
            key=lambda event: (
                event.display_timestamp or event.received_at,
                event.received_at,
                event.sequence,
                event.id,
            )
        )
        return filtered

    def delete_latest(self, source_id: str, limit: int) -> None:
        """Remove the tail that a restarted reader is about to ingest again."""
        with self._lock, self._connection:
            victims = self._connection.execute(
                """
                SELECT id, size_bytes FROM events
                WHERE source_id = ?
                ORDER BY received_at DESC, sequence DESC, id DESC
                LIMIT ?
                """,
                (source_id, limit),
            ).fetchall()
            if not victims:
                return
            reclaimed = sum(int(row["size_bytes"]) for row in victims)
            self._connection.executemany(
                "DELETE FROM events WHERE id = ?", [(row["id"],) for row in victims]
            )
            self._connection.execute(
                "UPDATE metadata SET value = MAX(0, value - ?) WHERE key = 'logical_bytes'",
                (reclaimed,),
            )

    def shift_source(self, source_id: str, offset_hours: int) -> None:
        """Recalculate display timestamps for buffered events after offset changes."""
        with self._lock, self._connection:
            rows = self._connection.execute(
                "SELECT id, original_ts FROM events WHERE source_id = ? AND original_ts IS NOT NULL",
                (source_id,),
            ).fetchall()
            updates = [
                (
                    (datetime.fromisoformat(row["original_ts"]) + timedelta(hours=offset_hours)).isoformat(),
                    row["id"],
                )
                for row in rows
            ]
            self._connection.executemany(
                "UPDATE events SET display_ts = ? WHERE id = ?", updates
            )

    def delete_source(self, source_id: str) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM events WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            reclaimed = int(row[0]) if row else 0
            self._connection.execute("DELETE FROM events WHERE source_id = ?", (source_id,))
            self._connection.execute(
                "UPDATE metadata SET value = MAX(0, value - ?) WHERE key = 'logical_bytes'",
                (reclaimed,),
            )

    def count(self) -> int:
        with self._lock:
            return int(self._connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> LogEvent:
        return LogEvent(
            id=row["id"],
            source_id=row["source_id"],
            original_timestamp=datetime.fromisoformat(row["original_ts"]) if row["original_ts"] else None,
            display_timestamp=datetime.fromisoformat(row["display_ts"]) if row["display_ts"] else None,
            received_at=datetime.fromisoformat(row["received_at"]),
            sequence=row["sequence"],
            level=row["level"],
            message=row["message"],
            raw=row["raw"],
            fields=json.loads(row["fields_json"]),
            parse_warning=row["parse_warning"],
        )

    def close(self, cleanup: bool = True) -> None:
        with self._lock:
            try:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                self._connection.close()
        if cleanup:
            shutil.rmtree(self.cache_dir, ignore_errors=True)
