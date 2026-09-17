"""A fake backend, so everything above the transport is testable without a browser."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tabpilot import payloads  # noqa: E402
from tabpilot.backends.base import Backend, Capability, TabInfo  # noqa: E402
from tabpilot.config import Config  # noqa: E402
from tabpilot.errors import NoTabError  # noqa: E402
from tabpilot.session import Session  # noqa: E402


def decode_options(expression: str) -> dict[str, Any]:
    """Recover the options a payload was called with.

    ``payloads.call`` renders ``/*tabpilot:name*/(<js>)(<json>)``, so the
    argument object is the final parenthesised group.
    """
    import json

    boundary = expression.rindex(")(")
    return json.loads(expression[boundary + 2:-1])


class FakeBackend(Backend):
    """Records every call and replays programmed payload results.

    ``responses`` maps a payload name to either a single result or a list of
    results consumed in order — which is how the matrix tests assert that rows
    are driven one per call rather than in one batch.
    """

    name = "fake"

    def __init__(
        self,
        tabs: list[TabInfo] | None = None,
        responses: dict[str, Any] | None = None,
        capabilities: frozenset[str] | None = None,
    ) -> None:
        self._tabs = tabs if tabs is not None else [
            TabInfo(id="t1", title="First", url="https://example.com/one", active=True),
            TabInfo(id="t2", title="Second", url="https://example.com/two"),
        ]
        self.responses = responses or {}
        self.capabilities = capabilities if capabilities is not None else frozenset({
            Capability.EVAL, Capability.NAVIGATE, Capability.OPEN_CLOSE,
            Capability.ACTIVATE, Capability.SCREENSHOT, Capability.TRUSTED_INPUT,
        })
        self.calls: list[tuple[str, Any]] = []
        self.screenshot_bytes = b"\x89PNG\r\n\x1a\nfake"

    # --- plumbing ------------------------------------------------------------

    def health(self) -> None:
        return None

    def list_tabs(self) -> list[TabInfo]:
        return list(self._tabs)

    def eval_js(self, tab_id: str, expression: str, timeout_s: float) -> Any:
        name = payloads.payload_name(expression)
        self.calls.append((name or "raw", expression if name is None else None))
        if name is None:
            return self.responses.get("raw", None)

        programmed = self.responses.get(name)
        if programmed is None:
            raise AssertionError(f"FakeBackend has no programmed response for payload {name!r}")
        if callable(programmed):
            # Lets a test answer differently per argument, so behaviour that
            # depends on *which* predicate is being polled can be asserted
            # without counting poll iterations.
            return programmed(decode_options(expression))
        if isinstance(programmed, list):
            if not programmed:
                raise AssertionError(f"Ran out of programmed responses for {name!r}")
            return programmed.pop(0)
        return programmed

    def open_tab(self, url: str, activate: bool = True) -> TabInfo:
        tab = TabInfo(id=f"t{len(self._tabs) + 1}", title="", url=url, active=activate)
        self._tabs.append(tab)
        self.calls.append(("open_tab", url))
        return tab

    def close_tab(self, tab_id: str) -> None:
        self._tabs = [tab for tab in self._tabs if tab.id != tab_id]
        self.calls.append(("close_tab", tab_id))

    def navigate(self, tab_id: str, url: str) -> None:
        self.calls.append(("navigate", (tab_id, url)))

    def activate_tab(self, tab_id: str) -> None:
        self.calls.append(("activate_tab", tab_id))

    def screenshot(self, tab_id: str, **kwargs: Any) -> bytes:
        self.calls.append(("screenshot", kwargs))
        return self.screenshot_bytes

    def click_at(self, tab_id: str, x: float, y: float, timeout_s: float = 20.0) -> None:
        self.calls.append(("click_at", (round(x), round(y))))

    def insert_text(self, tab_id: str, text: str, timeout_s: float = 20.0) -> None:
        self.calls.append(("insert_text", text))

    def press_key(self, tab_id: str, key: str, timeout_s: float = 20.0) -> None:
        self.calls.append(("press_key", key))

    # --- assertions ----------------------------------------------------------

    def payload_calls(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)


class FakeSession(Session):
    """A Session wired to a FakeBackend instead of a real browser."""

    def __init__(self, backend: FakeBackend, config: Config | None = None) -> None:
        super().__init__(config or Config(matrix_delay_ms=0, timeout_ms=1000))
        self._backend = backend

    @property
    def backend(self) -> FakeBackend:  # type: ignore[override]
        return self._backend  # type: ignore[return-value]


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def session(backend: FakeBackend) -> FakeSession:
    return FakeSession(backend)


@pytest.fixture
def tab(backend: FakeBackend) -> TabInfo:
    return backend.list_tabs()[0]


__all__ = ["FakeBackend", "FakeSession", "Config", "TabInfo", "NoTabError", "decode_options"]
