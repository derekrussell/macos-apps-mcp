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
            "Returns a JSON object {status, total, offset, returned, has_more, "
            "reminders}, where reminders is an array whose elements have id, "
            "title, due_date, notes, is_completed, and list. Results are "
            "paginated via count/offset (total is the full match count); a broad "
            "query can match hundreds of reminders, so page through with offset "
            "when has_more is true. "
            "Default (title) search is served from an in-memory index maintained "
            "in the background for reliability on large accounts. Reminders you "
            "create/update/delete through these tools are reflected immediately; "
            "an edit made directly in the Apple Reminders app may take up to ~90s "
            "to appear. In the default search the notes field is returned as null "
            "(use search_notes, or reminder_get, to retrieve note text). "
            "status is usually 'ok'; on a cold start it may be 'warming' with an "
            "empty reminders array — simply retry after a few seconds. "
            "Note: search_notes runs a live full-text scan that is significantly "
            "slower on large accounts and may time out; leave it off unless you "
            "specifically need to match note text (it does populate notes)."
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


async def _run_script(action: str, *args: str, timeout: float = 60.0) -> str:
    """Run reminders.applescript with the given action and arguments.

    Args:
        action:  The action keyword the AppleScript handler dispatches on.
        *args:   Zero or more additional string arguments.
        timeout: Seconds to wait before killing osascript. Defaults to 60s to
                 stay under the MCP client timeout; the background index build
                 passes a longer budget since no client is awaiting it.

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
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
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
# Background search index
#
# reminder_search must never block on an AppleScript scan: on a large account a
# full scan takes tens of seconds and exceeds the MCP client timeout, and
# repeated scans cumulatively wedge EventKit until searches stop returning.
# So searches are served purely from an in-memory index that is (re)built in
# the BACKGROUND, off the client clock — a search returns instantly from
# whatever the index currently holds and triggers a refresh only when the data
# is stale (serve-stale-while-revalidate). Mutations update the index in place
# so a create-then-search sees the new reminder without waiting for a scan.
#
# The index carries id/title/list/due/completed. Notes are NOT indexed: default
# search returns the notes field as null. Use search_notes for a live note-text
# scan (slower, may time out), or reminder_get for a specific reminder's notes.
# ------------------------------------------------------------

_INDEX_TTL = 90.0            # seconds before a served-stale index triggers a refresh
_index_cache: list[dict] | None = None
_index_built_at: float = 0.0
_index_refreshing: bool = False


async def _build_index() -> None:
    """Rebuild the in-memory reminder index from a background AppleScript scan.

    Runs off the client hot path, so a slow or failed scan costs freshness, never
    a user-facing timeout. The previous index is kept on failure for a later retry.
    """
    global _index_cache, _index_built_at, _index_refreshing
    try:
        # No client is awaiting this call, so allow a generous budget for the
        # full-account scan (~45s on a healthy store, but much slower on a
        # temporarily degraded one). A long build only delays freshness.
        raw = await _run_script("build_index", timeout=240.0)
        index: list[dict] = []
        for line in raw.splitlines():
            parts = line.split("|", maxsplit=4)
            if len(parts) == 5:
                rid, title, list_name, due, done = parts
                index.append({
                    "id": rid,
                    "title": title,
                    "list": list_name,
                    "due_date": due or None,
                    "is_completed": done == "true",
                })
        _index_cache = index
        _index_built_at = time.monotonic()
    except Exception:
        # Keep serving the previous index; the next refresh will retry.
        pass
    finally:
        _index_refreshing = False


def _kick_refresh() -> None:
    """Start a background index rebuild unless one is already in flight."""
    global _index_refreshing
    if _index_refreshing:
        return
    _index_refreshing = True
    try:
        asyncio.create_task(_build_index())
    except RuntimeError:
        # No running event loop (e.g. imported outside asyncio); allow a retry.
        _index_refreshing = False


def warm_index() -> None:
    """Kick an initial background index build. Called once at server startup so
    the index is usually ready before the first search arrives."""
    _kick_refresh()


def _index_add(reminder_id, title, list_name, due_date, is_completed) -> None:
    """Insert a newly created reminder into the live index, if one is built."""
    if _index_cache is None:
        return
    _index_cache.append({
        "id": reminder_id,
        "title": title,
        "list": list_name,
        "due_date": due_date or None,
        "is_completed": is_completed,
    })


def _index_remove(reminder_id) -> None:
    """Drop a deleted reminder from the live index, if one is built."""
    global _index_cache
    if _index_cache is None:
        return
    _index_cache = [e for e in _index_cache if e["id"] != reminder_id]


def _index_edit(reminder_id, *, title=None, list_name=None,
                due_date=None, is_completed=None) -> None:
    """Apply a known field change to a reminder in the live index, if built.

    Only non-None arguments are applied. due_date of "" means unchanged (it
    cannot be cleared); a real ISO value replaces it.
    """
    if _index_cache is None:
        return
    for e in _index_cache:
        if e["id"] == reminder_id:
            if title:
                e["title"] = title
            if list_name:
                e["list"] = list_name
            if due_date:
                e["due_date"] = due_date
            if is_completed is not None:
                e["is_completed"] = is_completed
            break


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
        count = int(arguments.get("count", 50))
        offset = int(arguments.get("offset", 0))

        if search_notes:
            # Opt-in full-text scan (matches notes too). This reads every
            # reminder's body live and can be slow on large accounts; it is not
            # served from the index because notes are not indexed.
            raw = await _run_script(
                "search", query, str(include_completed).lower(), "true",
            )
            matches = [r for r in (_parse_reminder(line)
                                   for line in raw.splitlines()) if r]
        elif _index_cache is None:
            # Cold start: no index yet. Trigger a build and tell the caller to
            # retry rather than blocking on a scan that could exceed the client
            # timeout. Startup warm_index() usually makes this branch rare.
            _kick_refresh()
            return [TextContent(type="text", text=json.dumps({
                "status": "warming",
                "total": 0,
                "offset": offset,
                "returned": 0,
                "has_more": False,
                "reminders": [],
                "message": "Reminder search index is building; retry in a few seconds.",
            }, indent=2))]
        else:
            # Serve from the in-memory index immediately; refresh in the
            # background if it has gone stale (never block the search on a scan).
            if (time.monotonic() - _index_built_at) >= _INDEX_TTL:
                _kick_refresh()
            q = query.lower()
            matches = []
            for e in _index_cache:
                if q not in e["title"].lower():
                    continue
                if not include_completed and e["is_completed"]:
                    continue
                matches.append({
                    "id": e["id"],
                    "title": e["title"],
                    "due_date": e["due_date"],
                    "notes": None,
                    "is_completed": e["is_completed"],
                    "list": e["list"],
                })

        # Paginate: a broad query can match hundreds of reminders, and an
        # unbounded response overflows the client's payload limit.
        total = len(matches)
        page = matches[offset:offset + count] if count >= 0 else matches[offset:]
        return [TextContent(type="text", text=json.dumps({
            "status": "ok",
            "total": total,
            "offset": offset,
            "returned": len(page),
            "has_more": offset + len(page) < total,
            "reminders": page,
        }, indent=2))]

    if name == "reminder_create":
        title = arguments["title"]
        list_name = arguments.get("list", "default")
        due_date = arguments.get("due_date", "")
        notes = arguments.get("notes", "")
        raw = await _run_script("create", title, list_name, due_date, notes)
        # create returns "id|resolved_list_name"; use the resolved name so the
        # index carries the real list (e.g. "Reminders") rather than "default".
        reminder_id, _, resolved_list = raw.partition("|")
        # Update the index in place so a create-then-search finds the new
        # reminder immediately. Deliberately do NOT kick a full background
        # rebuild here: that scan contends with the next mutation's write and
        # can make it time out. External edits are reconciled by the periodic
        # refresh a stale search triggers.
        _index_add(reminder_id, title, resolved_list or list_name, due_date, False)
        return [TextContent(type="text", text=reminder_id)]

    if name == "reminder_complete":
        reminder_id = arguments["reminder_id"]
        await _run_script("complete", reminder_id)
        _index_edit(reminder_id, is_completed=True)
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
        # Apply the known field changes to the index; a "__KEEP__"/"" due date
        # means unchanged, so pass None for it in that case.
        _index_edit(
            reminder_id,
            title=title or None,
            list_name=list_name or None,
            due_date=due_date if due_date not in ("__KEEP__", "") else None,
        )
        return [TextContent(type="text", text=f"Updated {reminder_id!r}.")]

    if name == "reminder_delete":
        reminder_id = arguments["reminder_id"]
        await _run_script("delete", reminder_id)
        _index_remove(reminder_id)
        return [TextContent(type="text", text=f"Deleted {reminder_id!r}.")]

    raise ValueError(f"Unknown reminder tool: '{name}'.")
