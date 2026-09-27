"""Checkbox solver for reCAPTCHA v2, hCaptcha, and Cloudflare Turnstile checkboxes."""

from __future__ import annotations

import time
from typing import Any

from ..backends.base import Capability, TabInfo
from ..captcha_state import (
    ChallengeKind,
    SolveResult,
    SolveSession,
    SolveStatus,
    VerificationLevel,
)
from ..captcha_verify import verify_access, verify_widget_passed
from .base import CaptchaSolverAdapter
from ..session import Session


class CheckboxSolver(CaptchaSolverAdapter):
    name = "checkbox"
    requires = frozenset({Capability.EVAL, Capability.TRUSTED_INPUT})

    def can_handle(self, candidate) -> bool:
        return candidate.challenge_kind == ChallengeKind.CHECKBOX.value

    def solve_step(
        self,
        session: Session,
        tab: TabInfo,
        solve_session: SolveSession,
    ) -> SolveResult:
        # Check if already passed
        passed, detail, ev = verify_widget_passed(
            session, tab, solve_session.candidate, solve_session.baseline_token_fingerprint
        )
        if passed:
            solve_session.evidence.append(ev)
            solve_session.status = SolveStatus.WIDGET_PASSED.value
            solve_session.verification_level = VerificationLevel.WIDGET.value
            return SolveResult(
                status=solve_session.status,
                verification_level=solve_session.verification_level,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                provider=solve_session.candidate.provider,
                challenge_kind=solve_session.candidate.challenge_kind,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail="Widget is already verified / passed",
            )

        budget_status = solve_session.check_budgets()
        if budget_status:
            solve_session.status = budget_status.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail=f"Terminated due to {budget_status.value}",
            )

        # If already clicked once, observe must not re-click
        if solve_session.attempts > 0:
            from .agent_vision import AgentVisionSolver
            vision_solver = AgentVisionSolver()
            expr = """(function() {
                var bframe = document.querySelector('iframe[src*="bframe"], iframe[title*="challenge"], .recaptcha-challenge, .h-captcha-challenge, .captcha-modal');
                if (!bframe) return false;
                var r = bframe.getBoundingClientRect();
                var style = window.getComputedStyle(bframe);
                return r.width > 100 && r.height > 100 && style.display !== 'none' && style.visibility !== 'hidden';
            })()"""
            has_challenge_modal = False
            try:
                has_challenge_modal = bool(session.backend.eval_js(tab.id, expr, timeout_s=2.0))
            except Exception:
                pass

            if has_challenge_modal and solve_session.agent_vision:
                solve_session.candidate.challenge_kind = ChallengeKind.IMAGE_GRID.value
                solve_session.solver_name = vision_solver.name
                return vision_solver.solve_step(session, tab, solve_session)

            solve_session.status = SolveStatus.WAITING.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail="Waiting for widget to update or challenge to present",
                next_action="observe",
                retry_after_ms=800,
            )

        # Locate click target: prefer live bounding rect after scrollIntoView
        w_ref = solve_session.candidate.widget_ref
        try:
            expr = f"""(function() {{
                var el = ({repr(w_ref)} ? document.querySelector({repr(w_ref)}) : null) ||
                         document.querySelector('.recaptcha-checkbox, .cf-turnstile, iframe[src*="anchor"], iframe[src*="turnstile"], .h-captcha');
                if (!el) return null;
                if (typeof el.scrollIntoView === 'function') {{
                    el.scrollIntoView({{ block: 'center', behavior: 'instant' }});
                }}
                var r = el.getBoundingClientRect();
                return {{ x: Math.round(r.left), y: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }};
            }})()"""
            live_rect = session.backend.eval_js(tab.id, expr, timeout_s=3.0)
        except Exception:
            live_rect = None

        rect = live_rect or solve_session.candidate.rect_css

        if not rect or rect.get("width", 0) <= 0:
            solve_session.status = SolveStatus.UNSUPPORTED.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail="Cannot locate checkbox coordinates on page",
            )

        # In standard reCAPTCHA and Turnstile widgets, the checkbox icon is around 25-35px from left edge
        w = float(rect["width"])
        h = float(rect["height"])
        rx = float(rect["x"])
        ry = float(rect["y"])

        click_x = rx + (28.0 if w >= 60 else w / 2.0)
        click_y = ry + h / 2.0

        # Dispatch trusted click
        solve_session.attempts += 1
        solve_session.actions_used += 1

        try:
            session.backend.click_at(tab.id, click_x, click_y, timeout_s=5.0)
        except Exception as exc:
            solve_session.status = SolveStatus.INTERRUPTED.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail=f"Failed to dispatch click: {exc}",
            )

        solve_session.evidence.append({
            "kind": "checkbox_click",
            "observed_at": time.time(),
            "click_x": click_x,
            "click_y": click_y,
        })

        # Wait briefly for widget to react
        time.sleep(1.0)

        # Check if widget passed after click
        passed, detail, ev = verify_widget_passed(
            session, tab, solve_session.candidate, solve_session.baseline_token_fingerprint
        )
        solve_session.evidence.append(ev)
        if passed:
            if solve_session.expected:
                ok, acc_detail, acc_ev = verify_access(
                    session, tab, solve_session.expected, solve_session.deadline_monotonic
                )
                solve_session.evidence.append(acc_ev)
                if ok:
                    solve_session.status = SolveStatus.ACCESS_VERIFIED.value
                    solve_session.verification_level = VerificationLevel.ACCESS.value
                    return SolveResult(
                        status=solve_session.status,
                        verification_level=solve_session.verification_level,
                        solve_id=solve_session.solve_id,
                        candidate_id=solve_session.candidate.candidate_id,
                        provider=solve_session.candidate.provider,
                        challenge_kind=solve_session.candidate.challenge_kind,
                        solver=self.name,
                        attempts=solve_session.attempts,
                        rounds=solve_session.rounds,
                        actions_used=solve_session.actions_used,
                        elapsed_ms=solve_session.elapsed_ms,
                        remaining_ms=solve_session.remaining_ms,
                        evidence=solve_session.evidence,
                        detail="Access postconditions verified after checkbox click",
                    )

            solve_session.status = SolveStatus.WIDGET_PASSED.value
            solve_session.verification_level = VerificationLevel.WIDGET.value
            return SolveResult(
                status=solve_session.status,
                verification_level=solve_session.verification_level,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                provider=solve_session.candidate.provider,
                challenge_kind=solve_session.candidate.challenge_kind,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=solve_session.remaining_ms,
                evidence=solve_session.evidence,
                detail="Checkbox clicked and widget passed",
            )

        # Check if an image challenge opened (e.g. bframe opened or challenge modal visible)
        from .agent_vision import AgentVisionSolver

        vision_solver = AgentVisionSolver()
        # Scan for challenge modal or bframe
        expr = """(function() {
            var bframe = document.querySelector('iframe[src*="bframe"], iframe[title*="challenge"], .recaptcha-challenge, .h-captcha-challenge, .captcha-modal');
            if (!bframe) return false;
            var r = bframe.getBoundingClientRect();
            var style = window.getComputedStyle(bframe);
            return r.width > 100 && r.height > 100 && style.display !== 'none' && style.visibility !== 'hidden';
        })()"""
        has_challenge_modal = False
        try:
            has_challenge_modal = bool(session.backend.eval_js(tab.id, expr, timeout_s=2.0))
        except Exception:
            pass

        if has_challenge_modal and solve_session.agent_vision:
            # Switch solver to agent vision
            solve_session.candidate.challenge_kind = ChallengeKind.IMAGE_GRID.value
            solve_session.solver_name = vision_solver.name
            return vision_solver.solve_step(session, tab, solve_session)

        # If still in waiting state
        solve_session.status = SolveStatus.WAITING.value
        return SolveResult(
            status=solve_session.status,
            solve_id=solve_session.solve_id,
            candidate_id=solve_session.candidate.candidate_id,
            solver=self.name,
            attempts=solve_session.attempts,
            rounds=solve_session.rounds,
            actions_used=solve_session.actions_used,
            elapsed_ms=solve_session.elapsed_ms,
            remaining_ms=solve_session.remaining_ms,
            evidence=solve_session.evidence,
            detail="Checkbox clicked; waiting for widget to update or challenge to present",
            next_action="observe",
            retry_after_ms=800,
        )
