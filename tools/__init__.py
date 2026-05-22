"""Tool handler modules for the Apple Assistant MCP server.

Each module in this package is responsible for one domain:

  mail.py         — Read, move, and delete emails via Apple Mail
  notes.py        — Create and append notes via Apple Notes
  reminders.py    — Create reminders with due dates via Apple Reminders
  filesystem.py   — Save files to organised folders via the macOS file system

Each module exposes two coroutines that server.py calls:

  list_tools() -> list[Tool]
    Returns the Tool definitions for this domain. Called once on
    startup so the client knows what actions are available.

  handle(name: str, arguments: dict) -> list[TextContent]
    Executes the named tool with the given arguments and returns
    the result as a list of MCP content objects.

All Apple interaction is done via AppleScript, invoked as a subprocess
using asyncio.create_subprocess_exec(). Longer scripts live in scripts/
and are called by filename; short one-liners are inlined as strings.
"""