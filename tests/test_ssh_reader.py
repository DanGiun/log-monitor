import asyncio
import io
import sys
from types import SimpleNamespace

import pytest

from log_viewer.models import SSHConfig, SourceConfig, SourceKind
from log_viewer.readers import SSHFileFollower


class FakeRemoteFile(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


class FakeSFTP:
    def __init__(self, data=b"", size=None, inode=1, mtime=1):
        self.data = data
        self.size = len(data) if size is None else size
        self.inode = inode
        self.mtime = mtime
        self.closed = False
    def stat(self, path):
        return SimpleNamespace(st_size=self.size, st_ino=self.inode, st_mtime=self.mtime)
    def open(self, path, mode):
        return FakeRemoteFile(self.data)
    def close(self): self.closed = True


class FakeSSHClient:
    instance = None
    def __init__(self):
        FakeSSHClient.instance = self
        self.loaded = False
        self.policy = None
        self.kwargs = None
        self.closed = False
        self.sftp = FakeSFTP()
    def load_system_host_keys(self): self.loaded = True
    def set_missing_host_key_policy(self, policy): self.policy = policy
    def connect(self, **kwargs): self.kwargs = kwargs
    def open_sftp(self): return self.sftp
    def close(self): self.closed = True


class RejectPolicy:
    pass


def make_source():
    return SourceConfig(
        id="ssh1", name="remote", kind=SourceKind.SSH, path="/var/log/a.log",
        ssh=SSHConfig(host="server", username="qa", key_path="/keys/id"), poll_interval_ms=100,
    )


@pytest.mark.unit
def test_ssh_connect_loads_system_hosts_and_rejects_unknown(monkeypatch):
    fake_module = SimpleNamespace(SSHClient=FakeSSHClient, RejectPolicy=RejectPolicy)
    monkeypatch.setitem(sys.modules, "paramiko", fake_module)
    follower = SSHFileFollower(make_source(), None, None)
    follower._connect()
    client = FakeSSHClient.instance
    assert client.loaded is True
    assert isinstance(client.policy, RejectPolicy)
    assert client.kwargs["key_filename"] == "/keys/id"
    assert client.kwargs["allow_agent"] is True
    assert client.kwargs["look_for_keys"] is False
    follower._close()
    assert client.closed is True


@pytest.mark.unit
def test_ssh_read_detects_truncation_and_rotation():
    follower = SSHFileFollower(make_source(), None, None)
    follower._sftp = FakeSFTP(b"abc", size=3, inode=1, mtime=1)
    payload, detail = follower._read_new()
    assert payload == b"abc" and detail == ""
    follower.offset = 20
    follower._sftp = FakeSFTP(b"new", size=3, inode=1, mtime=2)
    payload, detail = follower._read_new()
    assert "truncated" in detail
    follower.offset = 0
    follower.signature = (1, 2)
    follower._sftp = FakeSFTP(b"rotated", size=7, inode=2, mtime=3)
    payload, detail = follower._read_new()
    assert "rotation" in detail.lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ssh_run_happy_path_emits_and_stops(monkeypatch):
    events, statuses = [], []
    stop = asyncio.Event()
    async def on_events(items, seen):
        events.extend(items)
        stop.set()
    async def on_status(status): statuses.append(status)
    follower = SSHFileFollower(make_source(), on_events, on_status)
    monkeypatch.setattr(follower, "_connect", lambda: setattr(follower, "_sftp", object()))
    monkeypatch.setattr(follower, "_read_new", lambda: (b"2026-07-10 10:00:00 INFO remote\n", ""))
    monkeypatch.setattr(follower, "_close", lambda: setattr(follower, "_sftp", None))
    await asyncio.wait_for(follower.run(stop), 1)
    assert events and "remote" in events[0].message
    assert statuses[0].state == "connecting"
    assert statuses[-1].state == "stopped"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ssh_run_error_is_reported_without_fatal_exit(monkeypatch):
    statuses = []
    stop = asyncio.Event()
    async def on_events(items, seen): pass
    async def on_status(status):
        statuses.append(status)
        if status.state == "error":
            stop.set()
    follower = SSHFileFollower(make_source(), on_events, on_status)
    monkeypatch.setattr(follower, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(follower, "_close", lambda: None)
    await asyncio.wait_for(follower.run(stop), 1)
    assert any(item.state == "error" and "boom" in item.detail for item in statuses)

@pytest.mark.integration
@pytest.mark.asyncio
async def test_ssh_initial_read_is_limited_to_configured_history(monkeypatch):
    received = []
    stop = asyncio.Event()

    async def on_events(items, seen):
        received.extend(items)
        stop.set()

    async def on_status(status):
        pass

    source = make_source().model_copy(update={"history_events": 3})
    follower = SSHFileFollower(source, on_events, on_status)
    payload = "\n".join(
        f"2026-07-10 10:00:{index:02d} INFO remote-{index}" for index in range(10)
    ).encode() + b"\n"

    def connect():
        follower._sftp = FakeSFTP(payload)
        follower._client = SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(follower, "_connect", connect)
    await asyncio.wait_for(follower.run(stop), 1)
    assert [event.message.rsplit("-", 1)[-1] for event in received] == ["7", "8", "9"]
