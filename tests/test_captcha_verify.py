"""Tests for CAPTCHA verification and false-success prevention."""

import time
import pytest

from conftest import FakeBackend, FakeSession
from tabpilot.backends.base import TabInfo
from tabpilot.captcha_state import CaptchaCandidate
from tabpilot.captcha_verify import (
    hash_token,
    verify_access,
    verify_widget_passed,
)


@pytest.fixture
def fake_tab():
    return TabInfo(id="t1", title="Test", url="https://example.com/login")


def test_hash_token():
    assert hash_token(None) is None
    assert hash_token("") is None
    h1 = hash_token("token-abc-123")
    h2 = hash_token("token-abc-123")
    assert h1 == h2
    assert len(h1) == 16


def test_verify_widget_passed_with_fresh_token(fake_tab):
    backend = FakeBackend(responses={"raw": "fresh-token-xyz"})
    session = FakeSession(backend)
    cand = CaptchaCandidate(
        candidate_id="c1",
        provider="recaptcha",
        challenge_kind="checkbox",
        state="actionable",
        confidence="high",
        response_field_ref='textarea[name="g-recaptcha-response"]',
    )

    passed, detail, ev = verify_widget_passed(
        session, fake_tab, cand, baseline_fingerprint="old-baseline-hash"
    )
    assert passed is True
    assert "Response token verified" in detail
    assert ev["token_present"] is True


def test_verify_widget_passed_rejects_preexisting_token(fake_tab):
    """Spec Section 9: Token before start cannot be declared fresh success."""
    token = "stale-preexisting-token"
    token_fp = hash_token(token)

    backend = FakeBackend(responses={"raw": token})
    session = FakeSession(backend)
    cand = CaptchaCandidate(
        candidate_id="c1",
        provider="recaptcha",
        challenge_kind="checkbox",
        state="actionable",
        confidence="high",
        response_field_ref='textarea[name="g-recaptcha-response"]',
    )

    passed, detail, ev = verify_widget_passed(
        session, fake_tab, cand, baseline_fingerprint=token_fp
    )
    assert passed is False
    assert "Pre-existing token" in detail


def test_verify_widget_passed_via_ui_state(fake_tab):
    # No token in response field, but DOM check shows checked UI
    backend = FakeBackend(responses={"raw": True})
    session = FakeSession(backend)
    cand = CaptchaCandidate(
        candidate_id="c1",
        provider="recaptcha",
        challenge_kind="checkbox",
        state="actionable",
        confidence="high",
    )

    passed, detail, ev = verify_widget_passed(session, fake_tab, cand, baseline_fingerprint=None)
    assert passed is True
    assert "Widget UI shows passed status" in detail


def test_verify_access_requires_content_predicate(fake_tab):
    """Spec Section 9: URL alone is not enough; requires visible_selector or text_contains."""
    backend = FakeBackend()
    session = FakeSession(backend)

    # Only URL given
    passed, detail, ev = verify_access(
        session,
        fake_tab,
        expected={"url_pattern": ".*example\\.com.*"},
        deadline_monotonic=time.monotonic() + 10,
    )
    assert passed is False
    assert "Postcondition requires visible_selector or text_contains" in detail


def test_verify_access_succeeds_when_predicates_match(fake_tab):
    backend = FakeBackend(responses={"raw": {"ok": True}})
    session = FakeSession(backend)

    expected = {
        "url_pattern": "example\\.com/login",
        "visible_selector": "#dashboard",
        "text_contains": "Welcome back",
        "stable_ms": 50,
    }
    passed, detail, ev = verify_access(
        session,
        fake_tab,
        expected=expected,
        deadline_monotonic=time.monotonic() + 10,
    )
    assert passed is True
    assert "Access verified successfully" in detail


def test_verify_access_fails_when_url_mismatches(fake_tab):
    backend = FakeBackend()
    session = FakeSession(backend)

    expected = {
        "url_pattern": "example\\.com/dashboard",
        "visible_selector": "#dashboard",
    }
    passed, detail, ev = verify_access(
        session,
        fake_tab,
        expected=expected,
        deadline_monotonic=time.monotonic() + 10,
    )
    assert passed is False
    assert "URL does not match" in detail
