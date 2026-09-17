"""Session: one backend, lazily connected, shared by every tool call."""

from __future__ import annotations

from typing import Any

from .backends.base import Backend, TabInfo
from .backends.registry import build_backend
from .config import Config
from .errors import JSError, TabPilotError, UnsupportedByBackendError
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
}


class Session:
    """Holds the browser connection and turns payload results into Python values."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._backend: Backend | None = None

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
        if self._backend is not None:
            try:
                self._backend.close()
            finally:
                self._backend = None

    def close(self) -> None:
        self.reset()

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
