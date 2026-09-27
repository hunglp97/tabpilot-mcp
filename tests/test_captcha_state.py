"""Tests for CAPTCHA state models, enums, and data contracts."""

import time
import pytest

from tabpilot.captcha_state import (
    Action,
    ActionKind,
    CandidateState,
    CaptchaCandidate,
    ChallengeKind,
    DetectionCoverage,
    DetectionResult,
    DetectionStatus,
    Observation,
    Provider,
    SolveResult,
    SolveSession,
    SolveStatus,
    VerificationLevel,
)


def test_candidate_serialization():
    cand = CaptchaCandidate(
        candidate_id="cand_1",
        provider=Provider.RECAPTCHA.value,
        challenge_kind=ChallengeKind.CHECKBOX.value,
        state=CandidateState.ACTIONABLE.value,
        confidence="high",
        signals=["anchor_iframe"],
        visible=True,
        blocking=True,
        rect_css={"x": 100, "y": 200, "width": 300, "height": 78},
        sitekey="6Le-wvkSAAAAAPBMRTvw0Q4Muexq9bi0DJwx_mJ-",
        response_field_ref='textarea[name="g-recaptcha-response"]',
        available_strategies=["checkbox", "agent_vision"],
    )
    d = cand.to_dict()
    assert d["candidate_id"] == "cand_1"
    assert d["provider"] == "recaptcha"
    assert d["challenge_kind"] == "checkbox"

    restored = CaptchaCandidate.from_dict(d)
    assert restored.candidate_id == cand.candidate_id
    assert restored.sitekey == cand.sitekey
    assert restored.rect_css == cand.rect_css


def test_detection_result_json():
    cand = CaptchaCandidate(
        candidate_id="cand_1",
        provider=Provider.CLOUDFLARE.value,
        challenge_kind=ChallengeKind.INTERSTITIAL.value,
        state=CandidateState.ACTIONABLE.value,
        confidence="high",
        signals=["just_a_moment"],
        visible=True,
        blocking=True,
    )
    result = DetectionResult(
        status=DetectionStatus.PRESENT.value,
        tab_id="tab-1",
        document_generation="docgen-1",
        coverage=DetectionCoverage.TOP_DOCUMENT.value,
        candidates=[cand],
        selected_candidate_id="cand_1",
    )
    json_str = result.to_json()
    assert '"status": "present"' in json_str
    assert '"selected_candidate_id": "cand_1"' in json_str
    assert '"provider": "cloudflare"' in json_str


def test_solve_result_flags():
    # Terminal and success flags
    res_passed = SolveResult(status=SolveStatus.WIDGET_PASSED.value)
    assert res_passed.terminal is True
    assert res_passed.success is True

    res_access = SolveResult(status=SolveStatus.ACCESS_VERIFIED.value)
    assert res_access.terminal is True
    assert res_access.success is True

    res_timeout = SolveResult(status=SolveStatus.TIMEOUT.value)
    assert res_timeout.terminal is True
    assert res_timeout.success is False

    res_waiting = SolveResult(status=SolveStatus.WAITING.value)
    assert res_waiting.terminal is False
    assert res_waiting.success is False

    res_needs_agent = SolveResult(status=SolveStatus.NEEDS_AGENT.value)
    assert res_needs_agent.terminal is False
    assert res_needs_agent.success is False


def test_solve_session_budgets():
    cand = CaptchaCandidate(
        candidate_id="cand_1",
        provider="custom",
        challenge_kind="image_grid",
        state="actionable",
        confidence="high",
    )
    session = SolveSession(
        solve_id="solve-1",
        tab_id="tab-1",
        candidate=cand,
        document_generation="gen1",
        deadline_monotonic=time.monotonic() + 100.0,
        start_time_monotonic=time.monotonic(),
        max_attempts=2,
        max_rounds=5,
        max_actions=10,
        strategy="auto",
        agent_vision=True,
        expected=None,
        activate_on_fail=False,
    )

    assert session.check_budgets() is None

    session.attempts = 2
    assert session.check_budgets() == SolveStatus.EXHAUSTED

    session.attempts = 1
    session.rounds = 5
    assert session.check_budgets() == SolveStatus.EXHAUSTED

    session.rounds = 2
    session.actions_used = 10
    assert session.check_budgets() == SolveStatus.EXHAUSTED


def test_action_parsing():
    data = {
        "kind": "select_tile",
        "target_id": "tile-4",
    }
    action = Action.from_dict(data)
    assert action.kind == ActionKind.SELECT_TILE.value
    assert action.target_id == "tile-4"
