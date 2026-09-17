"""``tabpilot doctor`` — one command that says why the browser is unreachable.

Diagnosing this by hand means remembering to curl ``/json/version``, to check
whether Xvfb is up, to notice that Chrome is running as root without
``--no-sandbox``, and — the one nobody remembers until two hundred screenshots
come back full of empty boxes — to check that the server has fonts installed.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .backends.registry import build_backend, probe_backends
from .config import Config, is_linux, is_macos
from .errors import TabPilotError, cdp_launch_hint, find_chrome_binary

OK = "ok"
WARN = "warn"
FAIL = "fail"

_MARK = {OK: "✓", WARN: "!", FAIL: "✗"}

#: Below this, Chrome tabs crash in containers with "Out of memory".
MIN_DEV_SHM_MB = 256


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""

    def render(self) -> str:
        line = f"  {_MARK[self.status]} {self.name}: {self.detail}"
        if self.fix and self.status != OK:
            indented = "\n".join(f"      {row}" for row in self.fix.splitlines())
            line += "\n" + indented
        return line


def _run(command: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return 127, ""


def _process_running(needle: str) -> bool:
    code, out = _run(["pgrep", "-fa", needle])
    return code == 0 and bool(out.strip())


# --- individual checks -------------------------------------------------------


def check_environment() -> list[Check]:
    return [
        Check("platform", OK, f"{platform.system()} {platform.release()} ({platform.machine()})"),
        Check("python", OK if sys.version_info >= (3, 10) else FAIL,
              platform.python_version(),
              "TabPilot needs Python 3.10 or newer."),
    ]


def check_chrome() -> list[Check]:
    binary = find_chrome_binary()
    if not binary:
        return [Check(
            "chrome binary", FAIL, "not found on PATH or in the usual locations",
            "Ubuntu:\n"
            "  wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb\n"
            "  sudo apt install -y ./google-chrome-stable_current_amd64.deb\n"
            "Or install the distro build: sudo apt install -y chromium-browser",
        )]

    checks = [Check("chrome binary", OK, binary)]
    code, out = _run([binary, "--version"])
    if code == 0 and out:
        checks.append(Check("chrome version", OK, out.splitlines()[0]))
    return checks


def check_cdp(config: Config) -> list[Check]:
    """Report each transport.

    A missing CDP port is only fatal when nothing else can reach the browser. On
    macOS with a healthy AppleScript fallback TabPilot still works, so that is a
    warning about reduced capability — calling it a failure sends someone off
    debugging a connection that was never needed.
    """
    probes = {name: (healthy, detail) for name, healthy, detail in probe_backends(config)}
    cdp_healthy, cdp_detail = probes.get("cdp", (False, "not probed"))
    applescript_healthy, applescript_detail = probes.get("applescript", (False, "not probed"))
    have_fallback = is_macos() and applescript_healthy

    launch_hint = cdp_launch_hint(config.cdp_port, str(config.resolved_user_data_dir))
    checks: list[Check] = []

    if cdp_healthy:
        cdp_status, cdp_fix = OK, ""
    elif have_fallback:
        cdp_status = WARN
        cdp_detail += " — using the AppleScript fallback, which has no screenshots and no trusted input"
        cdp_fix = launch_hint
    else:
        cdp_status, cdp_fix = FAIL, launch_hint

    checks.append(Check(f"cdp {config.cdp_host}:{config.cdp_port}", cdp_status, cdp_detail, cdp_fix))

    if is_macos():
        checks.append(Check(
            "applescript backend",
            OK if applescript_healthy else WARN,
            applescript_detail,
            f"Open {config.application_name}, or use CDP instead.",
        ))
    else:
        checks.append(Check("applescript backend", OK, "not applicable (macOS only)"))

    return checks


def check_backend_roundtrip(config: Config) -> list[Check]:
    """Actually talk to the browser: evaluate, list tabs, confirm capabilities."""
    try:
        backend = build_backend(config)
    except TabPilotError as exc:
        return [Check("backend selection", FAIL, exc.message, exc.remedy or "")]

    checks = [Check("backend selected", OK, backend.describe())]
    try:
        open_tabs = backend.list_tabs()
        checks.append(Check("tabs", OK, f"{len(open_tabs)} open"))
        if open_tabs:
            value = backend.eval_js(open_tabs[0].id, "1 + 1", min(config.timeout_s, 10.0))
            checks.append(Check(
                "javascript round-trip",
                OK if value == 2 else WARN,
                f"1 + 1 returned {value!r}",
            ))
        else:
            checks.append(Check(
                "javascript round-trip", WARN, "skipped, no tabs open",
                "Open any page so the round-trip can be verified.",
            ))
    except TabPilotError as exc:
        checks.append(Check("browser conversation", FAIL, exc.message, exc.remedy or ""))
    finally:
        backend.close()
    return checks


def check_screenshots(config: Config) -> list[Check]:
    directory = config.screenshot_dir
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".tabpilot-write-probe"
        probe.write_bytes(b"x")
        probe.unlink()
        return [Check("screenshot dir", OK, str(directory))]
    except OSError as exc:
        return [Check(
            "screenshot dir", FAIL, f"{directory} is not writable — {exc}",
            "Point somewhere else: TABPILOT_SCREENSHOT_DIR=/path/you/own",
        )]


def check_linux_display(config: Config) -> list[Check]:
    checks: list[Check] = []

    display = os.environ.get("DISPLAY")
    if display:
        checks.append(Check("DISPLAY", OK, display))
    else:
        checks.append(Check(
            "DISPLAY", WARN, "unset",
            f"Headless Chrome works without it, but the managed stack expects {config.display}.\n"
            f"  export DISPLAY={config.display}",
        ))

    xvfb = _process_running(f"Xvfb {config.display}")
    checks.append(Check(
        "Xvfb", OK if xvfb else WARN,
        f"running on {config.display}" if xvfb else f"nothing on {config.display}",
        "tabpilot up    # or: systemctl --user start tabpilot-chrome.service",
    ))

    vnc = _process_running(f"x11vnc.*{config.vnc_port}")
    checks.append(Check(
        "x11vnc", OK if vnc else WARN,
        f"listening on {config.vnc_port}" if vnc else f"nothing on {config.vnc_port}",
        "Only needed to log into sites by hand. Start it with: tabpilot up",
    ))

    if vnc:
        code, out = _run(["pgrep", "-fa", "x11vnc"])
        if code == 0 and "-nopw" in out and "-localhost" not in out:
            checks.append(Check(
                "vnc exposure", FAIL,
                f"x11vnc is running with -nopw and no -localhost on port {config.vnc_port}",
                "That is an unauthenticated remote desktop onto a browser holding live logins.\n"
                "Anyone who can reach the port owns those accounts.\n"
                "  x11vnc -localhost -rfbauth ~/.vnc/passwd ...\n"
                "  ssh -N -L 5901:127.0.0.1:5901 <this-host>   # then connect to localhost",
            ))
        else:
            checks.append(Check("vnc exposure", OK, "bound to loopback or password-protected"))

    return checks


def check_linux_fonts() -> list[Check]:
    """Missing fonts turn screenshot evidence into rows of empty boxes.

    A minimal server image ships almost no fonts. Chrome renders anyway, so
    nothing fails and nothing warns — the text simply comes out as tofu, and it
    is only noticed much later, in the screenshots that were supposed to be proof.
    """
    if not shutil.which("fc-list"):
        return [Check(
            "fonts", WARN, "fontconfig is not installed, so fonts cannot be verified",
            "sudo apt install -y fontconfig fonts-liberation fonts-noto-core fonts-noto-cjk",
        )]

    code, out = _run(["fc-list"])
    total = len(out.splitlines()) if code == 0 else 0

    missing = []
    for language, label in (("vi", "Vietnamese"), ("ja", "Japanese"), ("zh-cn", "Chinese")):
        code, out = _run(["fc-list", f":lang={language}", "family"])
        if code != 0 or not out.strip():
            missing.append(label)

    if total == 0:
        return [Check(
            "fonts", FAIL, "no fonts installed at all — every screenshot will be empty boxes",
            "sudo apt install -y fonts-liberation fonts-noto-core fonts-noto-cjk",
        )]
    if missing:
        return [Check(
            "fonts", WARN, f"{total} installed, but no coverage for: {', '.join(missing)}",
            "Text in those scripts will render as empty boxes in screenshots.\n"
            "  sudo apt install -y fonts-noto-core fonts-noto-cjk",
        )]
    return [Check("fonts", OK, f"{total} fonts, including Vietnamese and CJK coverage")]


def check_linux_runtime(config: Config) -> list[Check]:
    checks: list[Check] = []

    try:
        stats = os.statvfs("/dev/shm")
        size_mb = (stats.f_blocks * stats.f_frsize) / (1024 * 1024)
        checks.append(Check(
            "/dev/shm", OK if size_mb >= MIN_DEV_SHM_MB else WARN,
            f"{size_mb:.0f} MB",
            f"Under {MIN_DEV_SHM_MB} MB, Chrome tabs die with out-of-memory crashes.\n"
            "The managed stack already passes --disable-dev-shm-usage.\n"
            "In Docker, raise it with: --shm-size=1g",
        ))
    except OSError:
        checks.append(Check("/dev/shm", WARN, "could not be inspected"))

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        checks.append(Check(
            "user", WARN, "running as root",
            "Chrome's sandbox refuses to start as root, which forces --no-sandbox and\n"
            "removes a real security boundary around a browser holding live logins.\n"
            "Run the stack as an unprivileged user instead.",
        ))
    else:
        checks.append(Check("user", OK, f"uid {os.geteuid()}" if hasattr(os, "geteuid") else "non-root"))

    profile = config.resolved_user_data_dir
    if profile.exists():
        detail = str(profile)
        status = OK
        fix = ""
        if str(profile).startswith(("/tmp", "/var/tmp")):
            status = WARN
            detail += " (inside a temp directory)"
            fix = ("Temp directories get cleaned, taking every logged-in session with them.\n"
                   "  TABPILOT_USER_DATA_DIR=$HOME/tabpilot-chrome")
        checks.append(Check("chrome profile", status, detail, fix))
    else:
        checks.append(Check(
            "chrome profile", WARN, f"{profile} does not exist yet",
            "It is created on first launch. You will need to sign in to your sites once inside it.",
        ))

    return checks


def check_systemd(config: Config) -> list[Check]:
    if not shutil.which("systemctl"):
        return []
    units = ["tabpilot-xvfb", "tabpilot-wm", "tabpilot-chrome", "tabpilot-vnc"]
    checks: list[Check] = []
    any_installed = False
    for unit in units:
        code, out = _run(["systemctl", "--user", "is-active", f"{unit}.service"])
        state = out.strip() or "unknown"
        if state in ("inactive", "failed", "active", "activating"):
            any_installed = True
        checks.append(Check(
            f"unit {unit}",
            OK if state == "active" else (WARN if state != "failed" else FAIL),
            state,
            f"journalctl --user -u {unit} -n 50 --no-pager",
        ))
    if not any_installed:
        return [Check(
            "systemd units", WARN, "not installed",
            "bash deploy/ubuntu/install.sh   # installs the units, then: tabpilot up",
        )]
    return checks


# --- report ------------------------------------------------------------------


def run(config: Config) -> tuple[str, int]:
    """Run every applicable check. Returns ``(report, exit_code)``."""
    sections: list[tuple[str, list[Check]]] = [
        ("Environment", check_environment()),
        ("Chrome", check_chrome()),
        ("Transport", check_cdp(config)),
        ("Browser", check_backend_roundtrip(config)),
        ("Evidence", check_screenshots(config)),
    ]

    if is_linux():
        sections.append(("Display (Linux)", check_linux_display(config)))
        sections.append(("Fonts (Linux)", check_linux_fonts()))
        sections.append(("Runtime (Linux)", check_linux_runtime(config)))
        systemd = check_systemd(config)
        if systemd:
            sections.append(("Managed stack", systemd))

    lines = ["TabPilot doctor", ""]
    failures = warnings = 0
    for title, checks in sections:
        if not checks:
            continue
        lines.append(title)
        for check in checks:
            lines.append(check.render())
            if check.status == FAIL:
                failures += 1
            elif check.status == WARN:
                warnings += 1
        lines.append("")

    if failures:
        lines.append(f"{failures} problem(s) and {warnings} warning(s). Fix the ✗ lines first.")
    elif warnings:
        lines.append(f"Usable, with {warnings} warning(s).")
    else:
        lines.append("Everything checks out.")

    return "\n".join(lines), (1 if failures else 0)
