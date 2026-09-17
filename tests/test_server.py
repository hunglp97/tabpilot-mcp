"""Tool registration and error presentation."""

from __future__ import annotations

import asyncio

import pytest

from conftest import FakeBackend, FakeSession
from tabpilot.config import Config
from tabpilot.errors import BridgeOffError
from tabpilot.server import create_server

EXPECTED_TOOLS = {
    "browser_status", "list_tabs", "read_tab", "query_dom",
    "open_tab", "close_tab", "navigate", "activate_tab",
    "eval_js", "click", "fill", "select_option", "select_option_ui",
    "scan_matrix", "fill_matrix", "wait_for", "screenshot",
}


@pytest.fixture
def server():
    return create_server(Config())


def tool_names(server) -> set[str]:
    return {tool.name for tool in asyncio.run(server.list_tools())}


def test_every_tool_is_registered(server):
    assert tool_names(server) == EXPECTED_TOOLS


def test_no_tool_lost_its_description(server):
    for tool in asyncio.run(server.list_tools()):
        assert tool.description, f"{tool.name} has no description"


def test_resources_are_registered(server):
    uris = {str(resource.uri) for resource in asyncio.run(server.list_resources())}
    assert "tab://active" in uris
    templates = asyncio.run(server.list_resource_templates())
    assert any(
        "tab://" in getattr(t, "uriTemplate", getattr(t, "uri_template", ""))
        for t in templates
    )


def test_instructions_steer_away_from_the_expensive_path(server):
    """An agent's default move is eval_js with innerText, which costs thousands of
    tokens for an answer query_dom gives in ten lines."""
    instructions = server.instructions or ""
    assert "url_pattern" in instructions
    assert "query_dom" in instructions
    assert "innerText" in instructions


def _call(server, name: str, arguments: dict) -> str:
    result = asyncio.run(server.call_tool(name, arguments))
    parts = result[0] if isinstance(result, tuple) else result
    items = parts if isinstance(parts, (list, tuple)) else [parts]
    return "\n".join(getattr(item, "text", str(item)) for item in items)


class TestErrorPresentation:
    """A raised exception reaches the model as a stack trace with the remedy
    stripped. Returning the text keeps the 'how to fix' part, which is what lets
    an agent recover without the user stepping in."""

    def test_an_unreachable_browser_returns_the_remedy_as_text(self):
        server = create_server(Config(cdp_port=1, backend="cdp"))
        output = _call(server, "list_tabs", {})
        assert "ERROR [BRIDGE_OFF]" in output
        assert "How to fix" in output
        assert "--remote-debugging-port" in output

    def test_a_bad_argument_is_labelled_as_such(self):
        server = create_server(Config())
        server._tabpilot_session = FakeSession(FakeBackend())
        output = _call(server, "read_tab", {"mode": "markdown"})
        assert "ERROR" in output

    def test_a_dead_connection_is_dropped_so_the_next_call_reconnects(self):
        """Chrome restarts and laptops sleep. Holding a dead socket would make
        every later call fail until the client reconnected."""
        server = create_server(Config(cdp_port=1, backend="cdp"))
        session = server._tabpilot_session

        class Exploding(FakeBackend):
            def list_tabs(self):
                raise BridgeOffError("socket is gone")

        session._backend = Exploding()
        assert "ERROR [BRIDGE_OFF]" in _call(server, "list_tabs", {})
        assert session._backend is None
