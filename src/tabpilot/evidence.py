"""Capturing proof that something happened.

On macOS it is tempting to shell out to ``screencapture``, but that only exists
on macOS and only photographs whatever is frontmost. Code written that way tends
to *silently skip* the capture on Linux, so a run on a headless server appears to
succeed while producing no evidence at all. Going through CDP instead captures
the tab that was asked for, on any OS, inside Xvfb, and whether or not that tab
is in front.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from .backends.base import Capability, TabInfo
from .errors import ElementNotFoundError
from .session import Session

MAX_LABEL_CHARS = 48


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value or "").strip("-").lower()
    return (cleaned[:MAX_LABEL_CHARS] or "tab").strip("-")


def screenshot(
    session: Session,
    tab: TabInfo,
    *,
    full_page: bool = False,
    selector: str | None = None,
    image_format: str = "png",
    quality: int = 80,
    label: str | None = None,
) -> tuple[str, Path, bytes]:
    """Capture a tab and save it.

    Returns ``(summary, path, image_bytes)``. The caller decides whether to inline
    the bytes; a saved path is useless to a client on a different machine, and
    inlined bytes are wasteful to one on the same machine.
    """
    session.require(Capability.SCREENSHOT)

    if image_format not in ("png", "jpeg"):
        raise ValueError("image_format must be 'png' or 'jpeg'")

    clip = None
    scope = "viewport"
    if selector:
        located = session.run_payload(tab.id, "locate", {"selector": selector, "requireVisible": False})
        rect = located.get("rect") or {}
        if not rect.get("w") or not rect.get("h"):
            raise ElementNotFoundError(
                f"`{selector}` has no size on screen, so there is nothing to capture.",
                remedy="It may be display:none, or inside a collapsed container.",
            )
        # locate reports viewport coordinates; a CDP clip is in page coordinates.
        clip = {
            "x": rect["x"] + located.get("scrollX", 0),
            "y": rect["y"] + located.get("scrollY", 0),
            "width": rect["w"],
            "height": rect["h"],
            "scale": 1,
        }
        scope = f"element `{selector}` ({rect['w']}x{rect['h']})"
    elif full_page:
        scope = "full page"

    data = session.backend.screenshot(
        tab.id,
        full_page=full_page,
        clip=clip,
        image_format=image_format,
        quality=quality,
        timeout_s=session.config.timeout_s,
    )

    directory = session.config.screenshot_dir
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(label or tab.title)}.{image_format}"
    path = directory / name
    path.write_bytes(data)

    size_kb = len(data) / 1024
    summary = (
        f"Captured {scope} of \"{tab.title or tab.url}\"\n"
        f"  saved: {path}\n"
        f"  size:  {size_kb:.0f} KB ({image_format})"
    )
    return summary, path, data
