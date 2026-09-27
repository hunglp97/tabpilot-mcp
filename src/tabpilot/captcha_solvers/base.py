"""Base solver adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..backends.base import TabInfo
from ..captcha_state import Action, CaptchaCandidate, SolveResult, SolveSession
from ..session import Session


class CaptchaSolverAdapter(ABC):
    """Abstract adapter implementing a specific solving approach."""

    name: str = "base"
    requires: frozenset[str] = frozenset()

    @abstractmethod
    def can_handle(self, candidate: CaptchaCandidate) -> bool:
        """Whether this solver can handle the given candidate."""

    @abstractmethod
    def solve_step(
        self,
        session: Session,
        tab: TabInfo,
        solve_session: SolveSession,
    ) -> SolveResult:
        """Perform one bounded step of solving within the remaining budget."""

    def handle_action(
        self,
        session: Session,
        tab: TabInfo,
        solve_session: SolveSession,
        action: Action,
    ) -> SolveResult:
        """Process an agent action for interactive/vision solvers."""
        raise NotImplementedError(f"Solver {self.name} does not accept agent actions.")
