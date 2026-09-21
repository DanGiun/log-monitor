# ISTQB-oriented test plan

## Test objectives

Verify that Log Viewer reads supported sources without modifying them, applies time shifts consistently, merges and filters events correctly, persists only configuration, remains available during source failures, and enforces security and resource boundaries.

## Generated test data

`scripts/generate_test_logs.py` produces deterministic fixtures for:

- ordinary text with every supported severity;
- multiline stack traces split across physical lines;
- TLL-style nested payloads containing embedded ISO timestamps;
- JSON Lines with nested numeric/string/boolean/null fields;
- syslog and custom-regex timestamps;
- malformed JSON, timestamp-less text, Unicode, negative numbers, IPs, and changing IDs;
- optional live append traffic for polling and WebSocket checks.

The standard QA matrix uses 60 logical events per timestamped source and validates
the exact resulting counts before filter assertions.

## Test levels

- **Unit:** models, timestamp parsing, multiline assembly, filters, normalization, aggregations.
- **Component/integration:** configuration persistence, SQLite buffer, local follower, SSH policy, source manager.
- **Acceptance/API:** source/workspace lifecycle, error responses, query/aggregation behavior, diagnostics, web entry point, WebSocket connection.

## Test-design techniques

### Equivalence partitioning

- Timestamped text, bracketed levels, JSON Lines, syslog, invalid JSON, and text with no timestamp.
- Local source, SSH source, missing source, non-file path, and invalid UTF-8.
- Include/exclude, regex/non-regex, case-sensitive/insensitive, source/level/JSON/time filters.
- Available, missing, error, connecting, running, and stopped source states.

### Boundary-value analysis

- Time shift: `-23`, `-1`, `0`, `+1`, `+23` accepted; `-24`, `+24` rejected.
- Disk buffer configuration: 100 MiB and 20 GiB boundaries through model constraints.
- Port: 1–65535 through validation.
- History and API query limits through Pydantic constraints.
- File truncation where new size is smaller than the previous offset.

### Decision tables

Merged-panel eligibility:

| Timestamp exists | One source | Multiple sources | Expected |
|---|---:|---:|---|
| Yes | Yes | No | Show by shifted time |
| Yes | No | Yes | Merge by shifted time |
| No | Yes | No | Show by arrival order |
| No | No | Yes | Exclude from merged timeline |

Filter logic:

| Group logic | C1 | C2 | Negated | Result |
|---|---:|---:|---:|---:|
| AND | T | T | F | T |
| AND | T | F | F | F |
| OR | F | T | F | T |
| OR | F | F | F | F |
| Any | R | R | T | NOT R |

### State-transition testing

- Missing local file appears and transitions to running.
- Existing file disappears and transitions to missing.
- File truncates and resumes from byte zero.
- File inode changes and transitions through rotation handling.
- SSH connection fails, reports error, retries, and can return to running.
- Enabled source stops and restarts through API actions.

### Error guessing

- Invalid regular expression.
- Custom timestamp regex without named `timestamp` group.
- Corrupt configuration file.
- Duplicate source/workspace/filter identifiers.
- Unknown SSH host key.
- Browser disconnect during a WebSocket stream.
- Cache limit exceeded by one unusually large event.

## Non-functional tests

- Security: localhost binding default, strict host key policy, private file permissions, no viewed content in diagnostics.
- Reliability: source errors are nonfatal; cache and config writes are isolated.
- Performance target: local events visible in approximately 500 ms plus configured sort delay; SSH events approximately 1.5 s under normal network conditions.
- Capacity target: 30 configured sources, normally around 10 active, at several lines per second, with a configurable disk limit.

## Entry and exit criteria

Entry: requirements agreed, implementation importable, test dependencies installed.

Exit:

- All automated tests pass.
- Coverage is at least the configured 75% threshold.
- Application starts on localhost and serves the UI/health endpoint.
- No unresolved critical or high-severity defects.
