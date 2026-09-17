"""Turn a loose tab reference into a concrete tab.

Tab ids are not stable: CDP mints a new target id on many navigations, and the
AppleScript handle is a position that shifts whenever a tab is opened or closed.
An agent that caches an id across a long workflow will eventually address the
wrong page. ``url_pattern`` sidesteps that entirely — a regex like
``tester\\.test\\.io/tests`` keeps pointing at the right tab for a whole session.
"""

from __future__ import annotations

import re

from .backends.base import Backend, TabInfo
from .errors import NoTabError


def resolve(
    backend: Backend,
    tab_id: str | None = None,
    url_pattern: str | None = None,
) -> TabInfo:
    """Resolve to exactly one tab.

    Precedence: an explicit ``tab_id``, then ``url_pattern``, then the tab that
    currently holds focus, then — if nothing is focused — the only open tab.
    """
    tabs = backend.list_tabs()
    if not tabs:
        raise NoTabError(
            "No tabs are open in the browser TabPilot is attached to.",
            remedy="Open a page, or call open_tab first.",
        )

    if tab_id:
        for tab in tabs:
            if tab.id == tab_id:
                return tab
        raise NoTabError(
            f"No open tab has id {tab_id!r}.\n\nCurrently open:\n" + _render(tabs),
            remedy="Tab ids change on navigation — pass url_pattern instead to get a durable handle.",
        )

    if url_pattern:
        try:
            pattern = re.compile(url_pattern)
        except re.error as exc:
            raise NoTabError(f"url_pattern {url_pattern!r} is not a valid regex: {exc}") from exc

        matches = [tab for tab in tabs if pattern.search(tab.url)]
        if not matches:
            raise NoTabError(
                f"No open tab's URL matches {url_pattern!r}.\n\nCurrently open:\n" + _render(tabs),
            )
        if len(matches) > 1:
            # Focus is the least surprising tiebreak; otherwise say it is ambiguous
            # rather than silently picking one and acting on the wrong page.
            focused = [tab for tab in matches if tab.active]
            if len(focused) == 1:
                return focused[0]
            raise NoTabError(
                f"{len(matches)} tabs match {url_pattern!r}:\n" + _render(matches),
                remedy="Tighten the pattern, or pass the tab_id of the one you want.",
            )
        return matches[0]

    active = [tab for tab in tabs if tab.active]
    if len(active) == 1:
        return active[0]
    if len(tabs) == 1:
        return tabs[0]
    raise NoTabError(
        "Could not tell which tab you meant: no single tab has focus.\n\nCurrently open:\n" + _render(tabs),
        remedy="Pass url_pattern (preferred) or tab_id.",
    )


def _render(tabs: list[TabInfo]) -> str:
    return "\n".join(tab.short() for tab in tabs)


def render_list(tabs: list[TabInfo], url_pattern: str | None = None) -> str:
    """Format a tab list for a tool result."""
    if url_pattern:
        try:
            pattern = re.compile(url_pattern)
        except re.error as exc:
            raise NoTabError(f"url_pattern {url_pattern!r} is not a valid regex: {exc}") from exc
        tabs = [tab for tab in tabs if pattern.search(tab.url)]

    if not tabs:
        scope = f" matching {url_pattern!r}" if url_pattern else ""
        return f"No open tabs{scope}."

    header = f"{len(tabs)} tab(s):"
    return header + "\n" + _render(tabs)
