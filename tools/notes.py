"""Apple Notes tools for the MCP server.

Exposes seven tools that let the client read, search, create, and edit notes in
Apple Notes via AppleScript:

    notes_list_folders  — List all folders
    notes_get           — Fetch notes from a folder, with pagination
    notes_search        — Search notes by text across all folders
    notes_create        — Create a new note
    notes_delete        — Delete a note
    notes_update        — Update the title or body of a note
    notes_append        — Append text to an existing note

AppleScript is invoked via osascript, passing scripts/notes.applescript as the
script file and an action keyword as the first argument.

Note:
    Note IDs used by these tools are the internal id values assigned by Apple
    Notes. Always use the id field returned by notes_get or notes_search when
    calling the mutation tools (notes_delete, notes_update, notes_append).

    The modified_date field is returned in ISO 8601 format
    (YYYY-MM-DDTHH:MM:SS), matching the reminders tools.

    Pass "default" as the folder name to target the user's default Notes folder
    without needing to know its name.
"""

from pathlib import Path

from mcp.types import TextContent, Tool

from ._osascript import DEFAULT_TIMEOUT_SECONDS, run_osascript
from ._responses import json_content, paginate, text_content

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
            "Returns a JSON object {status, total, offset, returned, has_more, "
            "notes}, where notes is an array whose elements have id, title, "
            "folder, and modified_date. Results are paginated via count/offset "
            "(total is the full match count); page through with offset when "
            "has_more is true."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in note titles and body content."
                },
                "count": {
                    "type": "integer",
                    "description": "Maximum number of matches to return in this page.",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based index of the first match to return.",
                    "default": 0,
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
            "Body content is plain text. Note: Apple Notes uses the first line "
            "of the body as the title, so the title is stored as the note's "
            "first line followed by the body."
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
            "Delete a note from Apple Notes. This is a soft delete: the note "
            "is moved to the 'Recently Deleted' folder and can be recovered "
            "there until it is purged. "
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
            "Body content replaces the entire existing body; the title is kept "
            "as the note's first line (Apple Notes derives the title from it). "
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
            "required": ["note_id"]
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
# Section C — osascript wrapper
# ------------------------------------------------------------

async def _run_script(
    action: str,
    *arguments: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run notes.applescript through the shared osascript runner."""
    return await run_osascript(
        _NOTES_SCRIPT, action, *arguments, timeout=timeout
    )


# ------------------------------------------------------------
# Section D — Output parsers (pure)
# ------------------------------------------------------------

def _parse_folder_line(line: str) -> dict | None:
    """Parse one "name|count" folder line into a dict.

    Returns None if the line does not split into exactly two fields.
    """
    parts = line.split("|", maxsplit=1)
    if len(parts) != 2:
        return None
    name, count_text = parts
    return {"name": name, "count": int(count_text)}


def _parse_note(line: str) -> dict | None:
    """Parse one pipe-delimited note record into a dict.

    Expected format: id|title|folder|modified_date

    An empty modified_date becomes None. Returns None if the line does not
    contain exactly four fields.
    """
    parts = line.split("|", maxsplit=3)
    if len(parts) != 4:
        return None
    note_id, title, folder, modified_date = parts
    return {
        "id": note_id,
        "title": title,
        "folder": folder,
        "modified_date": modified_date or None,
    }


# ------------------------------------------------------------
# Section E — Per-tool handlers
# ------------------------------------------------------------

async def _handle_list_folders(arguments: dict) -> list[TextContent]:
    del arguments  # no arguments; the parameter exists for a uniform dispatch signature
    raw = await _run_script("list_folders")
    folders = [
        parsed for parsed in
        (_parse_folder_line(line) for line in raw.splitlines())
        if parsed is not None
    ]
    return json_content(folders)


async def _handle_get(arguments: dict) -> list[TextContent]:
    folder = arguments.get("folder", "default")
    count = int(arguments.get("count", 50))
    offset = int(arguments.get("offset", 0))

    raw = await _run_script("get_notes", folder, str(count), str(offset))

    # The script paginates and puts the total note count on the first line.
    lines = raw.splitlines() if raw else []
    total = int(lines[0]) if lines else 0
    notes = [
        parsed for parsed in
        (_parse_note(line) for line in lines[1:])
        if parsed is not None
    ]
    returned = len(notes)
    return json_content({
        "total": total,
        "offset": offset,
        "returned": returned,
        "has_more": offset + returned < total,
        "notes": notes,
    })


async def _handle_search(arguments: dict) -> list[TextContent]:
    query = arguments["query"]
    count = int(arguments.get("count", 50))
    offset = int(arguments.get("offset", 0))

    raw = await _run_script("search", query)
    matches = [
        parsed for parsed in
        (_parse_note(line) for line in raw.splitlines())
        if parsed is not None
    ]

    page, total, has_more = paginate(matches, offset, count)
    return json_content({
        "status": "ok",
        "total": total,
        "offset": offset,
        "returned": len(page),
        "has_more": has_more,
        "notes": page,
    })


async def _handle_create(arguments: dict) -> list[TextContent]:
    title = arguments["title"]
    body = arguments.get("body", "")
    folder = arguments.get("folder", "default")
    note_id = await _run_script("create", title, body, folder)
    return text_content(note_id)


async def _handle_delete(arguments: dict) -> list[TextContent]:
    note_id = arguments["note_id"]
    await _run_script("delete", note_id)
    return text_content(f"Deleted {note_id!r}.")


async def _handle_update(arguments: dict) -> list[TextContent]:
    note_id = arguments["note_id"]
    title = arguments.get("title", "")
    body = arguments.get("body", "")
    await _run_script("update", note_id, title, body)
    return text_content(f"Updated {note_id!r}.")


async def _handle_append(arguments: dict) -> list[TextContent]:
    note_id = arguments["note_id"]
    text = arguments["text"]
    await _run_script("append", note_id, text)
    return text_content(f"Appended to {note_id!r}.")


_TOOL_HANDLERS = {
    "notes_list_folders": _handle_list_folders,
    "notes_get": _handle_get,
    "notes_search": _handle_search,
    "notes_create": _handle_create,
    "notes_delete": _handle_delete,
    "notes_update": _handle_update,
    "notes_append": _handle_append,
}


# ------------------------------------------------------------
# Section F — Public interface
# ------------------------------------------------------------

async def list_tools() -> list[Tool]:
    """Return the Tool definitions for the notes domain."""
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a notes tool call to its handler.

    Args:
        name:      The tool name, e.g. "notes_create".
        arguments: Dict of validated arguments from the client.

    Returns:
        A single-element list containing a TextContent with the result.

    Raises:
        ValueError:   If the tool name is not recognised.
        RuntimeError: If the underlying AppleScript call fails.
    """
    try:
        tool_handler = _TOOL_HANDLERS[name]
    except KeyError:
        raise ValueError(f"Unknown notes tool: '{name}'.")
    return await tool_handler(arguments)
