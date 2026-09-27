"""Verification of CAPTCHA outcomes and access postconditions."""

from __future__ import annotations

import hashlib
import json
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
            var el = document.querySelector({json.dumps(candidate.response_field_ref)});
            return el ? (el.value || el.textContent || '') : null;
        }})()"""
        val = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
        return str(val) if val else None
    except Exception:
        return None



def has_widget_error(session: Session, tab: TabInfo, candidate: CaptchaCandidate) -> bool:
    """Check if the candidate's widget or container displays an error or expired state."""
    try:
        expr = f"""(function() {{
            var wRef = {json.dumps(candidate.widget_ref)};
            var root = wRef ? document.querySelector(wRef) : null;
            if (!root) return false;
            var scope = root.closest('.challenge-section, .captcha-container, .captcha-box') || root;
            var isExpiredOrError = scope.querySelector('.recaptcha-checkbox-expired, .rc-anchor-error, .rc-anchor-error-msg, .cf-turnstile-error, .cf-turnstile-expired, .h-captcha-error') ||
                                   (scope.classList && (scope.classList.contains('recaptcha-checkbox-expired') || scope.classList.contains('rc-anchor-error') || scope.classList.contains('cf-turnstile-error')));
            return Boolean(isExpiredOrError);
        }})()"""
        return bool(session.backend.eval_js(tab.id, expr, timeout_s=3.0))
    except Exception:
        return False


def is_widget_ui_passed(session: Session, tab: TabInfo, candidate: CaptchaCandidate) -> bool:
    """Check if the candidate's widget UI explicitly displays a passed state.
    
    Must be strictly scoped to candidate's own widget or container. Never searches
    global document or enclosing form for generic checkboxes or aria-checked markers.
    """
    try:
        expr = f"""(function() {{
            var wRef = {json.dumps(candidate.widget_ref)};
            var root = wRef ? document.querySelector(wRef) : null;
            var kind = {json.dumps(candidate.challenge_kind)};
            var prov = {json.dumps(candidate.provider)};

            if (!root) {{
                if (prov === 'cloudflare' && kind === 'interstitial') {{
                    var cfStage = document.getElementById('challenge-stage') ||
                                  document.getElementById('challenge-running') ||
                                  document.getElementById('cf-challenge-running');
                    var cfTitle = (document.title || '').indexOf('Just a moment...') !== -1;
                    var titleLower = (document.title || '').toLowerCase();
                    var bodyText = document.body ? (document.body.innerText || document.body.textContent || '').toLowerCase() : '';
                    var isBlockedOrError = titleLower.indexOf('access denied') !== -1 ||
                                           titleLower.indexOf('attention required') !== -1 ||
                                           titleLower.indexOf('error') !== -1 ||
                                           bodyText.indexOf('403 access denied') !== -1 ||
                                           bodyText.indexOf('access denied') !== -1 ||
                                           bodyText.indexOf('error 403') !== -1;
                    if (isBlockedOrError) return false;
                    return !cfStage && !cfTitle && Boolean(document.body && document.body.children.length > 0);
                }}
                return false;
            }}

            // Check for explicit error or expired state in the widget or its immediate container
            var scope = (kind === 'image_text') ? (root.closest('.captcha-box, .challenge-section') || root) : root;
            var isExpiredOrError = scope.querySelector('.recaptcha-checkbox-expired, .rc-anchor-error, .rc-anchor-error-msg, .cf-turnstile-error, .cf-turnstile-expired, .h-captcha-error') ||
                                   (scope.classList && (scope.classList.contains('recaptcha-checkbox-expired') || scope.classList.contains('rc-anchor-error') || scope.classList.contains('cf-turnstile-error')));
            if (isExpiredOrError) return false;

            // Provider-specific scoped passed check
            if (prov === 'recaptcha') {{
                if (kind === 'checkbox') {{
                    var rcChecked = root.querySelector('#recaptcha-anchor[aria-checked="true"], .recaptcha-checkbox[aria-checked="true"], .recaptcha-checkbox-checked');
                    if (rcChecked) return true;
                    if (root.getAttribute('aria-checked') === 'true' && (root.id === 'recaptcha-anchor' || root.classList.contains('recaptcha-checkbox'))) return true;
                    return false;
                }} else if (kind === 'image_grid') {{
                    return root.classList.contains('captcha-passed') || Boolean(root.querySelector('.captcha-passed'));
                }}
            }} else if (prov === 'cloudflare') {{
                if (kind === 'checkbox') {{
                    var cfChecked = root.querySelector('.cf-turnstile-passed, [role="checkbox"][aria-checked="true"]');
                    if (cfChecked) return true;
                    if (root.classList.contains('cf-turnstile-passed')) return true;
                    return false;
                }}
            }} else if (prov === 'hcaptcha') {{
                var hChecked = root.querySelector('[aria-checked="true"][id*="checkbox"], .h-captcha-success');
                if (hChecked) return true;
                if (root.classList.contains('h-captcha-success')) return true;
                return false;
            }} else {{
                // Custom fixture / challenge
                if (root.classList.contains('captcha-passed') || root.classList.contains('slider-passed')) return true;
                if (root.querySelector('.captcha-passed, .slider-passed')) return true;
                if (kind === 'image_text') {{
                    var textContainer = root.closest('form, .challenge-section') || root.closest('.captcha-box') || root;
                    if (textContainer.querySelector('.captcha-passed, .badge-passed, #text-status.badge-passed')) return true;
                }}
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
    1. The widget does NOT show an error/expired state.
    2. A response token is present and distinct from baseline_fingerprint (for token-based captchas), OR
    3. The widget UI demonstrates a definitive checked/passed state with no error.
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

    # An explicit error or expired state in the widget invalidates any token or UI pass
    if has_widget_error(session, tab, candidate):
        evidence["detail"] = "Widget displays error or expired status"
        return False, "Widget displays error or expired status", evidence

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

    # Check live URL if specified
    if url_pattern:
        try:
            live_url = session.backend.eval_js(tab.id, "window.location.href", timeout_s=2.0)
            if isinstance(live_url, str) and (live_url.startswith("http://") or live_url.startswith("https://") or live_url.startswith("about:") or live_url.startswith("file://")):
                current_url = live_url
            else:
                current_url = tab.url
        except Exception:
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
        opts_json = json.dumps({
            "sel": visible_selector,
            "txt": text_contains,
        })
        expr = f"""(function(opts) {{
            var sel = opts.sel;
            var txt = opts.txt;
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
        }})({opts_json})"""

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
