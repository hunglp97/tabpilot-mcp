"""Live end-to-end tests driving real Chrome via CDP against local CAPTCHA fixtures."""

from __future__ import annotations

import functools
import http.server
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tabpilot import captcha
from tabpilot.backends.cdp import CDPBackend
from tabpilot.config import Config
from tabpilot.session import Session

pytestmark = pytest.mark.live

FIXTURES = Path(__file__).parent / "fixtures"
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def _cdp_available(host: str = "127.0.0.1", port: int = 9222) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def chrome_process():
    """Ensure a Chrome instance is running with remote debugging port 9222."""
    proc = None
    if not _cdp_available():
        if os.path.exists(CHROME_PATH):
            cmd = [
                CHROME_PATH,
                "--headless=new",
                "--remote-debugging-port=9222",
                "--user-data-dir=/tmp/tabpilot-captcha-test",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Wait for CDP to respond
            for _ in range(30):
                if _cdp_available():
                    break
                time.sleep(0.2)

    yield _cdp_available()

    if proc is not None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="module")
def fixture_server():
    """Serve the test fixture over HTTP."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/captcha_page.html"
    server.shutdown()
    server.server_close()


def test_live_detect_all_captchas(chrome_process, fixture_server):
    if not chrome_process:
        pytest.skip("Chrome is not available for live CDP tests")

    config = Config(cdp_port=9222, backend="cdp")
    session = Session(config)

    # Open fixture tab
    tab = session.backend.open_tab(fixture_server, activate=True)
    time.sleep(1.0)

    try:
        det_json = captcha.detect_captcha(session, tab_id=tab.id)
        det_data = json.loads(det_json)

        assert det_data["status"] == "present"
        candidates = det_data["candidates"]
        assert len(candidates) >= 4

        providers = {c["provider"] for c in candidates}
        assert "cloudflare" in providers
        assert "recaptcha" in providers
        assert "custom" in providers

        kinds = {c["challenge_kind"] for c in candidates}
        assert "checkbox" in kinds
        assert "image_grid" in kinds or "image_text" in kinds
    finally:
        session.backend.close_tab(tab.id)


def test_live_solve_turnstile_checkbox(chrome_process, fixture_server):
    if not chrome_process:
        pytest.skip("Chrome is not available for live CDP tests")

    config = Config(cdp_port=9222, backend="cdp")
    session = Session(config)
    tab = session.backend.open_tab(fixture_server, activate=True)
    time.sleep(1.0)

    try:
        det_data = json.loads(captcha.detect_captcha(session, tab_id=tab.id))
        turnstile_cand = next(
            c for c in det_data["candidates"]
            if c["provider"] == "cloudflare" and c["challenge_kind"] == "checkbox"
        )

        res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="start",
            candidate_id=turnstile_cand["candidate_id"],
            strategy="checkbox",
        )
        res_data = json.loads(res[0] if isinstance(res, list) else res)
        # Verify passed
        assert res_data["status"] == "widget_passed"
        assert res_data["success"] is True

        # Check DOM status badge
        badge_text = session.backend.eval_js(
            tab.id, "document.getElementById('turnstile-status').textContent", timeout_s=2.0
        )
        assert badge_text == "PASSED"
    finally:
        session.backend.close_tab(tab.id)


def test_live_solve_recaptcha_checkbox(chrome_process, fixture_server):
    if not chrome_process:
        pytest.skip("Chrome is not available for live CDP tests")

    config = Config(cdp_port=9222, backend="cdp")
    session = Session(config)
    tab = session.backend.open_tab(fixture_server, activate=True)
    time.sleep(1.0)

    try:
        det_data = json.loads(captcha.detect_captcha(session, tab_id=tab.id))
        recaptcha_cand = next(
            c for c in det_data["candidates"]
            if c["provider"] == "recaptcha" and c["challenge_kind"] == "checkbox"
        )

        res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="start",
            candidate_id=recaptcha_cand["candidate_id"],
            strategy="checkbox",
        )
        res_data = json.loads(res[0] if isinstance(res, list) else res)
        assert res_data["status"] == "widget_passed"
        assert res_data["success"] is True

        badge_text = session.backend.eval_js(
            tab.id, "document.getElementById('recaptcha-status').textContent", timeout_s=2.0
        )
        assert badge_text == "PASSED"
    finally:
        session.backend.close_tab(tab.id)


def test_live_solve_image_grid_challenge(chrome_process, fixture_server):
    if not chrome_process:
        pytest.skip("Chrome is not available for live CDP tests")

    config = Config(cdp_port=9222, backend="cdp")
    session = Session(config)
    tab = session.backend.open_tab(fixture_server, activate=True)
    time.sleep(1.0)

    try:
        det_data = json.loads(captcha.detect_captcha(session, tab_id=tab.id))
        grid_cand = next(
            c for c in det_data["candidates"]
            if c["challenge_kind"] == "image_grid"
        )

        # 1. Start agent vision solve
        res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="start",
            candidate_id=grid_cand["candidate_id"],
            strategy="agent_vision",
        )
        res_data = json.loads(res[0] if isinstance(res, list) else res)
        assert res_data["status"] == "needs_agent"
        solve_id = res_data["solve_id"]
        obs_id = res_data["observation_id"]

        # 2. Agent inspects prompt: "Select all squares with traffic lights"
        # The traffic light tiles are tile-0, tile-4, tile-8
        for target_tile in ["tile-0", "tile-4", "tile-8"]:
            res = captcha.solve_captcha(
                session,
                tab_id=tab.id,
                operation="act",
                solve_id=solve_id,
                observation_id=obs_id,
                action_id=f"act-{target_tile}",
                action={"kind": "select_tile", "target_id": target_tile},
            )
            res_data = json.loads(res[0] if isinstance(res, list) else res)
            obs_id = res_data.get("observation_id", obs_id)

        # 3. Agent clicks Verify
        verify_res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="act",
            solve_id=solve_id,
            observation_id=obs_id,
            action_id="act-verify-grid",
            action={"kind": "verify"},
        )
        verify_data = json.loads(verify_res[0] if isinstance(verify_res, list) else verify_res)

        assert verify_data["status"] == "widget_passed"
        assert verify_data["success"] is True

        badge_text = session.backend.eval_js(
            tab.id, "document.getElementById('grid-status').textContent", timeout_s=2.0
        )
        assert badge_text == "PASSED"
    finally:
        session.backend.close_tab(tab.id)


def test_live_solve_text_captcha(chrome_process, fixture_server):
    if not chrome_process:
        pytest.skip("Chrome is not available for live CDP tests")

    config = Config(cdp_port=9222, backend="cdp")
    session = Session(config)
    tab = session.backend.open_tab(fixture_server, activate=True)
    time.sleep(1.0)

    try:
        det_data = json.loads(captcha.detect_captcha(session, tab_id=tab.id))
        text_cand = next(
            c for c in det_data["candidates"]
            if c["challenge_kind"] == "image_text"
        )

        res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="start",
            candidate_id=text_cand["candidate_id"],
            strategy="agent_vision",
        )
        res_data = json.loads(res[0] if isinstance(res, list) else res)
        assert res_data["status"] == "needs_agent"
        solve_id = res_data["solve_id"]
        obs_id = res_data["observation_id"]

        # Type the decoded answer
        type_res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="act",
            solve_id=solve_id,
            observation_id=obs_id,
            action_id="act-type-code",
            action={"kind": "type_answer", "text": "K7X9"},
        )
        type_data = json.loads(type_res[0] if isinstance(type_res, list) else type_res)
        obs_id = type_data.get("observation_id", obs_id)

        # Click submit
        sub_res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="act",
            solve_id=solve_id,
            observation_id=obs_id,
            action_id="act-submit-code",
            action={"kind": "verify"},
        )
        sub_data = json.loads(sub_res[0] if isinstance(sub_res, list) else sub_res)
        assert sub_data["status"] == "widget_passed"
        assert sub_data["success"] is True

        badge_text = session.backend.eval_js(
            tab.id, "document.getElementById('text-status').textContent", timeout_s=2.0
        )
        assert badge_text == "PASSED"
    finally:
        session.backend.close_tab(tab.id)


def test_live_solve_slider_puzzle(chrome_process, fixture_server):
    if not chrome_process:
        pytest.skip("Chrome is not available for live CDP tests")

    config = Config(cdp_port=9222, backend="cdp")
    session = Session(config)
    tab = session.backend.open_tab(fixture_server, activate=True)
    time.sleep(1.0)

    try:
        det_data = json.loads(captcha.detect_captcha(session, tab_id=tab.id))
        slider_cand = next(
            c for c in det_data["candidates"]
            if c["challenge_kind"] == "slider"
        )

        res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="start",
            candidate_id=slider_cand["candidate_id"],
            strategy="agent_vision",
        )
        res_data = json.loads(res[0] if isinstance(res, list) else res)
        assert res_data["status"] == "needs_agent"
        solve_id = res_data["solve_id"]
        obs_id = res_data["observation_id"]

        # Drag handle from left (0.07) to slot center (0.57)
        drag_res = captcha.solve_captcha(
            session,
            tab_id=tab.id,
            operation="act",
            solve_id=solve_id,
            observation_id=obs_id,
            action_id="act-drag-slider",
            action={
                "kind": "drag",
                "point": {"x": 0.073, "y": 0.5},
                "drag_to": {"x": 0.573, "y": 0.5},
            },
        )
        drag_data = json.loads(drag_res[0] if isinstance(drag_res, list) else drag_res)
        assert drag_data["status"] == "widget_passed"
        assert drag_data["success"] is True

        badge_text = session.backend.eval_js(
            tab.id, "document.getElementById('slider-status').textContent", timeout_s=2.0
        )
        assert badge_text == "PASSED"
    finally:
        session.backend.close_tab(tab.id)

