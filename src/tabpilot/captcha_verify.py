"""Verification of CAPTCHA outcomes and access postconditions."""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any

from .backends.base import TabInfo
from .captcha_state import CaptchaCandidate, VerificationLevel
from .session import Session


def hash_token(token: str | None) -> str | None:
    if not token:
        return None
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def get_token_from_dom(session: Session, tab: TabInfo, candidate: CaptchaCandidate) -> str | None:
    """Read current value from the response field associated with the candidate."""
    if not candidate.response_field_ref:
        return None
    try:
        expr = f"""(function() {{
            var el = document.querySelector({repr(candidate.response_field_ref)});
            return el ? (el.value || el.textContent || '') : null;
        }})()"""
        val = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
        return str(val) if val else None
    except Exception:
        return None


def is_widget_ui_passed(session: Session, tab: TabInfo, candidate: CaptchaCandidate) -> bool:
    """Check if the widget UI explicitly displays a passed state."""
    try:
        expr = f"""(function() {{
            var widgetRef = {repr(candidate.widget_ref)};
            var root = widgetRef ? document.querySelector(widgetRef) : null;
            var container = root ? (root.closest('.challenge-section, form, [class*="section"]') || root.closest('.captcha-container, .recaptcha-challenge, .g-recaptcha, .cf-turnstile, .captcha-box, .captcha-slider') || root.parentElement || root) : null;

            if (container) {{
                if (container.querySelector('.recaptcha-checkbox-checked, [aria-checked="true"]')) return true;
                if (container.querySelector('.cf-turnstile-passed, .h-captcha-success, .captcha-passed, .slider-passed, .badge-passed')) return true;
                if (container.classList && (container.classList.contains('cf-turnstile-passed') || container.classList.contains('captcha-passed') || container.classList.contains('recaptcha-checkbox-checked') || container.classList.contains('slider-passed') || container.classList.contains('badge-passed'))) return true;
                var parentSection = container.closest('.challenge-section, form, [class*="section"]');
                if (parentSection) {{
                    if (parentSection.querySelector('.captcha-passed, .badge-passed, .slider-passed, .cf-turnstile-passed')) return true;
                }}
            }}

            var kind = {repr(candidate.challenge_kind)};
            var prov = {repr(candidate.provider)};
            if (prov === 'cloudflare') {{
                return Boolean(document.querySelector('.cf-turnstile-passed, .cf-turnstile.passed'));
            }} else if (prov === 'recaptcha' && kind === 'checkbox') {{
                return Boolean(document.querySelector('.recaptcha-checkbox-checked, [aria-checked="true"]'));
            }} else if (kind === 'image_grid') {{
                return Boolean(document.querySelector('.recaptcha-challenge.captcha-passed, #grid-challenge.captcha-passed'));
            }}
            return false;
        }})()"""
        return bool(session.backend.eval_js(tab.id, expr, timeout_s=3.0))
    except Exception:
        return False


def verify_widget_passed(
    session: Session,
    tab: TabInfo,
    candidate: CaptchaCandidate,
    baseline_fingerprint: str | None,
) -> tuple[bool, str, dict[str, Any]]:
    """Strictly verify if the candidate widget has passed in the browser.

    A widget is considered passed only if:
    1. A response token is present and distinct from baseline_fingerprint (for token-based captchas), OR
    2. The widget UI demonstrates a definitive checked/passed state with no error.
    """
    token = None
    # For image_text, the response_field_ref is typically user input, not a server token
    if candidate.challenge_kind != "image_text":
        token = get_token_from_dom(session, tab, candidate)
    token_fp = hash_token(token)

    evidence: dict[str, Any] = {
        "kind": "widget_check",
        "observed_at": time.time(),
        "candidate_id": candidate.candidate_id,
        "provider": candidate.provider,
        "token_present": bool(token),
        "token_fingerprint": token_fp,
    }

    if token:
        if baseline_fingerprint and token_fp == baseline_fingerprint:
            # Token existed before solve began; not proven fresh
            evidence["detail"] = "Token matches baseline fingerprint (pre-existing)"
            return False, "Pre-existing token observed; cannot verify fresh pass", evidence

        evidence["detail"] = "Fresh response token verified in DOM"
        return True, "Response token verified", evidence

    # Check UI passed state
    if is_widget_ui_passed(session, tab, candidate):
        evidence["detail"] = "Widget UI marked passed / checked"
        return True, "Widget UI shows passed status", evidence

    evidence["detail"] = "No response token or checked UI state found"
    return False, "Widget has not completed verification", evidence


