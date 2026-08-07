"""Helpers for building MCP tool responses.

These are domain-agnostic: every tool module returns results in the same shape
(a single JSON or plain-text content item) and paginates the same way, so that
logic lives here once rather than being duplicated per module.
"""

import json

from mcp.types import TextContent


def json_content(payload) -> list[TextContent]:
    """Wrap a JSON-serialisable payload as a single MCP text content item."""
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def text_content(text: str) -> list[TextContent]:
    """Wrap a plain string as a single MCP text content item."""
    return [TextContent(type="text", text=text)]


def paginate(items: list, offset: int, count: int):
    """Slice ``items`` for offset/count pagination.

    Returns a (page, total, has_more) tuple. A negative count returns everything
    from ``offset`` onward.
    """
    total = len(items)
    page = items[offset:offset + count] if count >= 0 else items[offset:]
    has_more = offset + len(page) < total
    return page, total, has_more
