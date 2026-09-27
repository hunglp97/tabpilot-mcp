"""Tests for CAPTCHA MCP tool registration, schema validation, and tool execution."""

import asyncio
import json
import pytest

from conftest import FakeBackend, FakeSession
from tabpilot.config import Config
from tabpilot.server import create_server


def _call_text(server, name: str, arguments: dict) -> str:
    result = asyncio.run(server.call_tool(name, arguments))
    if hasattr(result, "content") and result.content:
        for item in result.content:
            if hasattr(item, "text"):
                return item.text
    if isinstance(result, (list, tuple)) and result:
        item = result[0]
        if hasattr(item, "text"):
            return item.text
        if hasattr(item, "content") and item.content:
            return item.content[0].text
    return getattr(result, "text", str(result))


def test_detect_captcha_tool_via_mcp():
    server = create_server(Config())
    candidate_data = {
        "candidate_id": "cand_1",
        "provider": "cloudflare",
        "challenge_kind": "interstitial",
        "state": "actionable",
        "confidence": "high",
        "visible": True,
        "blocking": True,
        "available_strategies": ["passive_wait"],
    }
    backend = FakeBackend(responses={"captcha_detect": {"ok": True, "candidates": [candidate_data]}})
    server._tabpilot_session = FakeSession(backend)

    text = _call_text(server, "detect_captcha", {})
    data = json.loads(text)
    assert data["status"] == "present"
    assert data["selected_candidate_id"] == "cand_1"


def test_solve_captcha_start_and_act_via_mcp():
    server = create_server(Config())
    candidate_data = {
        "candidate_id": "cand_img",
        "provider": "custom",
        "challenge_kind": "image_grid",
        "state": "actionable",
        "confidence": "high",
        "visible": True,
        "blocking": True,
        "rect_css": {"x": 0, "y": 0, "width": 300, "height": 300},
        "available_strategies": ["agent_vision"],
    }
    backend = FakeBackend(responses={
        "captcha_detect": {"ok": True, "candidates": [candidate_data]},
        "raw": None,
    })
    server._tabpilot_session = FakeSession(backend)

    text = _call_text(server, "solve_captcha", {"operation": "start"})
    data = json.loads(text)
    assert data["status"] == "needs_agent"
    solve_id = data["solve_id"]
    obs_id = data["observation_id"]

    # Call act via MCP tool
    act_text = _call_text(server, "solve_captcha", {
        "operation": "act",
        "solve_id": solve_id,
        "observation_id": obs_id,
        "action_id": "mcp-act-1",
        "action": {"kind": "select_tile", "target_id": "tile-1"},
    })
    act_data = json.loads(act_text)
    assert act_data["solve_id"] == solve_id
