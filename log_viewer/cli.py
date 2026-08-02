from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import threading
import webbrowser

import uvicorn

from .api import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local-first real-time multi-log viewer")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host; localhost is recommended")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--log-level", default="info")
    return parser


def configure_logging(max_bytes: int = 20 * 1024**2) -> None:
    log_dir = Path.home() / ".local" / "state" / "log-viewer"
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    handler = RotatingFileHandler(log_dir / "application.log", maxBytes=max_bytes, backupCount=2)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.addHandler(handler)


def main() -> None:
    args = build_parser().parse_args()
    app = create_app(args.config, args.cache_dir)
    settings = app.state.config_store.get().settings
    configure_logging(settings.diagnostics_max_bytes)
    should_open = settings.open_browser and not args.no_browser
    if should_open:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://{args.host}:{args.port}")).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
