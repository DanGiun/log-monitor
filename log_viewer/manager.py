from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import AsyncIterator

from .config import ConfigStore
from .models import LogEvent, SourceConfig, SourceKind, SourceStatus
from .readers import LocalFileFollower, SSHFileFollower
from .storage import EventStore


class SourceManager:
    def __init__(self, config_store: ConfigStore, event_store: EventStore) -> None:
        self.config_store = config_store
        self.event_store = event_store
        self.tasks: dict[str, asyncio.Task] = {}
        self.stop_events: dict[str, asyncio.Event] = {}
        self.statuses: dict[str, SourceStatus] = {}
        self.subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def start_all(self) -> None:
        for source in self.config_store.get().sources:
            if source.enabled:
                await self.start(source)

    async def stop_all(self) -> None:
        for event in self.stop_events.values():
            event.set()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.stop_events.clear()

    async def sync_sources(self) -> None:
        configured = {source.id: source for source in self.config_store.get().sources}
        for source_id in list(self.tasks):
            if source_id not in configured:
                await self.stop(source_id)
        for source in configured.values():
            if source.enabled and source.id not in self.tasks:
                await self.start(source)
            elif not source.enabled and source.id in self.tasks:
                await self.stop(source.id)

    async def start(self, source: SourceConfig) -> None:
        async with self._lock:
            if source.id in self.tasks and not self.tasks[source.id].done():
                return
            stop_event = asyncio.Event()
            self.stop_events[source.id] = stop_event
            if source.kind == SourceKind.LOCAL:
                reader = LocalFileFollower(source, self._on_events, self._on_status)
            else:
                reader = SSHFileFollower(source, self._on_events, self._on_status)
            task = asyncio.create_task(reader.run(stop_event), name=f"source:{source.id}")
            self.tasks[source.id] = task
            source_id = source.id
            task.add_done_callback(lambda _, sid=source_id: self.tasks.pop(sid, None))

    async def stop(self, source_id: str) -> None:
        event = self.stop_events.get(source_id)
        if event:
            event.set()
        task = self.tasks.get(source_id)
        if task:
            await asyncio.gather(task, return_exceptions=True)
        self.stop_events.pop(source_id, None)
        self.tasks.pop(source_id, None)

    async def restart(self, source: SourceConfig) -> None:
        await self.stop(source.id)
        if source.enabled:
            await self.start(source)

    async def _on_events(self, events: list[LogEvent], timestamp_seen: bool) -> None:
        await asyncio.to_thread(self.event_store.insert_many, events)
        for event in events:
            status = self.statuses.get(event.source_id)
            if status:
                status.last_event_at = datetime.now(timezone.utc)
                status.timestamp_seen = status.timestamp_seen or timestamp_seen
            for queue in list(self.subscribers.get(event.source_id, set())):
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                        queue.put_nowait(event)
                    except (asyncio.QueueEmpty, asyncio.QueueFull):
                        pass

    async def _on_status(self, status: SourceStatus) -> None:
        previous = self.statuses.get(status.source_id)
        if previous:
            status.last_event_at = previous.last_event_at
            status.parse_errors = previous.parse_errors
            status.timestamp_seen = status.timestamp_seen or previous.timestamp_seen
        status.updated_at = datetime.now(timezone.utc)
        self.statuses[status.source_id] = status

    def get_statuses(self) -> list[SourceStatus]:
        configured = self.config_store.get().sources
        result = []
        for source in configured:
            result.append(
                self.statuses.get(source.id)
                or SourceStatus(source_id=source.id, state="stopped" if not source.enabled else "starting")
            )
        return result

    async def subscribe(self, source_ids: list[str]) -> AsyncIterator[LogEvent]:
        queue: asyncio.Queue[LogEvent] = asyncio.Queue(maxsize=5000)
        for source_id in source_ids:
            self.subscribers[source_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            for source_id in source_ids:
                self.subscribers[source_id].discard(queue)
