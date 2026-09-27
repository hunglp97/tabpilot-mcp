"""Optional slider CV solver adapter."""

from __future__ import annotations

from ..backends.base import Capability, TabInfo
from ..captcha_state import CaptchaCandidate, ChallengeKind, SolveResult, SolveSession
from ..errors import MissingDepError
from .base import CaptchaSolverAdapter
from ..session import Session


class SliderCVSolver(CaptchaSolverAdapter):
    name = "slider_cv"
    requires = frozenset({Capability.EVAL, Capability.SCREENSHOT, Capability.TRUSTED_INPUT, Capability.DRAG})

    def can_handle(self, candidate: CaptchaCandidate) -> bool:
        return candidate.challenge_kind == ChallengeKind.SLIDER.value

    def solve_step(
        self, session: Session, tab: TabInfo, solve_session: SolveSession
    ) -> SolveResult:
        try:
            import cv2  # type: ignore
        except ImportError:
            raise MissingDepError(
                "Local slider CV requires OpenCV.",
                remedy="Install optional dependencies via `pip install tabpilot-mcp[captcha-slider]` or use agent_vision.",
            )
        raise NotImplementedError("CV slider matching is optional; use agent_vision.")
