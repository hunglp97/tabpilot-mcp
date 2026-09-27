"""Tests for CAPTCHA detection, orchestrator, and solve lifecycle."""

import json
import pytest

from conftest import FakeBackend, FakeSession
from tabpilot import captcha
from tabpilot.captcha_state import SolveStatus
from tabpilot.config import Config
from tabpilot.errors import (
    CaptchaBusyError,
    DisabledError,
    SolveExpiredError,
)


def test_detect_captcha_when_disabled():
    cfg = Config(captcha_enabled=False)
    session = FakeSession(FakeBackend(), config=cfg)
    with pytest.raises(DisabledError):
        captcha.detect_captcha(session)


def test_detect_captcha_absent():
    backend = FakeBackend(responses={"captcha_detect": {"ok": True, "candidates": []}})
    session = FakeSession(backend)
    res_json = captcha.detect_captcha(session)
    data = json.loads(res_json)
    assert data["status"] == "absent"
    assert data["candidates"] == []


def test_detect_captcha_present():
    candidate_data = {
        "candidate_id": "cand_1",
        "provider": "cloudflare",
        "challenge_kind": "interstitial",
        "state": "actionable",
        "confidence": "high",
        "signals": ["title_just_a_moment"],
        "visible": True,
        "blocking": True,
        "available_strategies": ["passive_wait"],
    }
    backend = FakeBackend(responses={"captcha_detect": {"ok": True, "candidates": [candidate_data]}})
    session = FakeSession(backend)
    res_json = captcha.detect_captcha(session)
    data = json.loads(res_json)
    assert data["status"] == "present"
    assert len(data["candidates"]) == 1
    assert data["selected_candidate_id"] == "cand_1"


def test_solve_captcha_no_captcha():
    backend = FakeBackend(responses={"captcha_detect": {"ok": True, "candidates": []}})
    session = FakeSession(backend)
    res_json = captcha.solve_captcha(session, operation="start")
    data = json.loads(res_json)
    assert data["status"] == "no_captcha"
    assert data["success"] is False


def test_solve_captcha_start_passive_wait_success():
    candidate_data = {
        "candidate_id": "cand_1",
        "provider": "cloudflare",
        "challenge_kind": "interstitial",
        "state": "actionable",
        "confidence": "high",
        "visible": True,
        "blocking": True,
        "available_strategies": ["passive_wait"],
    }
    # Pass access check: raw returns {ok: true}
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate_data]},
        "raw": {"ok": True},
    })
    session = FakeSession(backend)

    res_json = captcha.solve_captcha(
        session,
        operation="start",
        expected={"visible_selector": "#content", "stable_ms": 10},
    )
    data = json.loads(res_json)
    assert data["status"] == "access_verified"
    assert data["success"] is True
    assert data["verification_level"] == "access"


def test_solve_captcha_start_needs_agent():
    candidate_data = {
        "candidate_id": "cand_grid",
        "provider": "recaptcha",
        "challenge_kind": "image_grid",
        "state": "actionable",
        "confidence": "high",
        "visible": True,
        "blocking": True,
        "rect_css": {"x": 50, "y": 50, "width": 300, "height": 300},
        "available_strategies": ["agent_vision"],
    }
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate_data]},
        "raw": None,
    })
    session = FakeSession(backend)

    result = captcha.solve_captcha(session, operation="start")
    res_json = result[0] if isinstance(result, list) else result
    data = json.loads(res_json)

    assert data["status"] == "needs_agent"
    assert data["solver"] == "agent_vision"
    assert data["observation"] is not None
    assert len(data["observation"]["tiles"]) > 0
    obs_id = data["observation_id"]
    solve_id = data["solve_id"]

    # Now simulate agent calling 'act' to select tile-0
    act_res = captcha.solve_captcha(
        session,
        operation="act",
        solve_id=solve_id,
        observation_id=obs_id,
        action_id="act-1",
        action={"kind": "select_tile", "target_id": "tile-0"},
    )
    act_data = json.loads(act_res[0] if isinstance(act_res, list) else act_res)
    # Backend click was recorded
    assert any(c[0] == "click_at" for c in backend.calls)

    # Idempotent replay of same action_id
    replay_res = captcha.solve_captcha(
        session,
        operation="act",
        solve_id=solve_id,
        observation_id=obs_id,
        action_id="act-1",
        action={"kind": "select_tile", "target_id": "tile-0"},
    )
    assert replay_res == act_res

    # Cancel solve
    cancel_res = captcha.solve_captcha(session, operation="cancel", solve_id=solve_id)
    cancel_data = json.loads(cancel_res)
    assert cancel_data["status"] == "cancelled"


def test_solve_lease_blocks_mutating_tools():
    candidate_data = {
        "candidate_id": "cand_grid",
        "provider": "recaptcha",
        "challenge_kind": "image_grid",
        "state": "actionable",
        "confidence": "high",
        "visible": True,
        "blocking": True,
        "available_strategies": ["agent_vision"],
    }
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate_data]},
        "raw": None,
    })
    session = FakeSession(backend)

    res = captcha.solve_captcha(session, operation="start")
    res_data = json.loads(res[0] if isinstance(res, list) else res)
    assert res_data["status"] == "needs_agent"

    tab_id = backend.list_tabs()[0].id
    # Attempting to mutate via check_mutation_guard should raise CaptchaBusyError
    with pytest.raises(CaptchaBusyError):
        session.check_mutation_guard(tab_id)

    # Releasing or cancelling solve removes the guard
    captcha.solve_captcha(session, operation="cancel", solve_id=res_data["solve_id"])
    # Now it shouldn't raise
    session.check_mutation_guard(tab_id)


def test_observe_requires_valid_solve_id():
    backend = FakeBackend()
    session = FakeSession(backend)
    with pytest.raises(SolveExpiredError):
        captcha.solve_captcha(session, operation="observe", solve_id="nonexistent-solve")
