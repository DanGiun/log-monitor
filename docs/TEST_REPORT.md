# Test report

**Execution date:** 2026-07-12  
**Version:** 0.1.0  
**Platform:** Linux, Python 3.13.5  
**Command:** `python -m coverage run -m pytest -q`

## Result

- Collected automated tests: **94**
- Passed: **94**
- Failed: **0**
- Errors: **0**
- Total coverage: **84%**
- Required coverage threshold: **75%**

## Covered risk areas

- Required text formats and multiline events.
- JSON Lines and custom timestamp patterns.
- Positive/negative time shifts and day-boundary crossing.
- `AND`, `OR`, `NOT`, grep, regular expressions, level/source/JSON/time filtering.
- Aggregation and dynamic-value normalization.
- Disk eviction, source deletion, permissions, and timestamp recalculation.
- Missing files, append, truncation, and rotation.
- SSH strict host-key policy, key parameters, success and failure state transitions.
- Source manager subscriptions and lifecycle.
- API source/workspace/query/aggregation/diagnostic behavior.
- Root UI and WebSocket route availability.

## Residual risks

- Real SSH integration depends on server-specific SFTP behavior and should be validated against each target environment.
- Browser interaction is smoke-tested with Chromium; visual usability should also be accepted by the intended QA users.
- Very large sustained throughput beyond the agreed several-lines-per-second profile requires a dedicated endurance test in the deployment environment.

## Additional verification

- `ruff check log_viewer tests`: passed with no findings.
- `node --check log_viewer/static/app.js`: passed.
- Offline Chromium UI smoke: source list, panel rendering, shifted timestamps, severity styling, and log rows rendered without console/page errors.
- Wheel build: succeeded; static UI assets verified inside the wheel.
- Clean virtual-environment install: succeeded.
- Installed `log-viewer` command: started on `127.0.0.1` and returned a successful `/api/health` response.
