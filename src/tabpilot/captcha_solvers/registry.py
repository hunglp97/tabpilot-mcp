"""Solver adapter registry and selection."""

from __future__ import annotations

from typing import Dict, Type

from ..captcha_state import CaptchaCandidate, ChallengeKind
from ..errors import StrategyIncompatibleError
from .agent_vision import AgentVisionSolver
from .base import CaptchaSolverAdapter
from .checkbox import CheckboxSolver
from .image_ocr import ImageOCRSolver
from .passive_wait import PassiveWaitSolver
from .recaptcha_audio import RecaptchaAudioSolver
from .slider_cv import SliderCVSolver

_REGISTRY: Dict[str, Type[CaptchaSolverAdapter]] = {
    "passive_wait": PassiveWaitSolver,
    "checkbox": CheckboxSolver,
    "agent_vision": AgentVisionSolver,
    "image_ocr": ImageOCRSolver,
    "recaptcha_audio": RecaptchaAudioSolver,
    "slider_cv": SliderCVSolver,
}


def get_solver_adapter(
    strategy: str | None,
    candidate: CaptchaCandidate,
    agent_vision: bool = True,
) -> CaptchaSolverAdapter:
    strategy = (strategy or "auto").strip().lower()

    if strategy != "auto":
        cls = _REGISTRY.get(strategy)
        if cls is None:
            raise StrategyIncompatibleError(f"Unknown strategy {strategy!r}.")
        solver = cls()
        if not solver.can_handle(candidate):
            raise StrategyIncompatibleError(
                f"Strategy {strategy!r} cannot handle challenge kind {candidate.challenge_kind!r}.",
                remedy=f"Available strategies for this candidate: {', '.join(candidate.available_strategies)}",
            )
        return solver

    # Auto selection
    if candidate.challenge_kind == ChallengeKind.INTERSTITIAL.value:
        return PassiveWaitSolver()

    if candidate.challenge_kind == ChallengeKind.CHECKBOX.value:
        return CheckboxSolver()

    if candidate.challenge_kind in {
        ChallengeKind.IMAGE_GRID.value,
        ChallengeKind.IMAGE_SELECT.value,
        ChallengeKind.IMAGE_TEXT.value,
        ChallengeKind.SLIDER.value,
    }:
        if agent_vision:
            return AgentVisionSolver()
        return PassiveWaitSolver()

    if agent_vision:
        return AgentVisionSolver()
    return PassiveWaitSolver()
