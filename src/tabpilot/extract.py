"""Reading pages under a budget."""

from __future__ import annotations

import json
from typing import Any

from .backends.base import TabInfo
from .session import Session

VALID_MODES = ("readable", "text", "html")


def read_tab(
    session: Session,
    tab: TabInfo,
    *,
    mode: str = "readable",
    selector: str | None = None,
    max_chars: int | None = None,
    include_links: bool = True,
) -> str:
    """Return a tab's content as markdown, plain text, or HTML."""
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {', '.join(VALID_MODES)}, not {mode!r}")

    budget = max_chars or session.config.max_chars
    result = session.run_payload(
        tab.id,
        "readable",
        {"mode": mode, "selector": selector, "maxChars": budget, "includeLinks": include_links},
    )

    header = [f"# {result.get('title') or tab.title or '(untitled)'}", result.get("url") or tab.url]
    if selector:
        header.append(f"scope: `{selector}`")
    if result.get("truncated"):
        full = result.get("fullLength", 0)
        header.append(
            f"**Truncated** at {budget} of {full} chars. Narrow it with `selector`, or raise `max_chars`."
        )

    return "\n".join(header) + "\n\n---\n\n" + (result.get("content") or "(no text content)")


def query_dom(
    session: Session,
    tab: TabInfo,
    *,
    selector: str,
    limit: int = 30,
    attrs: list[str] | None = None,
    text_max: int = 200,
    visible_only: bool = False,
) -> str:
    """Return matching elements as a compact structured list."""
    result = session.run_payload(
        tab.id,
        "query_dom",
        {
            "selector": selector,
            "limit": limit,
            "attrs": attrs or [],
            "textMax": text_max,
            "visibleOnly": visible_only,
        },
    )

    elements: list[dict[str, Any]] = result.get("elements", [])
    lines = [
        f"`{selector}` matched {result.get('matched', 0)}, showing {len(elements)}"
        + (f", skipped {result['skippedInvisible']} invisible" if result.get("skippedInvisible") else "")
    ]
    if result.get("truncated"):
        lines.append(f"_More matches exist beyond limit={limit}._")
    if not elements:
        lines.append("\nNothing to show.")
        return "\n".join(lines)

    lines.append("")
    for element in elements:
        rect = element.get("rect", {})
        flags = [] if element.get("visible") else ["hidden"]
        head = f"[{element['index']}] <{element['tag']}>"
        if flags:
            head += f" ({', '.join(flags)})"
        head += f" {rect.get('w', 0)}x{rect.get('h', 0)} at {rect.get('x', 0)},{rect.get('y', 0)}"
        lines.append(head)
        if element.get("text"):
            lines.append(f"    text: {element['text']}")
        attributes = element.get("attrs") or {}
        if attributes:
            lines.append("    " + json.dumps(attributes, ensure_ascii=False))
    return "\n".join(lines)
