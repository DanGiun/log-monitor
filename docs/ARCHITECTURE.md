# Architecture

## Components

```text
Browser UI
   │ REST + WebSocket on 127.0.0.1
FastAPI application
   ├── ConfigStore ── ~/.config/log-viewer/config.json
   ├── SourceManager
   │    ├── LocalFileFollower
   │    └── SSHFileFollower (Paramiko/SFTP)
   ├── LogParser + EventAssembler
   ├── EventStore ── ephemeral SQLite/WAL cache
   ├── Filter engine
   └── Aggregation engine
```

## Design decisions

### Single read stream per source

`SourceManager` creates at most one reader task for each source. Multiple panels consume the same parsed events, preventing duplicated file and SSH I/O.

### Temporary SQLite buffer

SQLite provides an indexed disk-backed working set without introducing a separate database service. The database uses WAL mode and stores only the current session. A logical-byte counter enforces the configured limit; oldest events are evicted in batches when the limit is exceeded.

### Stable event ordering

Events use the adjusted display timestamp, then reception time, source sequence, and event ID. The WebSocket endpoint buffers events for the configured sorting delay before emission. Late events are inserted at the correct position in each browser panel without forcing the user's scroll position to jump.

### Parsing

The parser supports automatic text/JSON detection, common ISO-like timestamps, syslog month timestamps, custom regex/format pairs, level extraction, and nested JSON timestamp/level fields. `EventAssembler` combines continuation lines with the preceding timestamped line.

### Source state machine

```text
starting → running
starting/running → missing
starting/running → error
missing/error → running
any active state → stopped
SSH additionally uses connecting before running/error
```

Source failures are isolated. They update UI state and trigger retry rather than terminating the process.

### Security boundaries

- Default bind address is `127.0.0.1`.
- Configuration and cache permissions are user-private.
- SSH uses system host keys and rejects unknown keys.
- SSH passwords/passphrases are not persisted or accepted by the configuration model.
- Source files are opened read-only.
- Diagnostics omit event contents and secrets.

## Data model

A source contains path/SSH location, parser settings, color, history count, poll interval, and time shift. Sources are global and can be referenced by any workspace.

A workspace stores grid columns and panels. A panel stores one or more source IDs, a filter tree, display settings, and aggregation mode.

Persistent configuration stores only metadata. `LogEvent` records exist solely in the current SQLite session and browser memory.

## Extension points

- Add another reader by implementing the reader protocol and registering it in `SourceManager`.
- Add timestamp formats in `parser.py` without changing storage.
- Add filters by extending `FilterCondition` and `matches_condition`.
- Add aggregations in `aggregation.py` and expose them through the existing endpoint.
