"""Optional local audio solver adapter."""

from __future__ import annotations

from ..backends.base import Capability, TabInfo
from ..captcha_state import CaptchaCandidate, ChallengeKind, SolveResult, SolveSession
from ..errors import MissingDepError
from .base import CaptchaSolverAdapter
from ..session import Session


class RecaptchaAudioSolver(CaptchaSolverAdapter):
    name = "recaptcha_audio"
    requires = frozenset({Capability.EVAL, Capability.TRUSTED_INPUT})

    def can_handle(self, candidate: CaptchaCandidate) -> bool:
        return candidate.challenge_kind == ChallengeKind.AUDIO.value

    def solve_step(
        self, session: Session, tab: TabInfo, solve_session: SolveSession
    ) -> SolveResult:
        try:
            import faster_whisper  # type: ignore
        except ImportError:
            raise MissingDepError(
                "Local audio CAPTCHA requires the optional 'captcha-stt' dependency.",
                remedy="Install optional dependencies via `pip install tabpilot-mcp[captcha-stt]` or use agent_vision.",
            )
        raise NotImplementedError("faster-whisper integration is optional; use agent_vision.")
