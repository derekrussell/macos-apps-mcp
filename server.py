"""MCP server entry point for macos-apps-mcp.

This module starts a Model Context Protocol (MCP) server over stdio, registers
every available tool, and routes each incoming tool call to the correct handler
module.

Transport: stdio -- the standard MCP transport, compatible with any MCP client
    (Claude Desktop, Cursor, Zed, VS Code, etc.).
SDK: mcp (https://github.com/modelcontextprotocol/python-sdk).

The tools rely on AppleScript and are therefore macOS-specific; the server
architecture itself is platform-agnostic.

Tool namespacing convention -- a tool's name prefix selects its module:
    mail_*      -> tools/mail.py
    notes_*     -> tools/notes.py
    reminder_*  -> tools/reminders.py
"""

import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server

from tools import mail, notes, reminders

# Map each tool-name prefix to the module that implements those tools. The
# prefixes are mutually exclusive, so lookup order does not matter. Adding a new
# domain is a single entry here.
MODULE_BY_TOOL_PREFIX = {
    "mail_": mail,
    "notes_": notes,
    "reminder_": reminders,
}

app = Server("macos-apps")


# ------------------------------------------------------------
# Routing helpers (pure enough to unit-test without the MCP app)
# ------------------------------------------------------------

def select_tool_module(tool_name: str):
    """Return the handler module responsible for a tool name, chosen by prefix.

    Args:
        tool_name: The full tool name, e.g. "mail_search".

    Returns:
        The module (mail, notes, or reminders) that handles it.

    Raises:
        ValueError: If no registered prefix matches the name.
    """
    for prefix, module in MODULE_BY_TOOL_PREFIX.items():
        if tool_name.startswith(prefix):
            return module
    raise ValueError(f"Unknown tool: {tool_name!r}.")


async def collect_tool_definitions():
    """Gather the Tool definitions from every handler module into one list.

    Called on startup so the client can discover every action the server
    provides. Each module keeps its own definitions co-located with their
    implementation.
    """
    definitions = []
    for module in MODULE_BY_TOOL_PREFIX.values():
        definitions += await module.list_tools()
    return definitions


# ------------------------------------------------------------
# MCP hooks (thin wrappers around the helpers above)
# ------------------------------------------------------------

@app.list_tools()
async def list_tools():
    """MCP hook: report every tool this server provides."""
    return await collect_tool_definitions()


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    """MCP hook: route an incoming tool call to its handler module.

    Args:
        name:      The tool name registered in list_tools(), e.g. "mail_search".
        arguments: The validated arguments provided by the client.

    Returns:
        The list of MCP content objects returned by the handler.
    """
    module = select_tool_module(name)
    return await module.handle(name, arguments)


# ------------------------------------------------------------
# Startup
# ------------------------------------------------------------

async def main():
    """Start the stdio MCP server and serve until the connection closes.

    ``stdio_server()`` provides the async streams the client communicates over;
    ``app.run()`` handles the MCP handshake, tool discovery, and the
    call/response loop for the lifetime of the session.
    """
    async with stdio_server() as streams:
        # Warm the reminder search index in the background so the first
        # reminder_search is usually served from a ready index.
        reminders.warm_index()
        await app.run(*streams, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
