"""Apple Reminders tools for the MCP server.

Exposes seven tools that lets the client read, create, update, and
delete reminders in Apple Reminders via AppleScript:

    reminder_list_lists  — List all reminder lists
    reminder_get         — Fetch reminders from a list, with pagination
    reminder_search      — Searcg reminders by text across all lists
    reminder_create      — Create a new reminder
    reminder_complete    — Mark a reminder as complete
    reminder_update      — Update the title, due date, notes, or list
    reminder_delete      — Delete a reminder

AppleScript is invoked via osascript, passing
scripts/reminders.applescript as the script file and an action
keyword as the first argument

Note:
    Reminder IDs used by these tools are the internal id values
    assigned by Apple Reminders. Always use the id field returned
    by reminder_get or reminder_search when calling the mutation
    tools (reminder_complete, reminder_update, reminder_delete).

    Due dates are expressed in ISO 8601 format:
    "YYYY-MM-DD" or "YYYY-MM-DDTHH:MM:SS".
    Pass an empty string to clear a due date in reminder_update.

    Pass "default" as the list name to target the user's default
    Reminders list without needing to know its name.
"""

import asyncio
import json
from pathlib import Path

from mcp.types import TextContent, Tool

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_REMINDERS_SCRIPT = _SCRIPTS_DIR / "reminders.applescript"

# ------------------------------------------------------------
# Section B — Tool definitions
# ------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="reminder_list_lists",
        description=(
            "List all reminder lists in Apple Reminders with the "
            "reminder count for each. Use this to discover available "
            "lists before creating or fetching reminders."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="reminder_get",
        description=(
            "Fetch reminders from an Apple Reminders list, with pagination. "
            "Returns a JSON object with: total, offset, returned, has_more, "
            "and a reminders array where each element has id, title, "
            "due_date, notes, and is_completed. "
            "Pass list as 'default' to target the user's default list. "
            "Set include_completed to true to include completed reminders."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "list": {
                    "type": "string",
                    "description": "Name of the reminder list to fetch from. Defaults to the default list.",
                    "default": "default",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of reminders to return in this batch.",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based index of the first reminder to return.",
                    "default": 0,
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "If true, include completed reminders. Defaults to false.",
                    "default": False,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="reminder_search",
        description=(
            "Search for reminders by text across all Apple Reminders lists. "
            "Matches against title and notes. "
            "Returns a JSON array where each element has id, title, "
            "due_date, notes, is_completed, and list."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in reminder titles and notes.",
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "If true, include completed reminders. Defaults to false.",
                    "default": False,
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="reminder_create",
        description=(
            "Create a new reminder in Apple Reminders. "
            "Returns the id of the created reminder. "
            "Pass list as 'default' to add to the user's default list. "
            "Due date must be ISO 8601 format: YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the reminder."
                },
                "list": {
                    "type": "string",
                    "description": "Name of the list to add the reminder to. Defaults to the default list.",
                    "default": "default",
                },
                "due_date": {
                    "type": "string",
                    "description": "Due date in ISO 8601 format. Omit for no due date.",
                },
                "notes": {
                    "type": "string",
                    "description": "Additional notes for the reminder.",
                },
            },
            "required": ["title"],
        },
    ),
    Tool(
        name="reminder_complete",
        description=(
            "Mark an Apple Reminders reminder as complete. "
            "Use the id returned by reminder_get or reminder_search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "Internal id of the reminder to complete."
                },
            },
            "required": ["reminder_id"],
        },
    ),
    Tool(
        name="reminder_update",
        description=(
            "Update the title, due date, notes, or list of an existing reminder. "
            "Only fields that are provided will be changed. "
            "Pass an empty string for due_date to clear it. "
            "Use the id returned by reminder_get or reminder_search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "Internal id of the reminder to update."
                },
                "title": {
                    "type": "string",
                    "description": "New title for the reminder."
                },
                "due_date": {
                    "type": "string",
                    "description": "New due date in ISO 8601 format, or empty string to clear."
                },
                "notes": {
                    "type": "string",
                    "description": "New notes for the reminder.",
                },
                "list": {
                    "type": "string",
                    "description": "Name of the list to move the reminder to."
                },
            },
            "required": ["reminder_id"],
        },
    ),
    Tool(
        name="reminder_delete",
        description=(
            "Permanently delete a reminder from Apple Reminders. "
            "This cannot be undone. "
            "Use the id returned by reminder_get or reminder_search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reminder_id": {
                    "type": "string",
                    "description": "Internal id of the reminder to delete."
                },
            },
            "required": ["reminder_id"],
        },
    ),
]

# ------------------------------------------------------------
# Section C — Private helpers
# ------------------------------------------------------------


