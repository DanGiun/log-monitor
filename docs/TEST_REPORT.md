# Test report

**Execution date:** 2026-09-25
**Version:** 0.2.1
**Platform:** Linux, Python 3.12
**Command:** `python -m coverage run -m pytest -q`

## Result

- Collected automated tests: **133**
- Passed: **133**
- Failed: **0**
- Errors: **0**
- Total coverage: **87%**
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
- Incident classification by exact `ERROR` level and editable keywords.
- Dynamic-value normalization, fuzzy burst grouping, per-source separation, and
  preservation of a 100-event burst as one group with count 100.
- Incident replay protection, retention from `last_seen`, private database
  permissions, workspace filtering, and persistence across application restart.
- Timestamp-independent exact incident grouping across the full retention period,
  while fuzzy grouping remains constrained to the 10-second burst window.

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

The incident register was implemented as new functionality according to
[INCIDENTS_SPEC.md](INCIDENTS_SPEC.md); its coverage matrix is documented in
[INCIDENTS_TEST_PLAN.md](INCIDENTS_TEST_PLAN.md).

## Residual risks

- Real SSH integration depends on server-specific SFTP behavior and should be validated against each target environment.
- The execution browser is isolated from locally started processes. UI network
  contracts, state transitions, escaping paths, static packaging, and JavaScript
  syntax were verified; a visual click-through remains part of deployment-host
  smoke testing.
- Very large sustained throughput beyond the agreed several-lines-per-second profile requires a dedicated endurance test in the deployment environment.

## Additional verification

- `ruff check log_viewer tests`: the current broad dependency range installed
  Ruff 0.16, which reports legacy style/modernization findings already present
  in the repository; no automatic style rewrite was applied because it is
  outside the bug-fix scope.
- `ruff check --select E4,E7,E9,F log_viewer tests`: passed.
- `node --check log_viewer/static/app.js`: passed.
- Deterministic generator execution and 307-event API matrix: passed.
- Wheel build succeeded; Python modules and all three static UI assets are present.