def verify_access(
    session: Session,
    tab: TabInfo,
    expected: dict[str, Any] | None,
    deadline_monotonic: float,
) -> tuple[bool, str, dict[str, Any]]:
    """Verify destination postconditions for access_verified status.

    Expects a dict with:
    - visible_selector: str (optional)
    - text_contains: str (optional)
    - url_pattern: str (optional)
    - stable_ms: int (default 300)

    URL alone is not sufficient; at least one content predicate is required.
    """
    evidence: dict[str, Any] = {
        "kind": "access_check",
        "observed_at": time.time(),
        "expected": expected,
    }

    if not expected:
        evidence["detail"] = "No expected postcondition provided"
        return False, "No expected postcondition provided", evidence

    url_pattern = expected.get("url_pattern")
    visible_selector = expected.get("visible_selector")
    text_contains = expected.get("text_contains")
    stable_ms = min(expected.get("stable_ms", 300), 2000)

    if not visible_selector and not text_contains:
        evidence["detail"] = "expected postcondition must contain at least visible_selector or text_contains"
        return False, "Postcondition requires visible_selector or text_contains", evidence

    # Check URL if specified
    if url_pattern:
        current_url = tab.url
        if not re.search(url_pattern, current_url):
            evidence["detail"] = f"URL {current_url!r} does not match {url_pattern!r}"
            return False, f"URL does not match {url_pattern}", evidence

    # Check content predicates
    start_check = time.monotonic()
    remaining = deadline_monotonic - start_check
    if remaining <= 0:
        evidence["detail"] = "Deadline exceeded during access check"
        return False, "Timeout during access check", evidence

    try:
        # Check visible_selector and text_contains
        expr = f"""(function() {{
            var sel = {repr(visible_selector)};
            var txt = {repr(text_contains)};
            if (sel) {{
                var el = document.querySelector(sel);
                if (!el) return {{ ok: false, reason: 'selector not found: ' + sel }};
                var style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity || '1') <= 0) {{
                    return {{ ok: false, reason: 'selector hidden: ' + sel }};
                }}
                if (txt && (el.innerText || el.textContent || '').indexOf(txt) === -1) {{
                    return {{ ok: false, reason: 'text not in element' }};
                }}
            }} else if (txt) {{
                if ((document.body.innerText || document.body.textContent || '').indexOf(txt) === -1) {{
                    return {{ ok: false, reason: 'text not found in body: ' + txt }};
                }}
            }}
            return {{ ok: true }};
        }})()"""

        res = session.backend.eval_js(tab.id, expr, timeout_s=min(5.0, remaining))
        if not res or not res.get("ok"):
            reason = res.get("reason", "Predicate check failed") if res else "Evaluation error"
            evidence["detail"] = reason
            return False, reason, evidence

        # Verify stability
        if stable_ms > 0:
            time.sleep(stable_ms / 1000.0)
            res_stable = session.backend.eval_js(tab.id, expr, timeout_s=min(5.0, max(0.5, deadline_monotonic - time.monotonic())))
            if not res_stable or not res_stable.get("ok"):
                evidence["detail"] = "Predicate was not stable over " + str(stable_ms) + "ms"
                return False, "Access predicate not stable", evidence

        evidence["detail"] = "All expected access predicates verified"
        return True, "Access verified successfully", evidence

    except Exception as exc:
        evidence["detail"] = f"Exception verifying access: {exc}"
        return False, f"Verification failed: {exc}", evidence
