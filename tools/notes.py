"""Apple Notes tools for the MCP server.

Exposes seven tools that let the client read, search, and create
notes in Apple Notes via AppleScript:

    notes_list_folders  — List all folders
    notes_get           — Fetch notes from a folder, with pagination
    notes_search        — Search notes by text across all folders
    notes_create        — Create a new note
    notes_delete        — Delete a note
    notes_update        — Update the title or body of a note
    notes_append        — Append text to an existing note. 

AppleScript is invoked via osascript, passing
scripts/notes.applescript as the script file and an action keyword as the first argument.

Note:
    Note IDs used by these tools are the internal ID values
    assigned by Apple Notes. Always use the id field returned
    by notes_get or notes_search when calling notes_delete.

    Pass "default" as the folder name to target the user's default Notes folder without needing to know its name.
"""

import asyncio
import json
from pathlib import Path

from mcp.types import TextContent, Tool

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_NOTES_SCRIPT = _SCRIPTS_DIR / "notes.applescript"

# ------------------------------------------------------------
# Section B — Tool definitions
# ------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="notes_list_folders",
        description=(
            "List all folders in Apple Notes with the note count for each. "
            "Use this to discover available folders before fetching or "
            "creating notes."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="notes_get",
        description=(
            "Fetch notes from an Apple Notes folder, with pagination. "
            "Returns a JSON object with: total, offset, returned, has_more "
            "and a notes array where each element has id, title, folder, "
            "and modified_date. "
            "Pass folder as 'default' to target the user's default folder."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Name of the folder to fetch from. Defaults to the default folder.",
                    "default": "default",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of notes to return in this batch.",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based index of the first note to return.",
                    "default": 0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="notes_search",
        description=(
            "Search for notes by text across all Apple Notes folders. "
            "Matches against title and body content. "
            "Returns a JSON array where each element has id, title, "
            "folder, and modified_date."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in note titles and body content."
                },
            },
            "required": ["query"]
        },
    ),
    Tool(
        name="notes_create",
        description=(
            "Create a new note in Apple Notes. "
            "Returns the id of the created note. "
            "Pass folder as 'default' to add to the user's default folder. "
            "Body content is plain text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title of the note.",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text body content of the note.",
                    "default": "",
                },
                "folder": {
                    "type": "string",
                    "description": "Name of the folder to create the note in. Defaults to the default folder.",
                    "default": "default",
                },
            },
            "required": ["title"]
        },
    ),
    Tool(
        name="notes_delete",
        description=(
            "Permanently delete a note from Apple Notes. "
            "This cannot be undone. "
            "Use the id returned by notes_get or notes_search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Internal id of the note to delete."
                },
            },
            "required": ["note_id"],
        },
    ),
    Tool(
        name="notes_update",
        description=(
            "Update the title and/or body of an existing Apple Notes note. "
            "Only fields that are provided will be changed. "
            "Body content replaces the entire existing body. "
            "Use the id returned by notes_get or notes_search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Internal id of the note to update."
                },
                "title": {
                    "type": "string",
                    "description": "New title for the note.",
                },
                "body": {
                    "type": "string",
                    "description": "New body content for the note. Replaces the entire existing body.",
                },
            },
            "required": ["note_id",]
        },
    ),
    Tool(
        name="notes_append",
        description=(
            "Append plain text to the end of an existing Apple Notes note. "
            "Preserves all existing content. "
            "Use the id returned by notes_get or notes_search."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "note_id": {
                    "type": "string",
                    "description": "Internal id of the note to append to."
                },
                "text": {
                    "type": "string",
                    "description": "Plain text to append to the note."
                },
            },
            "required": ["note_id", "text"],
        },
    ),
]


# ------------------------------------------------------------
# Section C — Private helpers
# ------------------------------------------------------------

