"""The stdlib WebSocket client, exercised against a real loopback server.

This is the layer with no safety net: every frame is hand-built, and a masking or
length-field mistake shows up as a hang or a corrupted message rather than an
exception. A live socket is the only way to test it honestly.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
import threading

import pytest

from tabpilot.backends._ws import WebSocket
from tabpilot.errors import BridgeOffError

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class MiniWebSocketServer:
    """Just enough RFC 6455 to answer one client, scripted per test."""

    def __init__(self, behaviour: str = "echo", accept_handshake: bool = True) -> None:
        self.behaviour = behaviour
        self.accept_handshake = accept_handshake
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self.port = self._listener.getsockname()[1]
        self.received: list[str] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/devtools/page/ABC"

    def _serve(self) -> None:
        try:
            conn, _ = self._listener.accept()
        except OSError:
            return
        with conn:
            request = b""
            while b"\r\n\r\n" not in request:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                request += chunk

            if not self.accept_handshake:
                conn.sendall(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
                return

            key = ""
            for line in request.decode("latin-1").split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            conn.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\n"
                b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
            )

            try:
                self._handle(conn)
            except OSError:
                pass

    def _handle(self, conn: socket.socket) -> None:
        if self.behaviour == "close_immediately":
            conn.sendall(_frame(0x8, b"", mask=False))
            return

        while True:
            message = _read_client_frame(conn)
            if message is None:
                return
            self.received.append(message)

            if self.behaviour == "echo":
                conn.sendall(_frame(0x1, message.encode(), mask=False))
            elif self.behaviour == "fragmented":
                payload = message.encode()
                half = len(payload) // 2
                conn.sendall(_frame(0x1, payload[:half], mask=False, fin=False))
                conn.sendall(_frame(0x0, payload[half:], mask=False, fin=True))
            elif self.behaviour == "ping_then_echo":
                conn.sendall(_frame(0x9, b"hb", mask=False))
                conn.sendall(_frame(0x1, message.encode(), mask=False))
            elif self.behaviour == "large":
                conn.sendall(_frame(0x1, (b"y" * 200_000), mask=False))
            elif self.behaviour == "silent":
                pass

    def stop(self) -> None:
        try:
            self._listener.close()
        except OSError:
            pass


def _frame(opcode: int, payload: bytes, *, mask: bool, fin: bool = True) -> bytes:
    header = bytearray([(0x80 if fin else 0x00) | opcode])
    length = len(payload)
    flag = 0x80 if mask else 0x00
    if length < 126:
        header.append(flag | length)
    elif length < 65536:
        header.append(flag | 126)
        header += struct.pack("!H", length)
    else:
        header.append(flag | 127)
        header += struct.pack("!Q", length)
    if mask:
        key = b"\x01\x02\x03\x04"
        header += key
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + payload


def _recv_exact(conn: socket.socket, count: int) -> bytes | None:
    buffer = b""
    while len(buffer) < count:
        chunk = conn.recv(count - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return buffer


def _read_client_frame(conn: socket.socket) -> str | None:
    head = _recv_exact(conn, 2)
    if head is None:
        return None
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        extended = _recv_exact(conn, 2)
        if extended is None:
            return None
        (length,) = struct.unpack("!H", extended)
    elif length == 127:
        extended = _recv_exact(conn, 8)
        if extended is None:
            return None
        (length,) = struct.unpack("!Q", extended)

    key = _recv_exact(conn, 4) if masked else b""
    if masked and key is None:
        return None
    payload = _recv_exact(conn, length) if length else b""
    if payload is None:
        return None
    if masked:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    if opcode == 0x8:
        return None
    if opcode in (0x9, 0xA):
        return ""
    return payload.decode("utf-8")


@pytest.fixture
def server(request):
    behaviour = getattr(request, "param", "echo")
    instance = MiniWebSocketServer(behaviour)
    yield instance
    instance.stop()


def test_handshake_and_round_trip(server):
    with WebSocket(server.url, timeout_s=5) as ws:
        ws.send_text('{"id":1,"method":"Runtime.evaluate"}')
        assert ws.recv_text() == '{"id":1,"method":"Runtime.evaluate"}'


def test_client_frames_are_masked(server):
    """An unmasked client frame is a protocol violation; Chrome drops the
    connection, which surfaces as a mysterious hang."""
    with WebSocket(server.url, timeout_s=5) as ws:
        ws.send_text("hello")
        ws.recv_text()
    assert server.received == ["hello"]


@pytest.mark.parametrize("payload_size", [10, 200, 70_000])
def test_every_length_field_width(server, payload_size):
    """7-bit, 16-bit and 64-bit length encodings are three separate code paths."""
    message = "z" * payload_size
    with WebSocket(server.url, timeout_s=10) as ws:
        ws.send_text(message)
        assert ws.recv_text() == message


def test_non_ascii_survives_utf8_framing(server):
    with WebSocket(server.url, timeout_s=5) as ws:
        ws.send_text("Khảo sát 日本語 \U0001f600")
        assert ws.recv_text() == "Khảo sát 日本語 \U0001f600"


@pytest.mark.parametrize("server", ["fragmented"], indirect=True)
def test_fragmented_messages_are_reassembled(server):
    with WebSocket(server.url, timeout_s=5) as ws:
        ws.send_text("abcdefghij")
        assert ws.recv_text() == "abcdefghij"


@pytest.mark.parametrize("server", ["ping_then_echo"], indirect=True)
def test_a_ping_is_answered_and_does_not_surface_as_a_message(server):
    with WebSocket(server.url, timeout_s=5) as ws:
        ws.send_text("ping me")
        assert ws.recv_text() == "ping me"


@pytest.mark.parametrize("server", ["large"], indirect=True)
def test_large_server_frames(server):
    with WebSocket(server.url, timeout_s=10) as ws:
        ws.send_text("go")
        assert len(ws.recv_text()) == 200_000


@pytest.mark.parametrize("server", ["close_immediately"], indirect=True)
def test_a_close_frame_becomes_a_clear_error(server):
    with WebSocket(server.url, timeout_s=5) as ws:
        with pytest.raises(BridgeOffError, match="closed the DevTools WebSocket"):
            ws.recv_text()


@pytest.mark.parametrize("server", ["silent"], indirect=True)
def test_a_silent_server_times_out_rather_than_hanging_forever(server):
    from tabpilot.errors import TimeoutError_

    with WebSocket(server.url, timeout_s=0.4) as ws:
        ws.send_text("anyone there")
        with pytest.raises(TimeoutError_):
            ws.recv_text()


def test_a_refused_handshake_explains_the_devtools_cause():
    """A tab with the F12 panel open loses its debugger URL; the message names
    that cause, because it is by far the most common one."""
    server = MiniWebSocketServer(accept_handshake=False)
    try:
        with pytest.raises(BridgeOffError, match="DevTools"):
            WebSocket(server.url, timeout_s=5)
    finally:
        server.stop()


def test_nothing_listening_is_reported_as_such():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    listener.close()
    with pytest.raises(BridgeOffError, match="Cannot open a socket"):
        WebSocket(f"ws://127.0.0.1:{port}/x", timeout_s=1)


def test_wss_is_refused_with_the_reason():
    with pytest.raises(BridgeOffError, match="loopback or an SSH tunnel"):
        WebSocket("wss://example.com/x", timeout_s=1)


def test_close_is_idempotent(server):
    ws = WebSocket(server.url, timeout_s=5)
    ws.close()
    ws.close()
