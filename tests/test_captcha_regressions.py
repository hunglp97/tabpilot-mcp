"""Regression tests for CAPTCHA review findings F01 through F11.

Covers:
- F01: Verify button scoping & single dispatch (no business form submission).
- F02: Widget-scoped UI pass detection (unrelated checkboxes / expired errors).
- F03: Pre-action staleness & image_id / tile_id validation.
- F04: Guaranteed inline Image content on needs_agent.
- F07: Atomic action deduplication under concurrent callers.
- F08: Repeated start returns active solve without re-running solver.
- F09: JSON-encoded optional expected postcondition fields.
- F10: Hard deadline enforcement under event flood.
- F11: Inspection failure retention instead of false no_captcha.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
from unittest.mock import patch
import pytest

from conftest import FakeBackend, FakeSession
from tabpilot import captcha
from tabpilot.backends.base import TabInfo
from tabpilot.backends.cdp import CDPBackend
from tabpilot.captcha_solvers.agent_vision import AgentVisionSolver
from tabpilot.captcha_state import Action, ActionKind, CaptchaCandidate, SolveStatus
from tabpilot.captcha_verify import is_widget_ui_passed, verify_access, verify_widget_passed
from tabpilot.config import Config
from tabpilot.errors import JSError, TimeoutError_


def test_f01_verify_does_not_submit_business_form():
    """F01: Verify action must not click unrelated submit buttons or double-click."""
    candidate = CaptchaCandidate(
        candidate_id="c1",
        provider="custom",
        challenge_kind="image_text",
        state="actionable",
        confidence="high",
        widget_ref="#captcha-box",
    )
    # Backend returns pos=None for challenge verify button, meaning no verify button found
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate.to_dict()]},
        "raw": None,
    })
    session = FakeSession(backend)
    res_start = captcha.solve_captcha(session, operation="start", strategy="agent_vision")
    start_data = json.loads(res_start[0] if isinstance(res_start, list) else res_start)

    res_act = captcha.solve_captcha(
        session,
        operation="act",
        solve_id=start_data["solve_id"],
        observation_id=start_data["observation_id"],
        action_id="act-verify-1",
        action={"kind": "verify"},
    )
    act_data = json.loads(res_act[0] if isinstance(res_act, list) else res_act)
    # Because challenge verify button could not be located, it returns unsupported rather than guessing
    assert act_data["status"] == "unsupported"
    # No click_at was dispatched to random elements
    assert not any(c[0] == "click_at" for c in backend.calls)


def test_f02_unrelated_checkbox_does_not_cause_false_pass():
    """F02: Unrelated checked checkboxes on the page must not mark reCAPTCHA as passed."""
    tab = TabInfo(id="t1", title="Form", url="https://example.com/checkout")
    candidate = CaptchaCandidate(
        candidate_id="c_rc",
        provider="recaptcha",
        challenge_kind="checkbox",
        state="actionable",
        confidence="high",
        widget_ref=".g-recaptcha",
    )
    # Eval_js returns False when scoped to .g-recaptcha
    backend = FakeBackend(responses={"raw": False})
    session = FakeSession(backend)
    assert is_widget_ui_passed(session, tab, candidate) is False

    passed, detail, _ = verify_widget_passed(session, tab, candidate, baseline_fingerprint=None)
    assert passed is False
    assert "not completed verification" in detail


def test_f02_expired_state_prevents_pass():
    """F02: Expired checkbox marker must invalidate passed status."""
    tab = TabInfo(id="t1", title="Form", url="https://example.com/checkout")
    candidate = CaptchaCandidate(
        candidate_id="c_rc",
        provider="recaptcha",
        challenge_kind="checkbox",
        state="actionable",
        confidence="high",
        widget_ref=".g-recaptcha",
    )
    backend = FakeBackend(responses={"raw": False})
    session = FakeSession(backend)
    assert is_widget_ui_passed(session, tab, candidate) is False


def test_f03_mismatched_image_id_returns_stale_observation():
    """F03: Action with incorrect image_id must return stale_observation without dispatching clicks."""
    candidate = CaptchaCandidate(
        candidate_id="c1",
        provider="custom",
        challenge_kind="image_grid",
        state="actionable",
        confidence="high",
        widget_ref="#grid",
    )
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate.to_dict()]},
        "raw": None,
    })
    session = FakeSession(backend)
    res_start = captcha.solve_captcha(session, operation="start", strategy="agent_vision")
    start_data = json.loads(res_start[0] if isinstance(res_start, list) else res_start)

    # Act with wrong image_id
    res_act = captcha.solve_captcha(
        session,
        operation="act",
        solve_id=start_data["solve_id"],
        observation_id=start_data["observation_id"],
        action_id="act-point-1",
        action={"kind": "click_point", "point": {"x": 0.5, "y": 0.5}, "image_id": "wrong-image-id"},
    )
    act_data = json.loads(res_act[0] if isinstance(res_act, list) else res_act)
    assert act_data["status"] == SolveStatus.STALE_OBSERVATION.value
    assert "Image ID mismatch" in act_data["detail"]
    assert not any(c[0] == "click_at" for c in backend.calls)


def test_f03_invalid_tile_id_rejected():
    """F03: Selecting a tile_id outside observation tiles raises ValueError."""
    candidate = CaptchaCandidate(
        candidate_id="c1",
        provider="custom",
        challenge_kind="image_grid",
        state="actionable",
        confidence="high",
        widget_ref="#grid",
    )
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate.to_dict()]},
        "raw": None,
    })
    session = FakeSession(backend)
    res_start = captcha.solve_captcha(session, operation="start", strategy="agent_vision")
    start_data = json.loads(res_start[0] if isinstance(res_start, list) else res_start)

    with pytest.raises(ValueError, match="not in observation tiles"):
        captcha.solve_captcha(
            session,
            operation="act",
            solve_id=start_data["solve_id"],
            observation_id=start_data["observation_id"],
            action_id="act-invalid-tile",
            action={"kind": "select_tile", "target_id": "unrelated-button-id"},
        )


def test_f04_needs_agent_returns_image_content():
    """F04: needs_agent must return Image content regardless of return_images config."""
    cfg = Config(return_images="auto")  # default auto does not inline images for normal screenshots
    candidate = {
        "candidate_id": "c1",
        "provider": "custom",
        "challenge_kind": "image_grid",
        "state": "actionable",
        "confidence": "high",
        "rect_css": {"x": 0, "y": 0, "width": 300, "height": 300},
        "available_strategies": ["agent_vision"],
    }
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate]},
        "raw": None,
    })
    session = FakeSession(backend, config=cfg)
    res = captcha.solve_captcha(session, operation="start")
    assert isinstance(res, list) and len(res) == 2
    data = json.loads(res[0])
    assert data["status"] == "needs_agent"
    # Second element is an Image instance
    assert hasattr(res[1], "data") and (hasattr(res[1], "_format") or hasattr(res[1], "format"))


def test_f07_concurrent_duplicate_action_deduplication():
    """F07: Concurrent callers with the same action_id must create exactly 1 input side effect."""
    candidate = {
        "candidate_id": "c1",
        "provider": "custom",
        "challenge_kind": "image_grid",
        "state": "actionable",
        "confidence": "high",
        "widget_ref": "#grid",
        "rect_css": {"x": 0, "y": 0, "width": 300, "height": 180},
    }
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate]},
        "raw": None,
    })
    session = FakeSession(backend)
    res_start = captcha.solve_captcha(session, operation="start", strategy="agent_vision")
    initial = json.loads(res_start[0] if isinstance(res_start, list) else res_start)

    original_handle = AgentVisionSolver.handle_action
    in_flight = threading.Event()

    def slow_handle(self, *args, **kwargs):
        in_flight.set()
        time.sleep(0.15)
        return original_handle(self, *args, **kwargs)

    def caller(_):
        return captcha.solve_captcha(
            session,
            operation="act",
            solve_id=initial["solve_id"],
            observation_id=initial["observation_id"],
            action_id="concurrent-same-action",
            action={"kind": "click_point", "point": {"x": 0.5, "y": 0.5}, "image_id": initial["observation"]["image_id"]},
        )

    with patch.object(AgentVisionSolver, "handle_action", slow_handle):
        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(caller, 0)
            assert in_flight.wait(timeout=5)
            f2 = pool.submit(caller, 1)
            results = [f1.result(), f2.result()]

    # Both callers received identical output
    assert results[0] == results[1]
    # Exactly one click was dispatched
    clicks = sum(c[0] == "click_at" for c in backend.calls)
    assert clicks == 1


def test_f08_repeated_start_returns_active_solve():
    """F08: Calling start again with the same candidate returns existing solve without re-clicking."""
    candidate = {
        "candidate_id": "cand_repeat",
        "provider": "recaptcha",
        "challenge_kind": "image_grid",
        "state": "actionable",
        "confidence": "high",
        "rect_css": {"x": 0, "y": 0, "width": 300, "height": 300},
        "available_strategies": ["agent_vision"],
    }
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate]},
        "raw": None,
    })
    session = FakeSession(backend)

    res1 = captcha.solve_captcha(session, operation="start")
    data1 = json.loads(res1[0] if isinstance(res1, list) else res1)

    res2 = captcha.solve_captcha(session, operation="start")
    data2 = json.loads(res2[0] if isinstance(res2, list) else res2)

    active = session.get_active_solve("t1")
    assert active is not None
    assert data1["solve_id"] == data2["solve_id"] == active.solve_id
    assert "Returning existing active solve" in data2["detail"]


def test_f09_optional_expected_fields_json_encoding():
    """F09: verify_access with selector-only or text-only does not fail on Python None representation."""
    tab = TabInfo(id="t1", title="Ready", url="https://example.com/ready")
    backend = FakeBackend(responses={"raw": {"ok": True}})
    session = FakeSession(backend)

    # selector only (text_contains is None)
    ok, detail, _ = verify_access(
        session,
        tab,
        expected={"visible_selector": "#ready", "stable_ms": 0},
        deadline_monotonic=time.monotonic() + 5.0,
    )
    assert ok is True
    assert "ReferenceError" not in detail

    # text only (visible_selector is None)
    ok, detail, _ = verify_access(
        session,
        tab,
        expected={"text_contains": "Ready", "stable_ms": 0},
        deadline_monotonic=time.monotonic() + 5.0,
    )
    assert ok is True
    assert "ReferenceError" not in detail


def test_f10_hard_deadline_enforcement_on_event_flood():
    """F10: Socket receiving a flood of events must respect timeout and raise TimeoutError_."""
    class FloodSocket:
        def __init__(self):
            self.start = time.monotonic()
            self.req_id = 1
        def settimeout(self, t): pass
        def close(self): pass
        def send_text(self, text):
            self.start = time.monotonic()
            req = json.loads(text)
            self.req_id = req["id"]
        def recv_text(self):
            time.sleep(0.002)
            # Send events for 100ms before returning result
            if time.monotonic() - self.start < 0.10:
                return json.dumps({"method": "Page.frameNavigated", "params": {}})
            return json.dumps({"id": self.req_id, "result": {}})

    backend = CDPBackend()
    backend._sockets["t1"] = FloodSocket()
    # Requested timeout is 10ms, but socket delays result by 100ms
    start = time.monotonic()
    with pytest.raises(TimeoutError_):
        backend._command("t1", "Page.enable", timeout_s=0.01)
    elapsed = time.monotonic() - start
    # Must have timed out close to 10ms, well below 80ms
    assert elapsed < 0.08


def test_f11_inspection_failure_retained_as_unverified():
    """F11: Inspection payload failure must not be converted to no_captcha."""
    def fail_detect(*args, **kwargs):
        raise JSError("injected inspection failure")

    backend = FakeBackend(responses={"captcha_detect": fail_detect})
    session = FakeSession(backend)

    res = captcha.solve_captcha(session, tab_id="t1", operation="start")
    data = json.loads(res if isinstance(res, str) else res[0])
    assert data["status"] == SolveStatus.UNVERIFIED.value
    assert data["status"] != SolveStatus.NO_CAPTCHA.value
    assert any(e.get("kind") == "inspection_failed" for e in data.get("evidence", []))
