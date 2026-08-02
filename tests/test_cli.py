import logging

import pytest

from log_viewer.cli import build_parser, configure_logging


@pytest.mark.unit
def test_cli_defaults_bind_only_to_localhost():
    args = build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8765


@pytest.mark.unit
def test_cli_accepts_no_browser_and_custom_port():
    args = build_parser().parse_args(["--no-browser", "--port", "9000"])
    assert args.no_browser is True
    assert args.port == 9000


@pytest.mark.integration
def test_diagnostic_logging_creates_rotating_handler(tmp_path, monkeypatch):
    monkeypatch.setattr("log_viewer.cli.Path.home", lambda: tmp_path)
    root = logging.getLogger()
    old = list(root.handlers)
    try:
        configure_logging(1024 * 1024)
        assert any(handler.__class__.__name__ == "RotatingFileHandler" for handler in root.handlers)
        assert (tmp_path / ".local/state/log-viewer/application.log").exists()
    finally:
        for handler in root.handlers:
            if handler not in old:
                handler.close()
                root.removeHandler(handler)
