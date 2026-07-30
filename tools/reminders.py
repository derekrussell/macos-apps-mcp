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
import time
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
            "due_date, notes, is_completed, and list. "
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
            "By default matches against the title only; set search_notes to true "
            "to also match notes/body content. "
            "Title search is served from a short-lived (~45s) in-memory index for "
            "reliability on large accounts, so a newly created reminder appears "
            "immediately (mutations refresh the index) but an edit made in the "
            "Apple Reminders app may take up to ~45s to be reflected. "
            "Note: search_notes is significantly slower on large accounts "
            "(it must read every reminder's body live) and may time out; leave it "
            "off unless you specifically need to match note text. "
            "The returned notes field is always populated regardless. "
            "Returns a JSON array where each element has id, title, "
            "due_date, notes, is_completed, and list."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for in reminder titles (and notes if search_notes is true).",
                },
                "include_completed": {
                    "type": "boolean",
                    "description": "If true, include completed reminders. Defaults to false.",
                    "default": False,
                },
                "search_notes": {
                    "type": "boolean",
                    "description": "If true, also match against each reminder's notes/body. Slower; defaults to false (title-only).",
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
            "Only fields that are provided will be changed; omit a field to "
            "leave it untouched. "
            "Note: an existing due date cannot be cleared (Apple Reminders' "
            "AppleScript interface does not support it) — provide a new due_date "
            "to change it, or omit it to keep it. "
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
                    "description": "New due date in ISO 8601 format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS). Omit to leave the due date unchanged; it cannot be cleared."
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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
    except asyncio.TimeoutError:
        proc.kill()
        # Reap the killed child so it does not linger and hold its pipes open.
        # An osascript blocked inside a synchronous Apple event ignores SIGKILL
        # until that event returns, so bound the wait rather than hang here.
        try:
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass
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
# Search index cache
#
# reminder_search cannot scan every list on every call: on a large account a
# full AppleScript scan takes tens of seconds, and repeating it a handful of
# times in a session cumulatively wedges EventKit until searches time out and
# stop recovering. Instead we scan once into an in-memory index (id, title,
# list), reuse it for a short TTL, and serialise rebuilds behind a lock so
# concurrent searches never launch overlapping scans. Mutations invalidate the
# index so a create-then-search still sees the new reminder. Only the title
# lives in the index; due/notes/completed are hydrated per match (a few O(1)
# `reminder id` lookups), which keeps both the build and each search cheap.
# ------------------------------------------------------------

_INDEX_TTL = 45.0
_index_cache: list[dict] | None = None
_index_built_at: float = 0.0
_index_lock = asyncio.Lock()


def _invalidate_index() -> None:
    """Drop the cached search index so the next search rebuilds it."""
    global _index_cache
    _index_cache = None


async def _get_search_index() -> list[dict]:
    """Return the cached reminder index, rebuilding it if stale or missing.

    The rebuild is serialised behind a lock: a second search arriving while a
    build is in flight waits, then finds the freshly built cache instead of
    launching its own overlapping scan.
    """
    global _index_cache, _index_built_at
    async with _index_lock:
        now = time.monotonic()
        if _index_cache is not None and (now - _index_built_at) < _INDEX_TTL:
            return _index_cache
        raw = await _run_script("build_index")
        index: list[dict] = []
        for line in raw.splitlines():
            parts = line.split("|", maxsplit=2)
            if len(parts) == 3:
                index.append({"id": parts[0], "title": parts[1], "list": parts[2]})
        _index_cache = index
        _index_built_at = time.monotonic()
        return _index_cache


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
        search_notes = bool(arguments.get("search_notes", False))

        if search_notes:
            # Opt-in full-text scan (matches notes too). This reads every
            # reminder's body live and can be slow on large accounts; it is not
            # cached because the body content is the expensive part to fetch.
            raw = await _run_script(
                "search", query, str(include_completed).lower(), "true",
            )
            reminders = [r for r in (_parse_reminder(line)
                                     for line in raw.splitlines()) if r]
            return [TextContent(type="text", text=json.dumps(reminders, indent=2))]

        # Fast path: match titles against the cached index in memory, then
        # hydrate the remaining fields for the matches only.
        index = await _get_search_index()
        q = query.lower()
        matches = [e for e in index if q in e["title"].lower()]
        if not matches:
            return [TextContent(type="text", text=json.dumps([], indent=2))]

        # A broad query could match many reminders; each hydrate is an O(1)
        # lookup, so cap how many we resolve in one call to bound the cost.
        matches = matches[:100]
        raw = await _run_script("hydrate", *[e["id"] for e in matches])
        details: dict[str, dict] = {}
        for line in raw.splitlines():
            parts = line.split("|", maxsplit=3)
            if len(parts) == 4:
                rid, due, notes, done = parts
                details[rid] = {
                    "due_date": due or None,
                    "notes": notes or None,
                    "is_completed": done == "true",
                }

        reminders = []
        for e in matches:
            d = details.get(
                e["id"],
                {"due_date": None, "notes": None, "is_completed": False},
            )
            if not include_completed and d["is_completed"]:
                continue
            reminders.append({
                "id": e["id"],
                "title": e["title"],
                "due_date": d["due_date"],
                "notes": d["notes"],
                "is_completed": d["is_completed"],
                "list": e["list"],
            })
        return [TextContent(type="text", text=json.dumps(reminders, indent=2))]

    if name == "reminder_create":
        title = arguments["title"]
        list_name = arguments.get("list", "default")
        due_date = arguments.get("due_date", "")
        notes = arguments.get("notes", "")
        reminder_id = await _run_script("create", title, list_name, due_date, notes)
        _invalidate_index()
        return [TextContent(type="text", text=reminder_id)]

    if name == "reminder_complete":
        reminder_id = arguments["reminder_id"]
        await _run_script("complete", reminder_id)
        _invalidate_index()
        return [TextContent(type="text", text=f"Completed {reminder_id!r}.")]

    if name == "reminder_update":
        reminder_id = arguments["reminder_id"]
        title = arguments.get("title", "")
        # Distinguish "omitted" (leave unchanged) from an explicit value via a
        # sentinel, so a title-only update does not touch the due date.
        due_date = arguments["due_date"] if "due_date" in arguments else "__KEEP__"
        notes = arguments.get("notes", "")
        list_name = arguments.get("list", "")
        await _run_script("update", reminder_id, title, due_date, notes, list_name)
        _invalidate_index()
        return [TextContent(type="text", text=f"Updated {reminder_id!r}.")]

    if name == "reminder_delete":
        reminder_id = arguments["reminder_id"]
        await _run_script("delete", reminder_id)
        _invalidate_index()
        return [TextContent(type="text", text=f"Deleted {reminder_id!r}.")]

    raise ValueError(f"Unknown reminder tool: '{name}'.")
