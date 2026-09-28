from __future__ import annotations

import os
import shlex
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.request import urlopen

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from log_viewer.api import create_app
from log_viewer.models import IncidentSettings, LogEvent, PanelConfig


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _event(source_id: str, message: str, sequence: int) -> LogEvent:
    now = datetime.now(timezone.utc)
    return LogEvent(
        source_id=source_id,
        display_timestamp=now,
        received_at=now,
        level="ERROR",
        message=message,
        raw=message,
        sequence=sequence,
    )


@pytest.fixture
def incident_ui_server(tmp_path):
    app = create_app(tmp_path / "config.json", tmp_path / "cache")

    def configure(config):
        config.workspaces[0].panels = [
            PanelConfig(id="incident-panel", source_ids=["source-a"])
        ]

    app.state.config_store.update(configure)
    now = datetime.now(timezone.utc)
    for sequence, message in enumerate(
        ("database unavailable", "order rejected by exchange"), start=1
    ):
        app.state.incident_store.record(
            _event("source-a", message, sequence),
            "Service A",
            IncidentSettings(),
            now,
        )

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/api/health", timeout=0.2) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("UI test server did not start")

    yield base_url, app

    server.should_exit = True
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.fixture
def incident_page(incident_ui_server):
    base_url, app = incident_ui_server
    with sync_playwright() as playwright:
        browser_name = os.environ.get("PLAYWRIGHT_BROWSER", "chromium")
        browser_type = getattr(playwright, browser_name)
        executable_path = os.environ.get("PLAYWRIGHT_BROWSER_EXECUTABLE")
        launch_options = {"executable_path": executable_path} if executable_path else {}
        browser_args = os.environ.get("PLAYWRIGHT_BROWSER_ARGS")
        if browser_args:
            launch_options["args"] = shlex.split(browser_args)
        browser_ld_preload = os.environ.get("PLAYWRIGHT_BROWSER_LD_PRELOAD")
        if browser_ld_preload:
            launch_options["env"] = {
                **os.environ,
                "LD_PRELOAD": browser_ld_preload,
                "FAKE_PROC_SELF_EXE": os.environ.get(
                    "PLAYWRIGHT_BROWSER_FAKE_EXE", executable_path or ""
                ),
            }
        browser = browser_type.launch(headless=True, **launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page_errors: list[str] = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url, wait_until="networkidle")
        page.get_by_role("button", name="Incidents", exact=True).click()
        expect(page.locator(".incident-card")).to_have_count(2)
        expect(page.locator(".incident-results-actions")).to_have_css(
            "flex-direction", "row"
        )
        yield page, app
        browser.close()
        assert page_errors == []


@pytest.mark.ui
def test_clear_all_button_deletes_visible_incidents(incident_page):
    page, app = incident_page
    page.on("dialog", lambda dialog: dialog.accept())

    page.locator("#clear-incidents").click()

    expect(page.locator(".incident-card")).to_have_count(0)
    expect(page.locator("#incident-summary")).to_contain_text("0 incident groups")
    expect(page.locator("#clear-incidents")).to_be_disabled()
    assert app.state.incident_store.count() == 0


@pytest.mark.ui
def test_mouse_swipe_snaps_back_then_deletes_incident(incident_page):
    page, app = incident_page
    first_card = page.locator(".incident-card").first
    box = first_card.bounding_box()
    assert box is not None
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2

    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x - 60, start_y)
    assert first_card.evaluate("element => element.style.transform") == "translateX(-60px)"
    page.mouse.up()
    expect(page.locator(".incident-card")).to_have_count(2)
    page.wait_for_timeout(220)
    assert first_card.evaluate("element => element.style.transform") == ""

    box = first_card.bounding_box()
    assert box is not None
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x - 160, start_y)
    page.mouse.up()

    expect(page.locator(".incident-card")).to_have_count(1)
    assert app.state.incident_store.count() == 1
