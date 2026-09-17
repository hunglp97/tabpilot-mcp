"""Backend contract.

A backend is the transport that reaches a live browser. Every backend exposes
the same primitive operations; the higher layers (``extract``, ``interact``,
``evidence``) are written once against this interface.

Backends differ in what they *can* do, so each declares a capability set and the
tool layer refuses cleanly — naming the capability and how to get it — instead of
failing in a way that looks like a page bug.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class Capability:
    """Capability names a backend may advertise."""

    EVAL = "eval"
    """Run JavaScript in a tab and get the value back."""

    NAVIGATE = "navigate"
    """Point an existing tab at a new URL."""

    OPEN_CLOSE = "open_close"
    """Create and destroy tabs."""

    ACTIVATE = "activate"
    """Bring a tab to the front."""

    SCREENSHOT = "screenshot"
    """Capture pixels from a specific tab, whether or not it is frontmost."""

    TRUSTED_INPUT = "trusted_input"
    """Dispatch input events the page cannot tell apart from a real human."""


@dataclass
class TabInfo:
    """One browser tab.

    ``id`` is an opaque handle whose format belongs to the backend — never build
    one by hand; always take it from :meth:`Backend.list_tabs` or
    :meth:`Backend.open_tab`.
    """

    id: str
    title: str
    url: str
    active: bool = False

    def short(self) -> str:
        title = self.title or "(untitled)"
        if len(title) > 70:
            title = title[:69] + "…"
        marker = " [active]" if self.active else ""
        return f"- `{self.id}`{marker} {title}\n  {self.url}"


class Backend(ABC):
    """Transport to a live browser."""

    name: str = "base"
    capabilities: frozenset[str] = frozenset()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    @abstractmethod
    def health(self) -> None:
        """Raise :class:`~tabpilot.errors.BridgeOffError` if the browser is unreachable."""

    @abstractmethod
    def list_tabs(self) -> list[TabInfo]:
        ...

    @abstractmethod
    def eval_js(self, tab_id: str, expression: str, timeout_s: float) -> Any:
        """Evaluate ``expression`` in ``tab_id`` and return a JSON-compatible value.

        Implementations must await a returned promise, so callers can write
        ``async`` expressions without extra ceremony.
        """

    @abstractmethod
    def open_tab(self, url: str, activate: bool = True) -> TabInfo:
        ...

    @abstractmethod
    def close_tab(self, tab_id: str) -> None:
        ...

    @abstractmethod
    def navigate(self, tab_id: str, url: str) -> None:
        ...

    @abstractmethod
    def activate_tab(self, tab_id: str) -> None:
        ...

    # --- Optional, capability-gated ------------------------------------------

    def screenshot(
        self,
        tab_id: str,
        *,
        full_page: bool = False,
        clip: dict[str, float] | None = None,
        image_format: str = "png",
        quality: int = 80,
        timeout_s: float = 20.0,
    ) -> bytes:
        raise NotImplementedError

    def click_at(self, tab_id: str, x: float, y: float, timeout_s: float = 20.0) -> None:
        """Dispatch a trusted mouse click at viewport coordinates."""
        raise NotImplementedError

    def insert_text(self, tab_id: str, text: str, timeout_s: float = 20.0) -> None:
        """Insert text as trusted input at the focused element."""
        raise NotImplementedError

    def press_key(self, tab_id: str, key: str, timeout_s: float = 20.0) -> None:
        """Press a single named key (``Enter``, ``Tab``, ``Escape``, arrows)."""
        raise NotImplementedError

    def describe(self) -> str:
        caps = ", ".join(sorted(self.capabilities)) or "none"
        return f"{self.name} (capabilities: {caps})"

    def close(self) -> None:
        """Release transport resources. Safe to call repeatedly."""
