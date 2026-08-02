#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/log-viewer"
BIN_DIR="$HOME/.local/bin"
VENV="$INSTALL_DIR/venv"

python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "$ROOT"
mkdir -p "$BIN_DIR"
ln -sfn "$VENV/bin/log-viewer" "$BIN_DIR/log-viewer"

echo "Installed: $BIN_DIR/log-viewer"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add $BIN_DIR to PATH." ;;
esac

if [[ "${1:-}" == "--systemd" ]]; then
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"
  sed "s|@EXEC@|$VENV/bin/log-viewer|g" "$ROOT/scripts/log-viewer.service.in" > "$UNIT_DIR/log-viewer.service"
  systemctl --user daemon-reload
  systemctl --user enable --now log-viewer.service
  echo "Started user service: log-viewer.service"
fi
