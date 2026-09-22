# Test report

**Execution date:** 2026-09-22
**Version:** 0.1.0
**Platform:** Linux, Python 3.12
**Command:** `python -m coverage run -m pytest -q`

## Result

- Collected automated tests: **110**
- Passed: **110**
- Failed: **0**
- Errors: **0**
- Total coverage: **86%**
- Required coverage threshold: **75%**

## Covered risk areas

- Required text formats and multiline events.
- JSON Lines and custom timestamp patterns.
- Positive/negative time shifts and day-boundary crossing.
- `AND`, `OR`, `NOT`, grep, regular expressions, level/source/JSON/time filtering.
- Aggregation and dynamic-value normalization.
- Disk eviction, source deletion, permissions, and timestamp recalculation.
- Per-source rolling retention at N+1, independent multi-source limits, and live-file eviction.
- Missing files, append, truncation, and rotation.
- SSH strict host-key policy, key parameters, success and failure state transitions.
- Source manager subscriptions and lifecycle.
- API source/workspace/query/aggregation/diagnostic behavior.
- Root UI and WebSocket route availability.
- Live local-file event delivery through WebSocket.
- Selective filters whose match lies beyond the first 5,000 candidates.
- TLL multiline blocks with embedded timestamps.
- Multiline events split across polling cycles.
- Stop/start history deduplication and UTF-8 tail boundaries.

## Generated-data matrix

| Source | Logical events | Result |
|---|---:|---|
| Text plus preamble | 61 | Passed |
| TLL multiline | 60 | Passed |
| JSON Lines, shifted +2h | 60 | Passed |
| Syslog | 60 | Passed |
| Custom timestamp regex | 60 | Passed |
| Timestamp-less text | 3 | Passed |
| Malformed JSON Lines | 3 | Passed without process failure |

Contains, regex, negative contains, level, nested JSON numeric, timestamp, source,
AND/OR/NOT, and invalid-input filter paths passed. Aggregation returned all 60 JSON
events and the expected 12 `ERROR` events.

## Confirmed defects and disposition

Eleven defects were specified and fixed. See [BUGFIX_SPEC.md](BUGFIX_SPEC.md) for
severity, evidence, acceptance criteria, and test traceability.

## Residual risks

- Real SSH integration depends on server-specific SFTP behavior and should be validated against each target environment.
- The cloud browser available in this execution could not reach the local
  `localhost` server (`ERR_BLOCKED_BY_CLIENT`). UI network contracts, state
  transitions, escaping paths, and JavaScript syntax were verified, but a fresh
  post-fix visual click-through should still be run in a browser that can reach
  the local application.
- Very large sustained throughput beyond the agreed several-lines-per-second profile requires a dedicated endurance test in the deployment environment.

## Additional verification

- `ruff check log_viewer tests`: the current broad dependency range installed
  Ruff 0.16, which reports legacy style/modernization findings already present
  in the repository; no automatic style rewrite was applied because it is
  outside the bug-fix scope.
- `node --check log_viewer/static/app.js`: passed.
- Deterministic generator execution and 307-event API matrix: passed.
- Wheel build succeeded; Python modules and all three static UI assets are present.
