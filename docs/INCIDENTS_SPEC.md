# Incident register specification

## Goal

Add a separate incident register that extracts operationally relevant events from
the existing log ingestion stream without changing how log panels, filters, or
source readers work.

## Functional requirements

### Detection

- An event is incident-worthy when its normalized level is exactly `ERROR` or its
  message contains any configured keyword.
- Keyword matching is case-insensitive and uses substring semantics, so `Reject`
  also matches `rejected`.
- The editable default keyword list is `Reject`, `Failed`, `Failure`, `Exception`,
  and `Timeout`.
- Keywords are persisted in `config.json`. Users can add and remove every item,
  including defaults. Empty lists are valid.
- Changing keywords affects future events only. Existing incidents remain until
  their retention period expires.

### Grouping and spam protection

- Incidents are grouped independently for each source.
- Exact normalized messages are grouped for the full retention lifetime. Leading
  timestamps are removed and UUIDs, IP addresses, hexadecimal values, and numbers
  are replaced before comparison.
- Otherwise, normalized messages with at least 70% similarity are grouped only
  when their occurrences are no more than 10 seconds apart.
- A group stores a representative message, source, level, first occurrence,
  latest occurrence, match reason, and occurrence count.
- Repeated events increment the counter instead of creating rows or being silently
  discarded. This preserves frequency as diagnostic evidence.

### Retention and persistence

- Incident retention is global for the section, editable in whole hours, with a
  default of 24 hours and allowed range of 1–8760 hours.
- Retention is counted from `last_seen`; another grouped occurrence extends the
  incident lifetime.
- Expired incidents are removed on startup, ingestion, settings changes, and list
  queries.
- Incidents are stored in a dedicated SQLite database next to `config.json`, so
  the existing Docker `dev-config` bind mount preserves them across restarts and
  image rebuilds.
- The transient event buffer remains separate and keeps its current lifecycle.

### Workspace scope

- The incidents section uses the currently selected workspace.
- Visible source IDs are the union of source IDs referenced by that workspace's
  panels.
- An incident for a source used in several workspaces is visible in each of them.
- A workspace without panels or sources shows no incidents.

### Interface

- The top bar provides `Logs` and `Incidents` section switches.
- The incident view shows source name/color, representative level and message,
  first/last occurrence, count, and whether detection came from `ERROR` or a
  keyword.
- The view refreshes automatically and immediately respects workspace changes.
- Retention and keyword editing are available inside the incident section.

## API contract

- `GET /api/incidents?workspace_id=<id>&limit=<n>` lists non-expired incidents for
  the sources of one workspace, newest activity first.
- `PUT /api/incident-settings` atomically persists retention and keywords and
  immediately applies a shorter retention period.
- Existing API and WebSocket payloads remain backward compatible.

## Explicitly out of scope

- Manual acknowledgement, assignment, comments, and resolution workflows.
- Notifications to external systems.
- Per-workspace detection rules or retention periods.
- Retrospective reclassification after keyword edits.
