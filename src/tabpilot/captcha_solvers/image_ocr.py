"""Optional local OCR solver adapter."""

from __future__ import annotations

from ..backends.base import Capability, TabInfo
from ..captcha_state import CaptchaCandidate, ChallengeKind, SolveResult, SolveSession
from ..errors import MissingDepError
from .base import CaptchaSolverAdapter
from ..session import Session


class ImageOCRSolver(CaptchaSolverAdapter):
    name = "image_ocr"
    requires = frozenset({Capability.EVAL, Capability.SCREENSHOT, Capability.TRUSTED_INPUT})

    def can_handle(self, candidate: CaptchaCandidate) -> bool:
        return candidate.challenge_kind == ChallengeKind.IMAGE_TEXT.value

    def solve_step(
        self, session: Session, tab: TabInfo, solve_session: SolveSession
    ) -> SolveResult:
        try:
            import ddddocr  # type: ignore  # optional extra
        except ImportError:
            raise MissingDepError(
                "Local OCR requires the optional 'captcha-ocr' dependency.",
                remedy="Install optional dependencies via `pip install tabpilot-mcp[captcha-ocr]` or use agent_vision.",
            )
        raise NotImplementedError("ddddocr integration is optional; use agent_vision.")
