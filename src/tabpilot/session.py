"""Session: one backend, lazily connected, shared by every tool call."""

from __future__ import annotations

import threading
from typing import Any

from .backends.base import Backend, TabInfo
from .backends.registry import build_backend
from .config import Config
from .errors import CaptchaBusyError, JSError, TabPilotError, UnsupportedByBackendError
from . import payloads, tabs as tabs_module

#: Why each capability might be missing, and what to do about it.
_CAPABILITY_REMEDY = {
    "screenshot": (
        "Capturing a specific tab's pixels needs the CDP backend.\n"
        "Relaunch Chrome with a debugging port, then retry:\n"
        "  tabpilot doctor"
    ),
    "trusted_input": (
        "Trusted input events need the CDP backend. TabPilot will fall back to DOM events,\n"
        "which most pages accept — but pages that check event.isTrusted will not."
    ),
    "frame_eval": (
        "Evaluating inside iframes/frames needs the CDP backend.\n"
        "Relaunch Chrome with a debugging port, then retry:\n"
        "  tabpilot doctor"
    ),
    "drag": (
        "Dragging elements requires trusted mouse events on the CDP backend."
    ),
}


class Session:
    """Holds the browser connection and turns payload results into Python values."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._backend: Backend | None = None
        self._active_solves: dict[str, Any] = {}
        self._completed_solves: dict[str, Any] = {}
        self._solve_local = threading.local()

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = build_backend(self.config)
        return self._backend

    def reset(self) -> None:
        """Drop the connection so the next call reconnects.

        Chrome restarts, laptops sleep, SSH tunnels die. Rebuilding on demand
        means a session survives all three without the client reconnecting.
        """
        self._active_solves.clear()
        self._completed_solves.clear()
        if self._backend is not None:
            try:
                self._backend.close()
            finally:
                self._backend = None

    def close(self) -> None:
        self.reset()

    # --- solve lease and mutation guard --------------------------------------

    def acquire_solve(self, tab_id: str, solve_session: Any) -> Any:
        """Acquire solve lease on tab_id. Returns active solve or raises CaptchaBusyError."""
        existing = self._active_solves.get(tab_id)
        if existing is not None:
            if existing.is_expired:
                self._completed_solves[existing.solve_id] = existing
                del self._active_solves[tab_id]
            elif existing.candidate.candidate_id == solve_session.candidate.candidate_id:
                return existing
            else:
                raise CaptchaBusyError(
                    f"Tab '{tab_id}' is already undergoing CAPTCHA solve '{existing.solve_id}'.",
                    remedy="Finish or cancel the current solve before starting a new one.",
                )
        self._active_solves[tab_id] = solve_session
        return solve_session

    def get_active_solve(self, tab_id: str) -> Any | None:
        solve = self._active_solves.get(tab_id)
        if solve is not None and solve.is_expired:
            self._completed_solves[solve.solve_id] = solve
            del self._active_solves[tab_id]
            return None
        return solve

    def release_solve(self, tab_id: str, solve_id: str | None = None) -> None:
        solve = self._active_solves.get(tab_id)
        if solve is not None:
            if solve_id is None or solve.solve_id == solve_id:
                self._active_solves.pop(tab_id, None)
                self._completed_solves[solve.solve_id] = solve
                if len(self._completed_solves) > 50:
                    oldest = next(iter(self._completed_solves))
                    self._completed_solves.pop(oldest, None)

    def get_solve(self, solve_id: str) -> Any | None:
        for s in self._active_solves.values():
            if s.solve_id == solve_id:
                return s
        return self._completed_solves.get(solve_id)

    def check_mutation_guard(self, tab_id: str) -> None:
        """Raise CaptchaBusyError if an external tool tries to mutate a tab undergoing solve."""
        if getattr(self._solve_local, "is_internal_solve", False):
            return
        solve = self.get_active_solve(tab_id)
        if solve is not None:
            raise CaptchaBusyError(
                f"Tab '{tab_id}' has an active CAPTCHA solve lease ('{solve.solve_id}').",
                remedy="Call solve_captcha with operation='cancel' or complete the solve first.",
            )

    # --- capability gating ---------------------------------------------------

    def require(self, capability: str) -> None:
        backend = self.backend
        if not backend.supports(capability):
            raise UnsupportedByBackendError(
                f"The {backend.name} backend cannot do {capability!r}.",
                remedy=_CAPABILITY_REMEDY.get(capability),
            )

    # --- tabs ----------------------------------------------------------------

    def list_tabs(self) -> list[TabInfo]:
        return self.backend.list_tabs()

    def resolve(self, tab_id: str | None = None, url_pattern: str | None = None) -> TabInfo:
        return tabs_module.resolve(self.backend, tab_id, url_pattern)

    # --- evaluation ----------------------------------------------------------

    def eval_raw(self, tab_id: str, expression: str, timeout_s: float | None = None) -> Any:
        return self.backend.eval_js(tab_id, expression, timeout_s or self.config.timeout_s)

    def run_payload(
        self,
        tab_id: str,
        name: str,
        options: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run a JS payload and unwrap its ``{ok, ...}`` envelope.

        Payloads report failure as data rather than by throwing, so a missing
        selector produces a usable message instead of an opaque page exception.
        """
        result = self.eval_raw(tab_id, payloads.call(name, options), timeout_s)
        if not isinstance(result, dict):
            raise JSError(f"Payload {name!r} returned {type(result).__name__}, expected an object.")
        if not result.get("ok"):
            raise _payload_error(name, result)
        return result

    def run_payload_in_frame(
        self,
        tab_id: str,
        frame_ref: Any,
        name: str,
        options: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run a JS payload inside a specific frame and unwrap its ``{ok, ...}`` envelope."""
        expr = payloads.call(name, options)
        result = self.backend.evaluate_in_frame(tab_id, frame_ref, expr, timeout_s or self.config.timeout_s)
        if not isinstance(result, dict):
            raise JSError(f"Payload {name!r} returned {type(result).__name__}, expected an object.")
        if not result.get("ok"):
            raise _payload_error(name, result)
        return result


def _payload_error(name: str, result: dict[str, Any]) -> TabPilotError:
    from .errors import ElementNotFoundError

    message = result.get("error") or f"payload {name} failed"
    remedy = None

    available = result.get("available")
    if available:
        listed = ", ".join(
            f"{item.get('label') or item.get('value')!r}" for item in available[:25]
        )
        remedy = f"Options actually present: {listed}"
        if result.get("optionCount", 0) > len(available):
            remedy += f" (+{result['optionCount'] - len(available)} more)"

    if result.get("needsUiInteraction"):
        remedy = (
            "Drive it through the UI instead: click the control, wait_for the option list,\n"
            "then click the option by its text."
        )

    if result.get("matched") == 0 or "matches" in message.lower() or "matched" in message.lower():
        return ElementNotFoundError(message, remedy=remedy)
    return JSError(message, remedy=remedy)
