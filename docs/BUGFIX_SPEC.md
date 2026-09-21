# Bug-fix specification

This specification contains only defects demonstrated by tests or direct code-path
analysis. It intentionally excludes new product features and architectural upgrades.

| ID | Severity | Defect and evidence | Affected components | Acceptance criteria |
|---|---|---|---|---|
| LM-001 | Critical | ISO timestamps in nested TLL fields (`local_time`, `market_time`) start false events; 60 generated messages became 300 events. | `parser.py` | Only a timestamp at the beginning of a standard text line starts an event; the 60-message TLL fixture yields 60 events. |
| LM-002 | High | A selective filter inspects only the newest `limit × 5` rows and omits valid older matches. | `storage.py` | Query continues through deterministic pages until the requested match limit or end of data. |
| LM-003 | High | Stop/start rereads initial history with new IDs and duplicates cached events (3 became 6). | `api.py`, `storage.py` | Restart replaces only the reread cache tail and keeps the event count stable. |
| LM-004 | High | A timestamped line and its continuation arriving in different polling cycles become separate events. | `readers.py` | A multiline event remains pending across active reads and is flushed after an idle poll, rotation, truncation, or stop. |
| LM-005 | Medium | An invalid custom parser regex raises an unhandled exception and returns HTTP 500. | `models.py` | Invalid regex is rejected by request validation with HTTP 422. |
| LM-006 | Medium | An invalid timestamp comparison can raise HTTP 500 once events are evaluated. | `filters.py`, `api.py` | Invalid timestamp operands return HTTP 422 with a useful message. |
| LM-007 | Medium | A bounded tail can start inside a multibyte UTF-8 character and report a valid file as corrupt. | `readers.py` | When reading a partial tail, the incomplete first physical line is discarded before strict UTF-8 decoding. |
| LM-008 | Medium | Replacing a WebSocket leaves the old `onclose` handler active, causing reconnect cascades. | `static/app.js` | Intentional replacement cannot schedule a reconnect for the superseded socket. |
| LM-009 | Medium | Editing a stopped source forces `enabled=true` and resets non-exposed polling/JSON-field settings. | `static/app.js` | Editing preserves enabled state, poll interval, timestamp fields, and level fields. |
| LM-010 | Medium | Invalid advanced/saved filters can be closed or persisted even after the query fails. | `static/app.js`, `api.py` | A failed advanced filter is rolled back and its dialog stays open; invalid saved filters return HTTP 422. |
| LM-011 | Low | Deleting a source updates only panels in the active workspace in browser memory. | `static/app.js` | The source ID is removed from panels in every locally loaded workspace. |

## Change constraints

- Keep the existing FastAPI, SQLite, reader, parser, and vanilla-JavaScript structure.
- Do not add authentication, new source types, exports, dashboards, or UI redesign.
- Do not alter source log files.
- Preserve REST and WebSocket payload shapes.
- Add a regression test for every backend defect and retain the existing test suite.

## Traceability

| Defect | Regression coverage |
|---|---|
| LM-001 | `test_embedded_iso_timestamps_remain_inside_tll_multiline_event` |
| LM-002 | `test_selective_filter_scans_past_initial_prefetch` |
| LM-003 | `test_stop_start_does_not_duplicate_initial_history`, `test_delete_latest_preserves_older_source_events` |
| LM-004 | `test_multiline_event_can_span_poll_cycles`, live WebSocket delivery test |
| LM-005 | `test_invalid_custom_timestamp_regex_is_rejected_at_configuration_boundary` |
| LM-006 | timestamp filter unit and API 422 tests |
| LM-007 | `test_tail_does_not_fail_when_window_starts_inside_utf8_character` |
| LM-008–LM-011 | JavaScript syntax check plus REST-contract regression; interactive browser verification remains listed as residual work in the test report. |
