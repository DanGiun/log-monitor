# Incident register test plan

## Unit coverage

- `ERROR` detection is case-insensitive but does not classify `WARNING` by level.
- Keyword matching is case-insensitive, supports substrings, and handles Unicode.
- Empty keyword lists and duplicate/case-variant keyword validation.
- Dynamic UUID/IP/hex/number normalization.
- Leading ISO and syslog timestamps are excluded from the comparison mask.
- Similarity boundaries immediately below, at, and above 70%.
- Retention bounds: 1 hour and 8760 hours accepted; outside values rejected.
- Stable occurrence fingerprinting for replay protection.

## Storage integration coverage

- Create an incident from a qualifying event.
- Ignore a non-qualifying event.
- Aggregate exact repeats and update `count`/`last_seen`.
- Aggregate exact normalized repeats across intervals longer than 10 seconds.
- Aggregate ≥70% similar events inside 10 seconds.
- Keep dissimilar events, different sources, and merely similar events outside 10 seconds separate.
- Prefer counter aggregation over row spam for a 100-event burst.
- Prevent replay of the same occurrence.
- Retain the earliest `first_seen` and latest `last_seen` for out-of-order events.
- Remove incidents older than global retention and preserve active incidents.
- Persist incidents across store close/reopen.
- Enforce private database permissions.
- Filter list queries by one or several source IDs and deterministic ordering.

## Manager and ingestion integration coverage

- All local/SSH parsed events still enter the normal EventStore.
- Qualifying events additionally enter IncidentStore exactly once.
- Current source name is captured with the incident.
- Existing SourceManager behavior remains unchanged when no IncidentStore is given.
- A source's rolling event limit does not delete persistent incidents.

## API acceptance coverage

- Defaults are present in a legacy configuration without incident fields.
- Incident settings GET through `/api/config` and PUT persistence.
- Invalid retention and invalid/duplicate keywords return 422 without mutation.
- Workspace incident listing uses the union of panel source IDs.
- Same source is visible in multiple workspaces; unrelated sources are excluded.
- Unknown workspace returns 404; empty workspace returns an empty list.
- A shorter retention setting immediately removes expired records.
- Restarting the application with the same config directory preserves incidents.
- Existing health, source, workspace, query, aggregation, diagnostics, and WebSocket
  tests remain green.

## UI and regression coverage

- Logs/Incidents navigation preserves the selected workspace.
- Empty-state, one incident, large counter, long multiline message, deleted source,
  and multiple-source rendering.
- Keyword add/remove, duplicate rejection, retention save, API error toast, and
  automatic refresh.
- HTML escaping for incident text and source names.
- Responsive layout at desktop and narrow widths.
- JavaScript syntax check and static asset packaging.

## Exit criteria

- All automated tests pass with project coverage at or above 75%.
- A 100-event similar burst produces one incident with count 100.
- Restart/rebuild persistence and retention cleanup are verified.
- No regression in existing log viewing, filtering, workspaces, or source lifecycle.
