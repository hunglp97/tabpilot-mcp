"""The managed Ubuntu stack: a browser that stays logged in around the clock.

The usual shell script that ``pkill``s the old processes and backgrounds new ones
with ``&`` has three problems that only show up later: everything dies on reboot,
a crashed Chrome stays dead with nobody watching, and the logs land in ``/tmp``
where they are rotated away before anyone reads them. Four user units with
``Restart=always`` and an ordering chain fix all three, and ``journalctl`` keeps
the history.

The unit files are generated from the templates below rather than shipped as
data files, so there is one source of truth whether TabPilot was installed from
a checkout, a wheel, or ``uvx``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import Config
from .errors import TabPilotError, find_chrome_binary

UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
ENV_DIR = Path.home() / ".config" / "tabpilot"
ENV_FILE = ENV_DIR / "stack.env"

#: Start order matters: the display must exist before a window manager, and both
#: before Chrome. ``tabpilot up`` starts the leaves and systemd pulls the rest in.
UNITS = ("tabpilot-xvfb", "tabpilot-wm", "tabpilot-chrome", "tabpilot-vnc")
LEAF_UNITS = ("tabpilot-chrome", "tabpilot-vnc")

_XVFB = """\
[Unit]
Description=TabPilot virtual display (Xvfb)
Documentation=https://github.com/lephuochung/tabpilot-mcp

[Service]
EnvironmentFile={env_file}
ExecStart=/bin/sh -c 'exec Xvfb "$TABPILOT_DISPLAY" -screen 0 "$TABPILOT_SCREEN" -ac +extension GLX +render -noreset'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
"""

_WM = """\
[Unit]
Description=TabPilot window manager (Openbox)
After=tabpilot-xvfb.service
Requires=tabpilot-xvfb.service

[Service]
EnvironmentFile={env_file}
ExecStartPre=/bin/sh -c 'for i in $(seq 1 30); do xdpyinfo -display "$TABPILOT_DISPLAY" >/dev/null 2>&1 && exit 0; sleep 0.5; done; exit 1'
ExecStart=/bin/sh -c 'DISPLAY="$TABPILOT_DISPLAY" exec openbox'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
"""

# --remote-debugging-address is pinned to loopback on purpose. Exposing the port
# on 0.0.0.0 hands full control of a browser holding live logins to anyone who
# can route to the host; reach it through an SSH tunnel instead.
#
# --password-store=basic matters specifically on servers: without it Chrome tries
# to reach a system keyring that is not running and blocks on start.
_CHROME = """\
[Unit]
Description=TabPilot Chrome (CDP on 127.0.0.1)
After=tabpilot-wm.service
Requires=tabpilot-wm.service
# StartLimit* are Unit-level directives. Put under the service section they are
# ignored, and systemd mentions it only in the journal, so the backoff looks
# configured while doing nothing.
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
EnvironmentFile={env_file}
ExecStartPre=/bin/sh -c 'mkdir -p "$TABPILOT_USER_DATA_DIR"'
ExecStart=/bin/sh -c 'DISPLAY="$TABPILOT_DISPLAY" exec "$TABPILOT_CHROME_BIN" \\
  --remote-debugging-port="$TABPILOT_CDP_PORT" \\
  --remote-debugging-address=127.0.0.1 \\
  --user-data-dir="$TABPILOT_USER_DATA_DIR" \\
  --window-size="$TABPILOT_WINDOW_SIZE" \\
  --no-first-run \\
  --no-default-browser-check \\
  --disable-dev-shm-usage \\
  --disable-gpu \\
  --password-store=basic \\
  --disable-features=TranslateUI,MediaRouter \\
  $TABPILOT_EXTRA_FLAGS \\
  "$TABPILOT_START_URL"'
Restart=always
RestartSec=5
# Chrome shares this host with whatever else runs on it. MemoryHigh throttles by
# applying reclaim pressure rather than inviting the OOM killer, so a runaway
# browser slows down instead of dying mid-workflow -- and its neighbours keep
# their memory. Empty means no limit.
MemoryHigh={memory_high}

[Install]
WantedBy=default.target
"""

# -localhost + -rfbauth by default. An unauthenticated VNC server on a browser
# full of live sessions is the single largest risk in this whole stack.
_VNC = """\
[Unit]
Description=TabPilot VNC (loopback only)
After=tabpilot-xvfb.service
Requires=tabpilot-xvfb.service

