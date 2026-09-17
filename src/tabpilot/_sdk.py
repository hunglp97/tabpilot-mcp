"""Compatibility shim across MCP SDK versions.

The SDK renamed ``FastMCP`` to ``MCPServer`` in 2.0. The decorator and
constructor shapes TabPilot relies on are the same in both, so importing through
one place keeps the server code free of version branches — and keeps TabPilot
installable next to whichever SDK the host already pins.
"""

from __future__ import annotations

SDK_MAJOR: int

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer as Server

    SDK_MAJOR = 2
except ImportError:  # pragma: no cover - exercised only on mcp 1.x
    from mcp.server.fastmcp import FastMCP as Server  # type: ignore[assignment]

    SDK_MAJOR = 1
    try:
        from mcp.server.fastmcp import Image  # type: ignore[assignment]
    except ImportError:
        Image = None  # type: ignore[assignment]

__all__ = ["Server", "Image", "SDK_MAJOR"]
