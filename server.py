"""MCP server entry point for the Apple Assistant.

This module starts a Model Context Protocol (MCP) server over stdio,
registers all available tools, and routes incoming tool calls to the
correct handler module.

Transport: stdio — the standard MCP transport, compatible with any
    MCP client (Claude Desktop, Cursor, Zed, VS Code, etc.)
SDK: mcp (https://github.com/modelcontextprotocol/python-sdk)

Note:
    The tools registered here rely on AppleScript and are therefore
    macOS-specific. The server architecture itself is platform-agnostic.

Tool namespacing convention:
    mail_*      → tools/mail.py
    note_*      → tools/notes.py
    reminder_*  → tools/reminders.py
    file_*      → tools/filesystem.py
"""
import asyncio

from mcp.server import Server
from mcp.server.stdio import stdio_server

from tools import filesystem, mail, notes, reminders

# ------------------------------------------------------------
# Server instance
# ------------------------------------------------------------

app = Server("apple-assistant")

# ------------------------------------------------------------
# Tool registry
# ------------------------------------------------------------


@app.list_tools()
async def list_tools():
    """Return the combined list of tools from every handler module.

    The client calls this once on startup to discover what the server can do.
    Each handler module exposes its own list_tools() coroutine so that
    tool definitions stay co-located with their implementation.

    Returns:
        A flat list of mcp.types.Tool objects covering all four domains:
        mail, notes, reminders, and filesystem.
    """
    # Aggregate tool definitions from every module.
    tools = []
    tools += await mail.list_tools()
    tools += await notes.list_tools()
    tools += await reminders.list_tools()
    tools += await filesystem.list_tools()
    return tools

# ------------------------------------------------------------
# Tool dispatcher
# ------------------------------------------------------------


@app.call_tool()
async def dispatch_tool(name: str, arguments: dict):
    """Route an incoming tool call to the appropriate handler module.

    The client calls this whenever it wants to invoke a tool. The tool
    name prefix determines which module handles the call — this keeps
    the routing logic simple and means adding a new domain is just one
    line.

    Args:
        name: The tool name as registered in list_tools(), e.g.
            "mail_get_unread".
        arguments: A dict of validated arguments provided by Claude.

    Returns:
        A list of mcp.types.TextContent (or other content types) returned
        by the handler. The client renders these as part of its response.

    Raises:
        ValueError: If the tool name doesn't match any known prefix.
    """
    if name.startswith("file_"):
        return await filesystem.handle(name, arguments)
    if name.startswith("mail_"):
        return await mail.handle(name, arguments)
    if name.startswith("notes_"):
        return await notes.handle(name, arguments)
    if name.startswith("reminder_"):
        return await reminders.handle(name, arguments)
    raise ValueError(f"Unknown tool: '{name}'.")


async def main():
    """Start the MCP server and block until the connection closes.

    stdio_server() provides a pair of async streams that the MCP client
    communicates over. app.run() handles the MCP handshake, tool
    discovery, and the call/response loop for the lifetime of the session.
    """
    async with stdio_server() as streams:
        await app.run(*streams, app.create_initialization_options())

if __name__ == "__main__":
    asyncio.run(main())