[Service]
EnvironmentFile={env_file}
ExecStart=/bin/sh -c 'exec x11vnc -display "$TABPILOT_DISPLAY" -rfbport "$TABPILOT_VNC_PORT" \\
  -shared -forever -localhost $TABPILOT_VNC_AUTH -o /dev/stdout'
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""

TEMPLATES = {
    "tabpilot-xvfb": _XVFB,
    "tabpilot-wm": _WM,
    "tabpilot-chrome": _CHROME,
    "tabpilot-vnc": _VNC,
}


def render_unit(name: str, *, env_file: "Path | str | None" = None, memory_high: str = "") -> str:
    """Render one unit file.

    Every caller goes through here, production and tests alike, so adding a
    field to a template cannot leave one of them formatting with a stale set of
    keys.
    """
    template = TEMPLATES[name]
    fields: dict[str, object] = {"env_file": env_file if env_file is not None else ENV_FILE}
    if "{memory_high}" in template:
        # "infinity" is systemd's own spelling for no limit.
        fields["memory_high"] = memory_high or "infinity"
    return template.format(**fields)


def _require_systemd() -> None:
    if not shutil.which("systemctl"):
        raise TabPilotError(
            "systemctl is not available, so the managed stack cannot be controlled here.",
            remedy="This applies to Linux with systemd. On macOS, just launch Chrome with a debug port.",
        )


def _systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, timeout=60
    )
    if check and proc.returncode != 0:
        raise TabPilotError(
            f"systemctl --user {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}",
            remedy="Inspect it with: journalctl --user -u tabpilot-chrome -n 50 --no-pager",
        )
    return proc


def render_env(config: Config, *, vnc_insecure: bool = False, start_url: str = "about:blank",
               screen: str = "1920x1080x24", extra_flags: str = "") -> str:
    """Build the EnvironmentFile shared by all four units."""
    chrome = find_chrome_binary()
    if not chrome:
        raise TabPilotError(
            "No Chrome or Chromium binary found.",
            remedy=(
                "wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb\n"
                "sudo apt install -y ./google-chrome-stable_current_amd64.deb"
            ),
        )

    if vnc_insecure:
        vnc_auth = "-nopw"
    else:
        passwd = Path.home() / ".vnc" / "passwd"
        if not passwd.exists():
            raise TabPilotError(
                f"No VNC password file at {passwd}.",
                remedy=(
                    "Create one (it is what stops anyone reaching the port from using your\n"
                    "logged-in browser):\n"
                    "  mkdir -p ~/.vnc && x11vnc -storepasswd ~/.vnc/passwd\n"
                    "\n"
                    "Or, accepting the risk, install with --vnc-insecure."
                ),
            )
        vnc_auth = f"-rfbauth {passwd}"

    window = "x".join(screen.split("x")[:2])
    lines = [
        "# Generated by `tabpilot install-stack`. Edit and then: tabpilot up --restart",
        f"TABPILOT_DISPLAY={config.display}",
        f"TABPILOT_SCREEN={screen}",
        f"TABPILOT_CHROME_BIN={chrome}",
        f"TABPILOT_CDP_PORT={config.cdp_port}",
        f"TABPILOT_USER_DATA_DIR={config.resolved_user_data_dir}",
        f"TABPILOT_WINDOW_SIZE={window}",
        f"TABPILOT_VNC_PORT={config.vnc_port}",
        f"TABPILOT_VNC_AUTH={vnc_auth}",
        f"TABPILOT_START_URL={start_url}",
        f"TABPILOT_EXTRA_FLAGS={extra_flags}",
    ]
    return "\n".join(lines) + "\n"


