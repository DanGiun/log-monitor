from __future__ import annotations

import asyncio
import codecs
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, BinaryIO, Callable, Protocol

from .models import LogEvent, SourceConfig, SourceStatus
from .parser import EventAssembler, parse_lines


EventCallback = Callable[[list[LogEvent], bool], Awaitable[None]]
StatusCallback = Callable[[SourceStatus], Awaitable[None]]


class Reader(Protocol):
    async def run(self, stop_event: asyncio.Event) -> None: ...


def tail_utf8_lines(path: Path, event_hint: int) -> list[str]:
    """Read a bounded tail; over-read physical lines to support multiline events."""
    physical_hint = max(event_hint * 20, 1000)
    block_size = 64 * 1024
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        data = bytearray()
        newline_count = 0
        while position > 0 and newline_count <= physical_hint:
            size = min(block_size, position)
            position -= size
            handle.seek(position)
            chunk = handle.read(size)
            data[:0] = chunk
            newline_count += chunk.count(b"\n")
        return bytes(data).decode("utf-8").splitlines()


@dataclass
class LocalFileFollower:
    source: SourceConfig
    on_events: EventCallback
    on_status: StatusCallback

    def __post_init__(self) -> None:
        self.path = Path(self.source.path).expanduser()
        self.offset = 0
        self.inode: tuple[int, int] | None = None
        self.handle: BinaryIO | None = None
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.partial = ""
        self.assembler = EventAssembler(
            self.source.id, self.source.parser, self.source.timezone_offset_hours
        )
        self.initialized = False

    async def _status(self, state: str, detail: str = "") -> None:
        await self.on_status(
            SourceStatus(
                source_id=self.source.id,
                state=state,  # type: ignore[arg-type]
                detail=detail,
                timestamp_seen=self.assembler.timestamp_seen,
            )
        )

    def _close_handle(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def _open_current(self, offset: int) -> None:
        self._close_handle()
        self.handle = self.path.open("rb")
        stat = os.fstat(self.handle.fileno())
        self.inode = (stat.st_dev, stat.st_ino)
        self.handle.seek(offset)
        self.offset = self.handle.tell()
        self.initialized = True

    async def _consume(self, payload: bytes) -> None:
        if not payload:
            return
        text = self.decoder.decode(payload)
        text = self.partial + text
        if text.endswith("\n") or text.endswith("\r"):
            lines = text.splitlines()
            self.partial = ""
        else:
            pieces = text.splitlines()
            self.partial = pieces.pop() if pieces else text
            lines = pieces
        events: list[LogEvent] = []
        for line in lines:
            events.extend(self.assembler.feed(line))
        events.extend(self.assembler.flush())
        if events:
            await self.on_events(events, self.assembler.timestamp_seen)

    async def initialize(self) -> None:
        if not self.path.exists():
            await self._status("missing", f"File does not exist: {self.path}")
            return
        if not self.path.is_file():
            await self._status("error", f"Path is not a regular file: {self.path}")
            return
        try:
            lines = await asyncio.to_thread(tail_utf8_lines, self.path, self.source.history_events)
            events, seen = parse_lines(
                lines, self.source.id, self.source.parser, self.source.timezone_offset_hours
            )
            events = events[-self.source.history_events :]
            self.assembler.timestamp_seen = seen
            self.assembler.sequence = max((event.sequence for event in events), default=0)
            self._open_current(self.path.stat().st_size)
            if events:
                await self.on_events(events, seen)
            await self._status("running")
        except UnicodeDecodeError as exc:
            await self._status("error", f"File is not valid UTF-8: {exc}")
        except OSError as exc:
            await self._status("error", str(exc))

    async def poll_once(self) -> None:
        try:
            if not self.initialized or self.handle is None:
                await self.initialize()
                return

            if not self.path.exists():
                # A renamed/unlinked file remains readable through the open descriptor.
                payload = self.handle.read()
                self.offset = self.handle.tell()
                await self._consume(payload)
                await self._status("missing", f"File does not exist: {self.path}")
                return

            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            detail = ""
            if identity != self.inode:
                # Finish the old inode before switching to the replacement path.
                old_payload = self.handle.read()
                self.offset = self.handle.tell()
                await self._consume(old_payload)
                self.decoder.reset()
                self.partial = ""
                self.assembler = EventAssembler(
                    self.source.id, self.source.parser, self.source.timezone_offset_hours
                )
                self._open_current(0)
                detail = "Log rotation detected; finished old file and switched to replacement file"
            elif stat.st_size < self.offset:
                self.handle.seek(0)
                self.offset = 0
                self.decoder.reset()
                self.partial = ""
                detail = "File was truncated; reading from the beginning"

            payload = self.handle.read()
            self.offset = self.handle.tell()
            await self._consume(payload)
            await self._status("running", detail)
        except UnicodeDecodeError as exc:
            await self._status("error", f"File is not valid UTF-8: {exc}")
        except OSError as exc:
            self.initialized = False
            self._close_handle()
            await self._status("error", str(exc))

    async def run(self, stop_event: asyncio.Event) -> None:
        await self.initialize()
        try:
            while not stop_event.is_set():
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.source.poll_interval_ms / 1000
                    )
                except asyncio.TimeoutError:
                    await self.poll_once()
        finally:
            self._close_handle()
        await self._status("stopped")


