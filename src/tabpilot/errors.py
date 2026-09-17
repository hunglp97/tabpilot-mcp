"""Typed errors with actionable, OS-aware remedies.

Every failure an agent can hit gets a stable ``code`` so the agent can branch on
it, plus a ``remedy`` written for a human to paste into a shell.
"""

from __future__ import annotations

import platform
import shutil


class TabPilotError(Exception):
    """Base error. ``code`` is stable; ``remedy`` is advice, not machine-readable."""

    code = "ERROR"

    def __init__(self, message: str, remedy: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.remedy = remedy

    def to_text(self) -> str:
        out = f"[{self.code}] {self.message}"
        if self.remedy:
            out += f"\n\nHow to fix:\n{self.remedy}"
        return out


class NoTabError(TabPilotError):
    code = "NO_TAB"


class BridgeOffError(TabPilotError):
    """Chrome is not reachable: no CDP port open, or Apple Events denied."""

    code = "BRIDGE_OFF"


class JSError(TabPilotError):
    """The page threw, or returned a value that could not be serialised."""

    code = "JS_ERROR"


class TimeoutError_(TabPilotError):
    code = "TIMEOUT"


class UnsupportedByBackendError(TabPilotError):
    code = "UNSUPPORTED_BY_BACKEND"


class ElementNotFoundError(TabPilotError):
    code = "ELEMENT_NOT_FOUND"


def chrome_binary_candidates() -> list[str]:
    """Chrome/Chromium executable names, most-preferred first."""
    system = platform.system()
    if system == "Darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    if system == "Windows":
        return [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    return ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser"]


def find_chrome_binary() -> str | None:
    """First Chrome/Chromium on this machine, or None."""
    import os

    for candidate in chrome_binary_candidates():
        if os.path.sep in candidate or candidate.startswith("/"):
            if os.path.isfile(candidate):
                return candidate
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


def cdp_launch_hint(port: int, user_data_dir: str = "") -> str:
    """The exact command line that opens a debuggable Chrome on this OS.

    Two requirements bite people constantly, so they are spelled out:
    ``--user-data-dir`` must point outside the default profile (since Chrome 136
    the flag is silently ignored there), and Chrome must be fully quit first or
    the running instance swallows the flags and just opens a tab.
    """
    system = platform.system()
    binary = find_chrome_binary()

    if system == "Darwin":
        binary = binary or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        udd = user_data_dir or "$HOME/tabpilot-chrome"
        return (
            "Quit Chrome completely (Cmd+Q), then:\n"
            f'  "{binary}" \\\n'
            f"    --remote-debugging-port={port} \\\n"
            f'    --user-data-dir="{udd}"'
        )
    if system == "Windows":
        binary = binary or r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        udd = user_data_dir or r"%USERPROFILE%\tabpilot-chrome"
        return (
            "Quit Chrome completely first, then in cmd.exe:\n"
            "  taskkill /F /IM chrome.exe\n"
            f'  "{binary}" --remote-debugging-port={port} --user-data-dir="{udd}"'
        )

    binary = binary or "google-chrome"
    udd = user_data_dir or "$HOME/tabpilot-chrome"
    return (
        "On a desktop Linux session, quit Chrome completely, then:\n"
        f"  {binary} --remote-debugging-port={port} --user-data-dir=\"{udd}\"\n"
        "\n"
        "On a headless Ubuntu server, install the managed stack instead so Chrome\n"
        "stays up across reboots and crashes:\n"
        "  bash deploy/ubuntu/install.sh && tabpilot up\n"
        "  tabpilot doctor"
    )
