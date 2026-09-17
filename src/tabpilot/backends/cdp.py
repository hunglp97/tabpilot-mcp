"""Chrome DevTools Protocol backend — the preferred transport on every OS.

CDP is a strict superset of what AppleScript can do: it captures a specific
tab's pixels whether or not that tab is frontmost, and it dispatches input
events that carry ``isTrusted``, which React and Vue cannot tell apart from a
real human. It also works headless, which is what makes an Ubuntu server a
viable place to run a logged-in browser around the clock.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..errors import BridgeOffError, JSError, NoTabError, TimeoutError_, cdp_launch_hint
from ._ws import WebSocket
from .base import Backend, Capability, TabInfo

#: Named keys mapped to the fields ``Input.dispatchKeyEvent`` wants.
KEY_MAP: dict[str, dict[str, Any]] = {
    "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"},
    "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9, "text": "\t"},
    "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
    "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    "Delete": {"key": "Delete", "code": "Delete", "windowsVirtualKeyCode": 46},
    "ArrowDown": {"key": "ArrowDown", "code": "ArrowDown", "windowsVirtualKeyCode": 40},
    "ArrowUp": {"key": "ArrowUp", "code": "ArrowUp", "windowsVirtualKeyCode": 38},
    "ArrowLeft": {"key": "ArrowLeft", "code": "ArrowLeft", "windowsVirtualKeyCode": 37},
    "ArrowRight": {"key": "ArrowRight", "code": "ArrowRight", "windowsVirtualKeyCode": 39},
    "Space": {"key": " ", "code": "Space", "windowsVirtualKeyCode": 32, "text": " "},
}

#: Target types worth showing an agent. Service workers and extension pages are noise.
_USEFUL_TARGET_TYPES = {"page"}

#: How long to wait for a closed target to actually disappear.
CLOSE_CONFIRM_TIMEOUT_S = 3.0
CLOSE_POLL_S = 0.05


class CDPBackend(Backend):
    name = "cdp"
    capabilities = frozenset({
        Capability.EVAL,
        Capability.NAVIGATE,
        Capability.OPEN_CLOSE,
        Capability.ACTIVATE,
        Capability.SCREENSHOT,
        Capability.TRUSTED_INPUT,
    })

    def __init__(self, host: str = "127.0.0.1", port: int = 9222, timeout_s: float = 20.0) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s
        self._sockets: dict[str, WebSocket] = {}
        self._next_id = 0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    # --- HTTP endpoints ------------------------------------------------------

    def _http(self, path: str, method: str = "GET") -> Any:
        url = f"{self.base_url}{path}"
        request = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise BridgeOffError(f"Chrome rejected {method} {path} with HTTP {exc.code}.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise BridgeOffError(
                f"No Chrome answering on {self.base_url} — {exc}",
                remedy=cdp_launch_hint(self.port),
            ) from exc
        body = body.strip()
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    def health(self) -> None:
        version = self._http("/json/version")
        if not isinstance(version, dict) or "Browser" not in version:
            raise BridgeOffError(
                f"Something is listening on {self.base_url} but it does not speak CDP.",
                remedy=cdp_launch_hint(self.port),
            )

    def browser_version(self) -> dict[str, Any]:
        version = self._http("/json/version")
        return version if isinstance(version, dict) else {}

    # --- tabs ----------------------------------------------------------------

    def list_tabs(self) -> list[TabInfo]:
        targets = self._http("/json/list")
        if not isinstance(targets, list):
            raise BridgeOffError(f"Unexpected /json/list payload from {self.base_url}.")

        tabs: list[TabInfo] = []
        for target in targets:
            if target.get("type") not in _USEFUL_TARGET_TYPES:
                continue
            url = target.get("url", "")
            if url.startswith(("devtools://", "chrome-extension://")):
                continue
            tabs.append(TabInfo(
                id=target.get("id", ""),
                title=target.get("title", ""),
                url=url,
                active=False,
            ))

        # CDP has no "active tab" concept over HTTP, so ask the pages themselves.
        # document.hasFocus() is the closest honest answer, and it is what matters
        # for keyboard input anyway.
        for tab in tabs:
            try:
                focused = self.eval_js(
                    tab.id,
                    "document.hasFocus() && document.visibilityState === 'visible'",
                    timeout_s=min(self.timeout_s, 3.0),
                )
                tab.active = bool(focused)
            except Exception:
                tab.active = False
        return tabs

    def open_tab(self, url: str, activate: bool = True) -> TabInfo:
        path = f"/json/new?{urllib.parse.quote(url, safe=':/?&=#%._-~')}"
        try:
            # Chrome 111+ requires PUT; older builds only accept GET.
            target = self._http(path, method="PUT")
        except BridgeOffError:
            target = self._http(path, method="GET")
        if not isinstance(target, dict) or not target.get("id"):
            raise BridgeOffError(f"Chrome did not return a new target for {url!r}.")
        tab = TabInfo(id=target["id"], title=target.get("title", ""), url=target.get("url", url), active=activate)
        if activate:
            try:
                self.activate_tab(tab.id)
            except BridgeOffError:
                pass
        return tab

    def close_tab(self, tab_id: str) -> None:
        """Close a tab and wait until Chrome has actually torn the target down.

        ``/json/close`` only *requests* the close; the target lingers in
        ``/json/list`` for a short while afterwards. Returning immediately means
        a caller that closes a tab and lists tabs sees a ghost, and a caller
        resolving by ``url_pattern`` can pick the dying tab and act on it.
        """
        self._drop_socket(tab_id)
        self._http(f"/json/close/{tab_id}")

        deadline = time.monotonic() + CLOSE_CONFIRM_TIMEOUT_S
        while time.monotonic() < deadline:
            targets = self._http("/json/list")
            if not isinstance(targets, list) or all(t.get("id") != tab_id for t in targets):
                return
            time.sleep(CLOSE_POLL_S)
        # The tab may still be running a beforeunload handler. Say so rather than
        # reporting a close that did not happen.
        raise JSError(
            f"Chrome accepted the close for {tab_id!r} but the tab is still open after "
            f"{CLOSE_CONFIRM_TIMEOUT_S:.0f}s.",
            remedy="The page is probably showing an 'are you sure you want to leave' dialog.",
        )

    def activate_tab(self, tab_id: str) -> None:
        self._http(f"/json/activate/{tab_id}")

    def navigate(self, tab_id: str, url: str) -> None:
        self._command(tab_id, "Page.navigate", {"url": url})

    # --- CDP command plumbing ------------------------------------------------

    def _socket_for(self, tab_id: str) -> WebSocket:
        socket_ = self._sockets.get(tab_id)
        if socket_ is not None:
            return socket_

        targets = self._http("/json/list")
        ws_url = None
        if isinstance(targets, list):
            for target in targets:
                if target.get("id") == tab_id:
                    ws_url = target.get("webSocketDebuggerUrl")
                    break
        if ws_url is None:
            raise NoTabError(
                f"No tab with id {tab_id!r} is open. Tab ids change on reload — "
                "prefer `url_pattern` so a handle survives navigation.",
            )
        if not ws_url:
            raise BridgeOffError(
                f"Tab {tab_id!r} exists but exposes no debugger URL. DevTools (F12) is almost "
                "certainly attached to it — close the DevTools panel on that tab and retry."
            )

        socket_ = WebSocket(ws_url, timeout_s=self.timeout_s)
        self._sockets[tab_id] = socket_
        return socket_

    def _drop_socket(self, tab_id: str) -> None:
        socket_ = self._sockets.pop(tab_id, None)
        if socket_ is not None:
            socket_.close()

    def _command(
        self,
        tab_id: str,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        timeout = self.timeout_s if timeout_s is None else timeout_s
        socket_ = self._socket_for(tab_id)
        socket_.settimeout(timeout)

        self._next_id += 1
        message_id = self._next_id
        payload = {"id": message_id, "method": method, "params": params or {}}

        try:
            socket_.send_text(json.dumps(payload))
            # CDP interleaves events with responses; skip anything that is not ours.
            while True:
                message = json.loads(socket_.recv_text())
                if message.get("id") != message_id:
                    continue
                if "error" in message:
                    error = message["error"]
                    raise JSError(f"{method} failed: {error.get('message', error)}")
                return message.get("result", {})
        except (BridgeOffError, TimeoutError_):
            # A dead or wedged socket must not be reused for the next call.
            self._drop_socket(tab_id)
            raise

    # --- evaluation ----------------------------------------------------------

    def eval_js(self, tab_id: str, expression: str, timeout_s: float) -> Any:
        result = self._command(
            tab_id,
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
                "timeout": int(timeout_s * 1000),
            },
            timeout_s=timeout_s + 2.0,
        )

        exception = result.get("exceptionDetails")
        if exception:
            raise JSError(_format_exception(exception))

        remote = result.get("result", {})
        if remote.get("type") == "undefined":
            return None
        if "value" in remote:
            return remote["value"]
        # Non-serialisable (a DOM node, a function, a cyclic object).
        return remote.get("description") or remote.get("className") or None

    # --- pixels --------------------------------------------------------------

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
        import base64

        self._command(tab_id, "Page.enable", timeout_s=timeout_s)
        params: dict[str, Any] = {"format": image_format, "fromSurface": True}
        if image_format == "jpeg":
            params["quality"] = quality

        if clip is not None:
            params["clip"] = {**clip, "scale": clip.get("scale", 1)}
            params["captureBeyondViewport"] = True
        elif full_page:
            metrics = self._command(tab_id, "Page.getLayoutMetrics", timeout_s=timeout_s)
            content = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
            width = content.get("width")
            height = content.get("height")
            if width and height:
                params["clip"] = {"x": 0, "y": 0, "width": width, "height": height, "scale": 1}
                params["captureBeyondViewport"] = True

        result = self._command(tab_id, "Page.captureScreenshot", params, timeout_s=max(timeout_s, 30.0))
        data = result.get("data")
        if not data:
            raise JSError("Chrome returned an empty screenshot.")
        return base64.b64decode(data)

    # --- trusted input -------------------------------------------------------

    def click_at(self, tab_id: str, x: float, y: float, timeout_s: float = 20.0) -> None:
        common = {"x": x, "y": y, "button": "left", "clickCount": 1, "buttons": 1}
        self._command(tab_id, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y}, timeout_s)
        self._command(tab_id, "Input.dispatchMouseEvent", {"type": "mousePressed", **common}, timeout_s)
        self._command(tab_id, "Input.dispatchMouseEvent", {"type": "mouseReleased", **common}, timeout_s)

    def insert_text(self, tab_id: str, text: str, timeout_s: float = 20.0) -> None:
        self._command(tab_id, "Input.insertText", {"text": text}, timeout_s)

    def press_key(self, tab_id: str, key: str, timeout_s: float = 20.0) -> None:
        spec = KEY_MAP.get(key)
        if spec is None:
            known = ", ".join(sorted(KEY_MAP))
            raise JSError(f"Unknown key {key!r}. Supported keys: {known}.")
        self._command(tab_id, "Input.dispatchKeyEvent", {"type": "keyDown", **spec}, timeout_s)
        self._command(tab_id, "Input.dispatchKeyEvent", {"type": "keyUp", **spec}, timeout_s)

    # --- teardown ------------------------------------------------------------

    def close(self) -> None:
        for socket_ in list(self._sockets.values()):
            socket_.close()
        self._sockets.clear()


def _format_exception(details: dict[str, Any]) -> str:
    exception = details.get("exception") or {}
    message = exception.get("description") or exception.get("value") or details.get("text") or "unknown error"
    line = details.get("lineNumber")
    if isinstance(message, str):
        message = message.strip().splitlines()[0] if message.strip() else "unknown error"
    if line is not None:
        return f"page threw at line {line}: {message}"
    return f"page threw: {message}"
