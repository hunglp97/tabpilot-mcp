"""Minimal RFC 6455 client, stdlib only.

Chrome DevTools Protocol needs a WebSocket and nothing more: text frames, no
extensions, no compression, one connection at a time. A dependency-free client
keeps ``tabpilot`` installable on a bare Ubuntu server where ``pip install`` may
not be available at all.
"""

from __future__ import annotations

import base64
import os
import socket
import struct
from urllib.parse import urlparse

from ..errors import BridgeOffError, TimeoutError_

_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BINARY = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

MAX_FRAME_BYTES = 64 * 1024 * 1024


class WebSocket:
    """A synchronous text WebSocket. Not thread-safe; one owner per instance."""

    def __init__(self, url: str, timeout_s: float = 20.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("ws", "wss"):
            raise BridgeOffError(f"Unsupported WebSocket scheme: {parsed.scheme!r}")
        if parsed.scheme == "wss":
            raise BridgeOffError(
                "wss:// is not supported. CDP should be reached over loopback or an SSH "
                "tunnel, never over TLS to a remote host."
            )

        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or 80
        self._path = parsed.path or "/"
        if parsed.query:
            self._path += "?" + parsed.query
        self._timeout_s = timeout_s
        self._buffer = b""
        self._closed = False

        try:
            self._sock = socket.create_connection((self._host, self._port), timeout=timeout_s)
        except OSError as exc:
            raise BridgeOffError(f"Cannot open a socket to {self._host}:{self._port} — {exc}") from exc
        self._sock.settimeout(timeout_s)
        self._handshake()

    # --- setup ---------------------------------------------------------------

    def _handshake(self) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))

        header = b""
        while b"\r\n\r\n" not in header:
            chunk = self._recv_some()
            if not chunk:
                raise BridgeOffError("Chrome closed the connection during the WebSocket handshake.")
            header += chunk
            if len(header) > 65536:
                raise BridgeOffError("WebSocket handshake response was implausibly large.")

        head, _, rest = header.partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status_line:
            raise BridgeOffError(
                f"Chrome refused the WebSocket upgrade: {status_line!r}. "
                "This usually means DevTools is attached to that tab (close the F12 panel) "
                "or the target was discarded."
            )
        self._buffer = rest

    # --- socket plumbing -----------------------------------------------------

    def _recv_some(self) -> bytes:
        try:
            return self._sock.recv(65536)
        except socket.timeout as exc:
            raise TimeoutError_(f"Timed out waiting for Chrome after {self._timeout_s:.1f}s.") from exc
        except OSError as exc:
            raise BridgeOffError(f"Connection to Chrome failed — {exc}") from exc

    def _recv_exact(self, count: int) -> bytes:
        while len(self._buffer) < count:
            chunk = self._recv_some()
            if not chunk:
                raise BridgeOffError("Chrome closed the connection mid-frame.")
            self._buffer += chunk
        out, self._buffer = self._buffer[:count], self._buffer[count:]
        return out

    def settimeout(self, timeout_s: float) -> None:
        self._timeout_s = timeout_s
        self._sock.settimeout(timeout_s)

    # --- frames --------------------------------------------------------------

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise BridgeOffError("WebSocket is already closed.")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack("!H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack("!Q", length)

        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        try:
            self._sock.sendall(bytes(header) + masked)
        except OSError as exc:
            raise BridgeOffError(f"Could not write to Chrome — {exc}") from exc

    def send_text(self, text: str) -> None:
        self._send_frame(_OP_TEXT, text.encode("utf-8"))

    def _read_frame(self) -> tuple[int, bytes, bool]:
        first, second = self._recv_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F

        if length == 126:
            (length,) = struct.unpack("!H", self._recv_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._recv_exact(8))
        if length > MAX_FRAME_BYTES:
            raise BridgeOffError(f"Chrome sent a {length} byte frame, above the {MAX_FRAME_BYTES} byte cap.")

        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length)
        if masked:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return opcode, payload, fin

    def recv_text(self) -> str:
        """Read one complete text message, handling fragments and control frames."""
        chunks: list[bytes] = []
        expecting_continuation = False

        while True:
            opcode, payload, fin = self._read_frame()

            if opcode == _OP_PING:
                self._send_frame(_OP_PONG, payload)
                continue
            if opcode == _OP_PONG:
                continue
            if opcode == _OP_CLOSE:
                self._closed = True
                raise BridgeOffError("Chrome closed the DevTools WebSocket.")

            if opcode in (_OP_TEXT, _OP_BINARY):
                if expecting_continuation:
                    raise BridgeOffError("Chrome sent a new message before finishing the previous one.")
                chunks = [payload]
            elif opcode == _OP_CONT:
                if not expecting_continuation:
                    raise BridgeOffError("Chrome sent a continuation frame with nothing to continue.")
                chunks.append(payload)
            else:
                raise BridgeOffError(f"Unexpected WebSocket opcode from Chrome: {opcode}")

            if fin:
                return b"".join(chunks).decode("utf-8", errors="replace")
            expecting_continuation = True

    # --- teardown ------------------------------------------------------------

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._send_frame(_OP_CLOSE, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
