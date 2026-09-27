"""Passive wait solver for interstitials and invisible challenges."""

from __future__ import annotations

import time

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


class PassiveWaitSolver(CaptchaSolverAdapter):
    name = "passive_wait"
    requires = frozenset({Capability.EVAL})

    def can_handle(self, candidate) -> bool:
        return (
            candidate.challenge_kind == ChallengeKind.INTERSTITIAL.value
            or "passive_wait" in candidate.available_strategies
        )

    def solve_step(
        self,
        session: Session,
        tab: TabInfo,
        solve_session: SolveSession,
    ) -> SolveResult:
        solve_session.attempts += 1

        # Check expected postconditions first if provided
        if solve_session.expected:
            ok, detail, ev = verify_access(
                session, tab, solve_session.expected, solve_session.deadline_monotonic
            )
            solve_session.evidence.append(ev)
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
                    detail=detail,
                )

        # Check widget pass
        passed, detail, ev = verify_widget_passed(
            session, tab, solve_session.candidate, solve_session.baseline_token_fingerprint
        )
        solve_session.evidence.append(ev)
        if passed:
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
                detail=detail,
            )

        # Still waiting
        if solve_session.is_expired:
            solve_session.status = SolveStatus.TIMEOUT.value
            return SolveResult(
                status=solve_session.status,
                solve_id=solve_session.solve_id,
                candidate_id=solve_session.candidate.candidate_id,
                solver=self.name,
                attempts=solve_session.attempts,
                rounds=solve_session.rounds,
                actions_used=solve_session.actions_used,
                elapsed_ms=solve_session.elapsed_ms,
                remaining_ms=0,
                evidence=solve_session.evidence,
                detail="Passive wait timed out before verification completed",
            )

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
            detail="Waiting for challenge to pass or access to be granted",
            next_action="observe",
            retry_after_ms=1000,
        )
