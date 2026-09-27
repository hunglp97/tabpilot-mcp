"""CAPTCHA solver adapters and registry."""

from __future__ import annotations

from .base import CaptchaSolverAdapter
from .registry import get_solver_adapter

__all__ = ["CaptchaSolverAdapter", "get_solver_adapter"]