class SSHFileFollower:
    """SFTP follower using strict system known_hosts verification and key/agent auth."""

    def __init__(
        self,
        source: SourceConfig,
        on_events: EventCallback,
        on_status: StatusCallback,
    ) -> None:
        self.source = source
        self.on_events = on_events
        self.on_status = on_status
        self.offset = 0
        self.signature: tuple[int, int] | None = None
        self.assembler = EventAssembler(source.id, source.parser, source.timezone_offset_hours)
        self.partial = ""
        self.initial_batch = False
        self.initial_started_mid_file = False
        self._client = None
        self._sftp = None

    async def _set_status(self, state: str, detail: str = "") -> None:
        await self.on_status(
            SourceStatus(
                source_id=self.source.id,
                state=state,  # type: ignore[arg-type]
                detail=detail,
                timestamp_seen=self.assembler.timestamp_seen,
            )
        )

    def _connect(self) -> None:
        try:
            import paramiko
        except ImportError as exc:
            raise RuntimeError("SSH support requires the 'paramiko' package") from exc
        assert self.source.ssh is not None
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=self.source.ssh.host,
            port=self.source.ssh.port,
            username=self.source.ssh.username,
            key_filename=self.source.ssh.key_path,
            allow_agent=True,
            look_for_keys=self.source.ssh.key_path is None,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
        )
        self._client = client
        self._sftp = client.open_sftp()

    def _close(self) -> None:
        if self._sftp:
            self._sftp.close()
        if self._client:
            self._client.close()
        self._sftp = None
        self._client = None

    def _read_new(self) -> tuple[bytes, str]:
        assert self._sftp is not None
        attrs = self._sftp.stat(self.source.path)
        signature = (int(getattr(attrs, "st_ino", 0)), int(attrs.st_mtime))
        detail = ""
        self.initial_batch = self.signature is None
        if self.signature is None:
            self.offset = max(0, int(attrs.st_size) - 4 * 1024 * 1024)
            self.initial_started_mid_file = self.offset > 0
        elif int(attrs.st_size) < self.offset:
            self.offset = 0
            detail = "Remote file was truncated; reading from the beginning"
        elif signature[0] and self.signature[0] and signature[0] != self.signature[0]:
            self.offset = 0
            detail = "Remote rotation detected; switched to replacement file"
        self.signature = signature
        if int(attrs.st_size) <= self.offset:
            return b"", detail
        with self._sftp.open(self.source.path, "rb") as handle:
            handle.seek(self.offset)
            data = handle.read()
            self.offset += len(data)
            return data, detail

    async def run(self, stop_event: asyncio.Event) -> None:
        backoffs = [1, 2, 5, 10, 30]
        attempt = 0
        while not stop_event.is_set():
            try:
                if self._sftp is None:
                    await self._set_status("connecting")
                    await asyncio.to_thread(self._connect)
                    attempt = 0
                payload, detail = await asyncio.to_thread(self._read_new)
                if payload and self.initial_batch:
                    lines = payload.decode("utf-8").splitlines()
                    if self.initial_started_mid_file and lines:
                        lines = lines[1:]
                    events, seen = parse_lines(
                        lines,
                        self.source.id,
                        self.source.parser,
                        self.source.timezone_offset_hours,
                    )
                    events = events[-self.source.history_events :]
                    self.assembler.timestamp_seen = seen
                    self.assembler.sequence = max(
                        (event.sequence for event in events), default=0
                    )
                    if events:
                        await self.on_events(events, seen)
                    self.initial_batch = False
                elif payload:
                    text = self.partial + payload.decode("utf-8")
                    if text.endswith("\n") or text.endswith("\r"):
                        lines, self.partial = text.splitlines(), ""
                    else:
                        lines = text.splitlines()
                        self.partial = lines.pop() if lines else text
                    events: list[LogEvent] = []
                    for line in lines:
                        events.extend(self.assembler.feed(line))
                    events.extend(self.assembler.flush())
                    if events:
                        await self.on_events(events, self.assembler.timestamp_seen)
                elif self.initial_batch:
                    self.initial_batch = False
                await self._set_status("running", detail)
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=self.source.poll_interval_ms / 1000
                    )
                except asyncio.TimeoutError:
                    pass
            except FileNotFoundError:
                await self._set_status("missing", f"Remote file does not exist: {self.source.path}")
                await asyncio.sleep(1)
            except Exception as exc:
                self._close()
                delay = backoffs[min(attempt, len(backoffs) - 1)]
                attempt += 1
                await self._set_status("error", f"SSH/SFTP error: {exc}; retry in {delay}s")
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
        self._close()
        await self._set_status("stopped")
