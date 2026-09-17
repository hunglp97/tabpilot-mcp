"""Configuration: env vars first, CLI flags override."""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field, replace
from pathlib import Path

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_MAX_CHARS = 20_000
DEFAULT_TIMEOUT_MS = 20_000
DEFAULT_MATRIX_DELAY_MS = 80
DEFAULT_DISPLAY = ":99"
DEFAULT_VNC_PORT = 5901
DEFAULT_USER_DATA_DIR = "~/tabpilot-chrome"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    """Runtime settings. Construct via :meth:`from_env`, then :meth:`merge_args`."""

    backend: str = "auto"  # auto | cdp | applescript
    cdp_host: str = DEFAULT_CDP_HOST
    cdp_port: int = DEFAULT_CDP_PORT
    application_name: str = "Google Chrome"

    max_chars: int = DEFAULT_MAX_CHARS
    timeout_ms: int = DEFAULT_TIMEOUT_MS
    matrix_delay_ms: int = DEFAULT_MATRIX_DELAY_MS

    screenshot_dir: Path = field(default_factory=lambda: Path.home() / ".tabpilot" / "screenshots")
    #: always | never | auto — whether ``screenshot`` inlines the image bytes in
    #: the tool result. Over SSH the saved file sits on the remote host and is
    #: useless to the client, so remote configs should set ``always``.
    return_images: str = "auto"

    # Ubuntu managed-stack settings, used by `tabpilot up|down|logs|doctor`.
    display: str = DEFAULT_DISPLAY
    vnc_port: int = DEFAULT_VNC_PORT
    user_data_dir: str = DEFAULT_USER_DATA_DIR

    @classmethod
    def from_env(cls) -> "Config":
        default_return = "always" if _env_bool("TABPILOT_REMOTE") else "auto"
        return cls(
            backend=os.environ.get("TABPILOT_BACKEND", "auto").strip().lower(),
            cdp_host=os.environ.get("TABPILOT_CDP_HOST", DEFAULT_CDP_HOST),
            cdp_port=_env_int("TABPILOT_CDP_PORT", DEFAULT_CDP_PORT),
            application_name=os.environ.get("TABPILOT_APPLICATION_NAME", "Google Chrome"),
            max_chars=_env_int("TABPILOT_MAX_CHARS", DEFAULT_MAX_CHARS),
            timeout_ms=_env_int("TABPILOT_TIMEOUT_MS", DEFAULT_TIMEOUT_MS),
            matrix_delay_ms=_env_int("TABPILOT_MATRIX_DELAY_MS", DEFAULT_MATRIX_DELAY_MS),
            screenshot_dir=Path(
                os.environ.get("TABPILOT_SCREENSHOT_DIR", str(Path.home() / ".tabpilot" / "screenshots"))
            ).expanduser(),
            return_images=os.environ.get("TABPILOT_RETURN_IMAGES", default_return).strip().lower(),
            display=os.environ.get("TABPILOT_DISPLAY", DEFAULT_DISPLAY),
            vnc_port=_env_int("TABPILOT_VNC_PORT", DEFAULT_VNC_PORT),
            user_data_dir=os.environ.get("TABPILOT_USER_DATA_DIR", DEFAULT_USER_DATA_DIR),
        )

    def merge_args(self, args) -> "Config":
        """Overlay non-None argparse values onto this config."""
        overrides = {}
        for name in (
            "backend", "cdp_host", "cdp_port", "application_name", "max_chars",
            "timeout_ms", "matrix_delay_ms", "return_images", "display",
            "vnc_port", "user_data_dir",
        ):
            value = getattr(args, name, None)
            if value is not None:
                overrides[name] = value
        screenshot_dir = getattr(args, "screenshot_dir", None)
        if screenshot_dir is not None:
            overrides["screenshot_dir"] = Path(screenshot_dir).expanduser()
        return replace(self, **overrides) if overrides else self

    @property
    def timeout_s(self) -> float:
        return self.timeout_ms / 1000.0

    @property
    def cdp_base_url(self) -> str:
        return f"http://{self.cdp_host}:{self.cdp_port}"

    @property
    def resolved_user_data_dir(self) -> Path:
        return Path(self.user_data_dir).expanduser()

    def wants_inline_image(self, explicit: bool | None) -> bool:
        """Whether ``screenshot`` should inline image bytes into the result."""
        if explicit is not None:
            return explicit
        if self.return_images == "always":
            return True
        if self.return_images == "never":
            return False
        # auto: inline only when the client cannot reach our filesystem.
        return _env_bool("TABPILOT_REMOTE") or bool(os.environ.get("SSH_CONNECTION"))


def add_common_args(parser) -> None:
    """Register the flags that mirror ``TABPILOT_*`` env vars."""
    parser.add_argument("--backend", choices=["auto", "cdp", "applescript"], default=None,
                        help="force a backend instead of detecting one (env TABPILOT_BACKEND)")
    parser.add_argument("--cdp-host", default=None, help="CDP host (env TABPILOT_CDP_HOST)")
    parser.add_argument("--cdp-port", type=int, default=None, help="CDP port (env TABPILOT_CDP_PORT)")
    parser.add_argument("--application-name", default=None,
                        help="AppleScript application name, macOS only (env TABPILOT_APPLICATION_NAME)")
    parser.add_argument("--max-chars", type=int, default=None,
                        help="default content budget per read (env TABPILOT_MAX_CHARS)")
    parser.add_argument("--timeout-ms", type=int, default=None, help="default timeout (env TABPILOT_TIMEOUT_MS)")
    parser.add_argument("--matrix-delay-ms", type=int, default=None,
                        help="delay between matrix rows (env TABPILOT_MATRIX_DELAY_MS)")
    parser.add_argument("--screenshot-dir", default=None, help="where screenshots land (env TABPILOT_SCREENSHOT_DIR)")
    parser.add_argument("--return-images", choices=["auto", "always", "never"], default=None,
                        help="inline screenshot bytes in results (env TABPILOT_RETURN_IMAGES)")
    parser.add_argument("--display", default=None, help="X display for the Ubuntu stack (env TABPILOT_DISPLAY)")
    parser.add_argument("--vnc-port", type=int, default=None, help="VNC port (env TABPILOT_VNC_PORT)")
    parser.add_argument("--user-data-dir", default=None,
                        help="Chrome profile directory (env TABPILOT_USER_DATA_DIR)")


def is_linux() -> bool:
    return platform.system() == "Linux"


def is_macos() -> bool:
    return platform.system() == "Darwin"
