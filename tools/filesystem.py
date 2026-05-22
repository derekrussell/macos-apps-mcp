"""Apple filesystem tools for the MCP server — stub pending implementation.
"""

from mcp.types import TextContent, Tool

TOOLS: list[Tool] = []


async def list_tools() -> list[Tool]:
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    raise ValueError(f"Unknown filesystem tool '{name}'.")
