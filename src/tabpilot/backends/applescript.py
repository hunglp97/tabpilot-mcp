"""AppleScript backend — a macOS fallback for when restarting Chrome is not an option.

This exists so TabPilot still works against the Chrome someone already has open,
with all their sessions, without asking them to quit it and relaunch with a
debugging port. The trade is real and deliberate: AppleScript can only run
JavaScript in a tab. It cannot capture a specific tab's pixels, it cannot
dispatch trusted input events, and it cannot await a promise. Anything needing
those capabilities is refused with a pointer to CDP rather than faked.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from ..errors import BridgeOffError, JSError, NoTabError, TimeoutError_
from .base import Backend, Capability, TabInfo

_TAB_ID = re.compile(r"^w(\d+):t(\d+)$")

#: A separator unlikely to appear in a page title.
_FIELD = "\u001f"
_RECORD = "\u001e"


def escape_applescript_string(value: str) -> str:
    """Escape ``value`` for use inside an AppleScript double-quoted literal.

    Hand-written ``osascript -e`` strings are the single largest source of
    breakage in browser automation, because the payload passes through the shell,
    AppleScript and JavaScript, each with its own escaping rules. Centralising it
    here means a JS payload containing quotes, backslashes or newlines is no
    longer something callers have to think about.
    """
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    return out.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


class AppleScriptBackend(Backend):
    name = "applescript"
    capabilities = frozenset({
        Capability.EVAL,
        Capability.NAVIGATE,
        Capability.OPEN_CLOSE,
        Capability.ACTIVATE,
    })

    def __init__(self, application_name: str = "Google Chrome", timeout_s: float = 20.0) -> None:
        self.application_name = application_name
        self.timeout_s = timeout_s

    # --- osascript plumbing --------------------------------------------------

    def _run(self, script: str, timeout_s: float | None = None) -> str:
        timeout = self.timeout_s if timeout_s is None else timeout_s
        try:
            proc = subprocess.run(
                ["osascript", "-"],
                input=script,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise BridgeOffError("`osascript` is missing — the AppleScript backend needs macOS.") from exc
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError_(f"osascript did not return within {timeout:.1f}s.") from exc

        if proc.returncode != 0:
            raise self._classify(proc.stderr.strip())
        return proc.stdout.strip()

    def _classify(self, stderr: str) -> Exception:
        lowered = stderr.lower()
        if "not authorized" in lowered or "-1743" in stderr:
            return BridgeOffError(
                f"macOS denied Apple Events to {self.application_name}.",
                remedy=(
                    "System Settings > Privacy & Security > Automation, then tick your terminal's\n"
                    f"entry for \"{self.application_name}\". Relaunch the terminal afterwards."
                ),
            )
        if "execute" in lowered and "javascript" in lowered:
            return BridgeOffError(
                "Chrome refused to execute JavaScript from Apple Events.",
                remedy=(
                    "In Chrome: View > Developer > Allow JavaScript from Apple Events.\n"
                    "If that menu is absent, enable the Develop menu first, or use the CDP\n"
                    "backend instead (--backend cdp), which needs no such permission."
                ),
            )
        if "application isn't running" in lowered or "-600" in stderr:
            return BridgeOffError(f"{self.application_name} is not running.")
        return JSError(stderr or "osascript failed without a message")

    def health(self) -> None:
        script = f'tell application "System Events" to return (exists process "{self.application_name}")'
        if self._run(script, timeout_s=min(self.timeout_s, 8.0)).lower() != "true":
            raise BridgeOffError(
                f"{self.application_name} is not running.",
                remedy=f"Open {self.application_name} and try again.",
            )

    # --- tabs ----------------------------------------------------------------

    def _resolve(self, tab_id: str) -> tuple[int, int]:
        match = _TAB_ID.match(tab_id or "")
        if not match:
            raise NoTabError(
                f"{tab_id!r} is not an AppleScript tab handle. Expected the `w<window>:t<tab>` "
                "form returned by list_tabs."
            )
        return int(match.group(1)), int(match.group(2))

    def list_tabs(self) -> list[TabInfo]:
        script = f'''
set out to ""
tell application "{self.application_name}"
  set windowCount to count of windows
  repeat with w from 1 to windowCount
    set activeIndex to active tab index of window w
    set tabCount to count of tabs of window w
    repeat with t from 1 to tabCount
      set theTab to tab t of window w
      set isActive to "0"
      if t is activeIndex and w is 1 then set isActive to "1"
      set out to out & "w" & w & ":t" & t & "{_FIELD}" & isActive & "{_FIELD}" & (title of theTab) & "{_FIELD}" & (URL of theTab) & "{_RECORD}"
    end repeat
  end repeat
end tell
return out
'''
        raw = self._run(script)
        tabs: list[TabInfo] = []
        for record in raw.split(_RECORD):
            record = record.strip()
            if not record:
                continue
            parts = record.split(_FIELD)
            if len(parts) < 4:
                continue
            tab_id, is_active, title, url = parts[0], parts[1], parts[2], _FIELD.join(parts[3:])
            tabs.append(TabInfo(id=tab_id, title=title, url=url, active=is_active == "1"))
        return tabs

    def open_tab(self, url: str, activate: bool = True) -> TabInfo:
        escaped = escape_applescript_string(url)
        script = f'''
tell application "{self.application_name}"
  if (count of windows) is 0 then
    make new window
    set URL of active tab of window 1 to "{escaped}"
    set newIndex to active tab index of window 1
  else
    tell window 1 to make new tab with properties {{URL:"{escaped}"}}
    set newIndex to count of tabs of window 1
  end if
  {"activate" if activate else ""}
  return "w1:t" & newIndex
end tell
'''
        tab_id = self._run(script)
        return TabInfo(id=tab_id, title="", url=url, active=activate)

    def close_tab(self, tab_id: str) -> None:
        window, tab = self._resolve(tab_id)
        self._run(f'tell application "{self.application_name}" to close tab {tab} of window {window}')

    def activate_tab(self, tab_id: str) -> None:
        window, tab = self._resolve(tab_id)
        self._run(f'''
tell application "{self.application_name}"
  set active tab index of window {window} to {tab}
  set index of window {window} to 1
  activate
end tell
''')

    def navigate(self, tab_id: str, url: str) -> None:
        window, tab = self._resolve(tab_id)
        escaped = escape_applescript_string(url)
        self._run(
            f'tell application "{self.application_name}" to set URL of tab {tab} '
            f'of window {window} to "{escaped}"'
        )

    # --- evaluation ----------------------------------------------------------

    def eval_js(self, tab_id: str, expression: str, timeout_s: float) -> Any:
        window, tab = self._resolve(tab_id)
        wrapped = _wrap_for_json(expression)
        escaped = escape_applescript_string(wrapped)
        script = (
            f'tell application "{self.application_name}" to return (execute tab {tab} '
            f'of window {window} javascript "{escaped}")'
        )
        raw = self._run(script, timeout_s=timeout_s)
        if not raw:
            return None
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            # The page returned something JSON.stringify could not represent.
            return raw
        if not envelope.get("ok"):
            raise JSError(envelope.get("error") or "page threw an unknown error")
        if envelope.get("pending"):
            raise JSError(
                "That expression returned a promise, which the AppleScript backend cannot await.",
                remedy="Use the CDP backend for async expressions: --backend cdp (or TABPILOT_BACKEND=cdp).",
            )
        return envelope.get("value")


def _wrap_for_json(expression: str) -> str:
    """Wrap an expression so AppleScript always gets a single JSON string back.

    ``execute javascript`` coerces return values into AppleScript types, which
    flattens objects and loses type information. Serialising on the page side and
    parsing on ours keeps the value intact and carries page exceptions across as
    data rather than as an opaque osascript failure.
    """
    return (
        "(function(){try{var __v=(" + expression + ");"
        "if(__v&&typeof __v.then==='function'){return JSON.stringify({ok:true,pending:true});}"
        "return JSON.stringify({ok:true,value:__v===undefined?null:__v});}"
        "catch(e){return JSON.stringify({ok:false,error:String((e&&e.message)||e)});}})()"
    )