async def _run_script(action: str, *args: str) -> str:
    """Run notes.applescript with the given action and arguments.

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
        "osascript", str(_NOTES_SCRIPT), action, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"osascript timeout (action={action!r}): script took too long")

    if proc.returncode != 0:
        raise RuntimeError(
            f"osascript error (action={action!r}): "
            f"{stderr.decode().strip()}"
        )

    # Normalise line endings so splitlines() does not treat a stray CR as a
    # record boundary and shift fields (mirrors the mail/reminders tools).
    return stdout.decode().replace('\r\n', '\n').replace('\r', '\n').strip()


def _parse_note(line: str) -> dict | None:
    """Parse one pipe-delimited note record into a dict.

    Expected format:
        id|title|folder|modified_date

    Args:
        line: A single line of AppleScript output.

    Returns:
        A dict with keys id, title, folder, and modified_date.
        Returns None if the line does not contain exactly 4 fields.
    """
    parts = line.split("|", maxsplit=3)
    if len(parts) != 4:
        return None
    note_id, title, folder, modified_date = parts
    return {
        "id": note_id,
        "title": title,
        "folder": folder,
        "modified_date": modified_date if modified_date else None,
    }


# ------------------------------------------------------------
# Section D — Public Interface
# ------------------------------------------------------------

async def list_tools() -> list[Tool]:
    """Return the Tool definitions for the notes domain.

    Returns:
        The module-level TOOLS list containing all notes Tool definitions.
    """
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a notes tool call to the correct implementation.

    Args:
        name:      The tool name, e.g. "notes_create".
        arguments: Dict of validated arguments from the client.

    Returns:
        A single-element list containing a TextContent with the
        result as text or JSON.

    Raises:
        ValueError:   If the tool name is not recognised.
        RuntimeError: If the underlying AppleScript call fails.
    """
    if name == "notes_list_folders":
        raw = await _run_script("list_folders")
        folders = []
        for line in raw.splitlines():
            parts = line.split("|", maxsplit=1)
            if len(parts) == 2:
                folders.append({"name": parts[0], "count": int(parts[1])})
        return [TextContent(type="text", text=json.dumps(folders, indent=2))]

    if name == "notes_get":
        folder = arguments.get("folder", "default")
        count = int(arguments.get("count", 50))
        offset = int(arguments.get("offset", 0))
        raw = await _run_script("get_notes", folder, str(count), str(offset))
        lines = raw.splitlines() if raw else []
        total = int(lines[0]) if lines else 0
        notes = [n for n in (_parse_note(line) for line in lines[1:]) if n]
        return [TextContent(type="text", text=json.dumps({
            "total": total,
            "offset": offset,
            "returned": len(notes),
            "has_more": offset + len(notes) < total,
            "notes": notes,
        }, indent=2))]

    if name == "notes_search":
        query = arguments["query"]
        raw = await _run_script("search", query)
        notes = [n for n in (_parse_note(line)
                             for line in raw.splitlines()) if n]
        return [TextContent(type="text", text=json.dumps(notes, indent=2))]

    if name == "notes_create":
        title = arguments["title"]
        body = arguments.get("body", "")
        folder = arguments.get("folder", "default")
        note_id = await _run_script("create", title, body, folder)
        return [TextContent(type="text", text=note_id)]

    if name == "notes_delete":
        note_id = arguments["note_id"]
        await _run_script("delete", note_id)
        return [TextContent(type="text", text=f"Deleted {note_id!r}.")]

    if name == "notes_update":
        note_id = arguments["note_id"]
        title = arguments.get("title", "")
        body = arguments.get("body", "")
        await _run_script("update", note_id, title, body)
        return [TextContent(type="text", text=f"Updated {note_id!r}.")]

    if name == "notes_append":
        note_id = arguments["note_id"]
        text = arguments["text"]
        await _run_script("append", note_id, text)
        return [TextContent(type="text", text=f"Appended to {note_id!r}.")]

    raise ValueError(f"Unknown notes tool: '{name}'.")
