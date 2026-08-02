#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python3 -m venv .build-venv
. .build-venv/bin/activate
python -m pip install --upgrade pip
python -m pip install . pyinstaller
pyinstaller --noconfirm --clean --onefile --name log-viewer --collect-data log_viewer scripts/pyinstaller_entry.py
printf 'Built %s/dist/log-viewer\n' "$ROOT"
