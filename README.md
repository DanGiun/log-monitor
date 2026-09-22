# Log Viewer

Local-first, real-time multi-log viewer for Linux. It runs a web interface on `127.0.0.1`, reads local files and remote files through SSH/SFTP, and never modifies source logs.

## Requirements

- Linux x86_64 or ARM64.
- Python 3.11 or newer.
- Tested target families: Ubuntu 22.04+, Debian 12+, and compatible distributions.
- A modern Firefox or Chromium browser.

## Quick start

```bash
cd log-viewer
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
log-viewer
```

Open `http://127.0.0.1:8765` if the browser does not open automatically.

Development mode:

```bash
python -m pip install -e '.[test]'
python -m log_viewer --no-browser
```

## User-local installation

```bash
./scripts/install.sh
~/.local/bin/log-viewer
```

To install and start the optional user-level systemd service:

```bash
./scripts/install.sh --systemd
systemctl --user status log-viewer
```

The service does not open a browser. Browse to `http://127.0.0.1:8765`.

## Configuration and runtime files

- Persistent configuration: `~/.config/log-viewer/config.json`, mode `0600`.
- Temporary event cache: `~/.cache/log-viewer/session/`, directory mode `0700`; deleted between sessions.
- Application diagnostic log: `~/.local/state/log-viewer/application.log`, rotated at 20 MiB by default.
- Source log contents are not written into the persistent configuration or diagnostic report.
- Each source's **Events retained** value is both the initial-history size and its rolling
  event limit. When a new event exceeds that limit, the oldest event for that source is
  removed from the temporary cache and browser panels.

## Adding SSH sources

1. Ensure normal command-line SSH access works with a key or `ssh-agent`.
2. Add the remote server key to `~/.ssh/known_hosts` using your normal security process.
3. In **Add source**, select **SSH / SFTP** and provide host, port, username, remote path, and optionally a private-key path.
4. Password authentication and secret persistence are intentionally not supported.

## Timestamp rules

The built-in parser recognizes timestamps such as:

```text
2026-07-10 15:58:57.655 INFO component: message
2026-07-06 07:50:42.607 [WARNING] component: message
```

A line with a timestamp starts a logical event. Following lines without timestamps are attached to that event. Lines before the first timestamp remain arrival-ordered. A source with no recognized timestamps can be viewed alone but is excluded from merged time-ordered panels.

Custom parsing supports a regular expression with the named group `timestamp`:

```text
.*time=(?P<timestamp>\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}).*
```

and a matching format such as:

```text
%d/%m/%Y %H:%M:%S
```

## Testing

```bash
python -m pip install -e '.[test]'
python -m pytest -q
python -m coverage run -m pytest -q
python -m coverage report -m
```

The delivered test suite contains 110 automated tests and currently reports 86% statement/branch coverage under the configured coverage calculation. See [docs/TEST_PLAN.md](docs/TEST_PLAN.md) and [docs/TEST_REPORT.md](docs/TEST_REPORT.md).

Generate deterministic QA fixtures, including multiline TLL-style payloads, JSON Lines,
syslog, custom timestamps, malformed input, timestamp-less text, and Unicode:

```bash
python scripts/generate_test_logs.py --output generated-logs --count 100
python scripts/generate_test_logs.py --output generated-logs --count 100 --stream --iterations 50
```

## Development container

```bash
docker compose up --build
```

Mount additional host log directories read-only in `compose.yaml`. A container can only see paths explicitly mounted into it, so native installation is preferred for broad local filesystem access.

## Native single-file build

Run this on each target architecture:

```bash
./scripts/build_binary.sh
```

The output is placed in `dist/log-viewer`. Build natively on x86_64 and ARM64; the script does not cross-compile.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Known scope boundaries

- No `.gz` archive reading.
- No log editing, deletion, annotation, export, or generation.
- No mobile UI target.
- One local user; no application authentication.
- The server binds to localhost by default. Exposing it to other hosts is outside the supported security model.
