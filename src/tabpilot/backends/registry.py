"""Backend selection.

CDP is tried first on every OS because it is strictly more capable. AppleScript
is the macOS fallback, used only when no debugging port answers — that way the
common case (Chrome already open, no debug port) still works, and the capable
case is preferred whenever it is available.
"""

from __future__ import annotations

from ..config import Config, is_macos
from ..errors import BridgeOffError, cdp_launch_hint
from .applescript import AppleScriptBackend
from .base import Backend
from .cdp import CDPBackend


def build_backend(config: Config) -> Backend:
    """Return a healthy backend, or raise :class:`BridgeOffError` explaining why not."""
    if config.backend == "cdp":
        backend = CDPBackend(config.cdp_host, config.cdp_port, config.timeout_s)
        backend.health()
        return backend

    if config.backend == "applescript":
        if not is_macos():
            raise BridgeOffError(
                "The AppleScript backend only exists on macOS.",
                remedy="Use --backend cdp (or leave it on auto) on Linux and Windows.",
            )
        backend = AppleScriptBackend(config.application_name, config.timeout_s)
        backend.health()
        return backend

    # auto
    cdp = CDPBackend(config.cdp_host, config.cdp_port, config.timeout_s)
    try:
        cdp.health()
        return cdp
    except BridgeOffError as cdp_error:
        if not is_macos():
            raise
        applescript = AppleScriptBackend(config.application_name, config.timeout_s)
        try:
            applescript.health()
            return applescript
        except BridgeOffError as applescript_error:
            raise BridgeOffError(
                "No browser reachable by either backend.\n"
                f"  cdp:         {cdp_error.message}\n"
                f"  applescript: {applescript_error.message}",
                remedy=cdp_launch_hint(config.cdp_port, str(config.resolved_user_data_dir)),
            ) from cdp_error


def probe_backends(config: Config) -> list[tuple[str, bool, str]]:
    """Report each backend as ``(name, healthy, detail)`` — used by ``tabpilot doctor``."""
    results: list[tuple[str, bool, str]] = []

    cdp = CDPBackend(config.cdp_host, config.cdp_port, config.timeout_s)
    try:
        cdp.health()
        version = cdp.browser_version()
        results.append(("cdp", True, version.get("Browser", "connected")))
    except BridgeOffError as exc:
        results.append(("cdp", False, exc.message))
    finally:
        cdp.close()

    if is_macos():
        applescript = AppleScriptBackend(config.application_name, config.timeout_s)
        try:
            applescript.health()
            results.append(("applescript", True, f"{config.application_name} is running"))
        except BridgeOffError as exc:
            results.append(("applescript", False, exc.message))
    else:
        results.append(("applescript", False, "macOS only"))

    return results