async def _run_script(action: str, *args: str) -> str:
    """Run reminders.applescript with the given action and arguments.

    Args:
        action: The action keyword the AppleScript handler dispatches on.
        *args:  Zero or more additional string arguments.

    Returns:
        The trimmed stdout produced by the script.

    Raises:
        RuntimeError: If osascript exits with a non-zero return code,
                      with the stderr output included in the message.
    """
    proc = await asyncio.create_subprocess_exec(
        "osascript", str(_REMINDERS_SCRIPT), action, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"osascript timeout (action={action!r}): script took too long")

    if proc.returncode != 0:
        raise RuntimeError(
            f"osascript error (action={action!r}): "
            f"{stderr.decode().strip()}"
        )

    return stdout.decode().replace('\r\n', '\n').replace('\r', '\n').strip()


def _parse_reminder(line: str) -> dict | None:
    """Parse one pipe-delimited reminder record into a dict.

    Expected format:
        id|title|due_date|notes|is_completed|list

    Args:
        line: A single line of AppleScript output.

    Returns:
        A dict with keys id, title, due_date, notes, is_completed, and list. due_date is None if the field is empty.
        Returns None if the line does not contain exactly 6 fields.
    """
    parts = line.split("|", maxsplit=5)
    if len(parts) != 6:
        return None
    reminder_id, title, due_date, notes, is_completed, list_name = parts
    return {
        "id": reminder_id,
        "title": title,
        "due_date": due_date if due_date else None,
        "notes": notes if notes else None,
        "is_completed": is_completed == "true",
        "list": list_name,
    }

# ------------------------------------------------------------
# Section D — Public interface
# ------------------------------------------------------------


async def list_tools() -> list[Tool]:
    """Return the Tool definitions for the reminders domain.

    Returns:
        The module-level TOOLS list containing all reminder Tool
        definitions.
    """
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a reminder tool call to the correct implementation.

    Args:
        name:      The tool name, e.g. "reminder_create".
        arguments: Dict of validated arguments from the client.

    Returns:
        A single-element list containing a TextContent with the
        result as text or JSON.

    Raises:
        ValueError:   If the tool name is not recognised.
        RuntimeError: If the underlying AppleScript call fails.
    """
    if name == "reminder_list_lists":
        raw = await _run_script("list_lists")
        lists = []
        for line in raw.splitlines():
            parts = line.split("|", maxsplit=1)
            if len(parts) == 2:
                lists.append({"name": parts[0], "count": int(parts[1])})
        return [TextContent(type="text", text=json.dumps(lists, indent=2))]

    if name == "reminder_get":
        list_name = arguments.get("list", "default")
        count = int(arguments.get("count", 50))
        offset = int(arguments.get("offset", 0))
        include_completed = bool(arguments.get("include_completed", False))
        raw = await _run_script(
            "get_reminders",
            list_name,
            str(count),
            str(offset),
            str(include_completed).lower(),
        )
        lines = raw.splitlines() if raw else []
        total = int(lines[0]) if lines else 0
        reminders = [r for r in (_parse_reminder(line)
                                 for line in lines[1:]) if r]
        return [TextContent(type="text", text=json.dumps({
            "total": total,
            "offset": offset,
            "returned": len(reminders),
            "has_more": offset + len(reminders) < total,
            "reminders": reminders
        }, indent=2))]

    if name == "reminder_search":
        query = arguments["query"]
        include_completed = bool(arguments.get("include_completed", False))
        raw = await _run_script("search", query, str(include_completed).lower())
        reminders = [r for r in (_parse_reminder(line)
                                 for line in raw.splitlines()) if r]
        return [TextContent(type="text", text=json.dumps(reminders, indent=2))]

    if name == "reminder_create":
        title = arguments["title"]
        list_name = arguments.get("list", "default")
        due_date = arguments.get("due_date", "")
        notes = arguments.get("notes", "")
        reminder_id = await _run_script("create", title, list_name, due_date, notes)
        return [TextContent(type="text", text=reminder_id)]

    if name == "reminder_complete":
        reminder_id = arguments["reminder_id"]
        await _run_script("complete", reminder_id)
        return [TextContent(type="text", text=f"Completed {reminder_id!r}.")]

    if name == "reminder_update":
        reminder_id = arguments["reminder_id"]
        title = arguments.get("title", "")
        due_date = arguments.get("due_date", "")
        notes = arguments.get("notes", "")
        list_name = arguments.get("list", "")
        await _run_script("update", reminder_id, title, due_date, notes, list_name)
        return [TextContent(type="text", text=f"Updated {reminder_id!r}.")]

    if name == "reminder_delete":
        reminder_id = arguments["reminder_id"]
        await _run_script("delete", reminder_id)
        return [TextContent(type="text", text=f"Deleted {reminder_id!r}.")]

    raise ValueError(f"Unknown reminder tool: '{name}'.")