def install(config: Config, *, vnc_insecure: bool = False, start_url: str = "about:blank",
            screen: str = "1920x1080x24", extra_flags: str = "", enable: bool = True,
            memory_high: str = "") -> str:
    """Write the env file and unit files, reload systemd, and enable the units."""
    _require_systemd()

    ENV_DIR.mkdir(parents=True, exist_ok=True)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)

    env_text = render_env(
        config, vnc_insecure=vnc_insecure, start_url=start_url,
        screen=screen, extra_flags=extra_flags,
    )
    ENV_FILE.write_text(env_text, encoding="utf-8")
    ENV_FILE.chmod(0o600)

    written = []
    for name in TEMPLATES:
        path = UNIT_DIR / f"{name}.service"
        path.write_text(render_unit(name, memory_high=memory_high), encoding="utf-8")
        written.append(path)

    _systemctl("daemon-reload")

    lines = [f"Wrote {ENV_FILE} and {len(written)} unit file(s) to {UNIT_DIR}."]

    if enable:
        _systemctl("enable", *[f"{name}.service" for name in UNITS])
        lines.append("Enabled all four units.")

        # Without linger, the whole stack is torn down the moment the installing
        # user logs out — which is the opposite of what a 24/7 browser is for.
        if shutil.which("loginctl"):
            user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
            proc = subprocess.run(
                ["loginctl", "enable-linger", user], capture_output=True, text=True
            )
            if proc.returncode == 0:
                lines.append(f"Enabled linger for {user}, so the stack survives logout.")
            else:
                lines.append(
                    f"Could not enable linger automatically. Without it the stack stops when you\n"
                    f"log out. Run: sudo loginctl enable-linger {user}"
                )

    lines.append("\nNext: tabpilot up && tabpilot doctor")
    return "\n".join(lines)


def up(config: Config, *, restart: bool = False, with_vnc: bool = True) -> str:
    """Start (or restart) the stack.

    ``with_vnc=False`` brings up Chrome and its display without VNC, which is
    what a host wants when nobody needs to sign in to sites by hand — and what a
    non-interactive install has to do, since there is no password to prompt for.
    """
    _require_systemd()
    if not (UNIT_DIR / "tabpilot-chrome.service").exists():
        raise TabPilotError(
            "The managed stack is not installed yet.",
            remedy="tabpilot install-stack    # then: tabpilot up",
        )
    units = LEAF_UNITS if with_vnc else tuple(u for u in LEAF_UNITS if u != "tabpilot-vnc")
    action = "restart" if restart else "start"
    _systemctl(action, *[f"{name}.service" for name in units])
    return f"{action.capitalize()}ed the stack.\n\n{status(config)}"


def down(config: Config) -> str:
    """Stop the stack, leaves first so Chrome exits cleanly."""
    _require_systemd()
    for name in (*LEAF_UNITS, "tabpilot-wm", "tabpilot-xvfb"):
        _systemctl("stop", f"{name}.service", check=False)
    return "Stopped the stack."


def status(config: Config) -> str:
    _require_systemd()
    lines = []
    for name in UNITS:
        proc = _systemctl("is-active", f"{name}.service", check=False)
        state = (proc.stdout or proc.stderr).strip() or "unknown"
        lines.append(f"  {name}: {state}")
    lines.append(f"\n  cdp:  {config.cdp_base_url}")
    lines.append(f"  vnc:  127.0.0.1:{config.vnc_port} (loopback; tunnel in with ssh -L)")
    return "Managed stack:\n" + "\n".join(lines)


def logs(unit: str = "tabpilot-chrome", lines: int = 50, follow: bool = False) -> int:
    """Stream unit logs. Returns the exit code of ``journalctl``."""
    if not unit.startswith("tabpilot-"):
        unit = f"tabpilot-{unit}"
    if unit not in UNITS:
        raise TabPilotError(f"Unknown unit {unit!r}. Choose from: {', '.join(UNITS)}")
    if not shutil.which("journalctl"):
        raise TabPilotError("journalctl is not available on this system.")
    command = ["journalctl", "--user", "-u", unit, "-n", str(lines)]
    command.append("-f") if follow else command.append("--no-pager")
    return subprocess.call(command)


def uninstall() -> str:
    """Disable and remove the units, leaving the Chrome profile alone."""
    _require_systemd()
    _systemctl("disable", "--now", *[f"{name}.service" for name in UNITS], check=False)
    removed = []
    for name in UNITS:
        path = UNIT_DIR / f"{name}.service"
        if path.exists():
            path.unlink()
            removed.append(path.name)
    _systemctl("daemon-reload", check=False)
    return (
        f"Removed {len(removed)} unit file(s). {ENV_FILE} and the Chrome profile were left in place "
        "so your logged-in sessions survive."
    )
