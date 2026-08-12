"""Apple Reminders tools for the MCP server.

Exposes seven tools that let the client read, create, update, and delete
reminders in Apple Reminders via AppleScript:

    reminder_list_lists  — List all reminder lists
    reminder_get         — Fetch reminders from a list, with pagination
    reminder_search      — Search reminders by text across all lists
    reminder_create      — Create a new reminder
    reminder_complete    — Mark a reminder as complete
    reminder_update      — Update the title, due date, notes, or list
    reminder_delete      — Delete a reminder

AppleScript is invoked via osascript, passing scripts/reminders.applescript as
the script file and an action keyword as the first argument.

Note:
    Reminder IDs used by these tools are the internal id values assigned by
    Apple Reminders. Always use the id field returned by reminder_get or
    reminder_search when calling the mutation tools (reminder_complete,
    reminder_update, reminder_delete).

    Due dates are expressed in ISO 8601 format: "YYYY-MM-DD" or
    "YYYY-MM-DDTHH:MM:SS". An existing due date cannot be cleared through
    AppleScript, so reminder_update leaves it unchanged when omitted.

    Pass "default" as the list name to target the user's default Reminders list
    without needing to know its name.
"""

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from mcp.types import TextContent, Tool

from ._osascript import DEFAULT_TIMEOUT_SECONDS, run_osascript
from ._responses import json_content, paginate, text_content

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_REMINDERS_SCRIPT = _SCRIPTS_DIR / "reminders.applescript"

# Sentinel the Python layer sends for reminder_update's due date when the caller
# omitted it, so the AppleScript can tell "leave unchanged" apart from an
# explicit value. Must match the sentinel handled in reminders.applescript.
_KEEP_DUE_DATE = "__KEEP__"

# Seconds a served index may age before a search triggers a background refresh.
_INDEX_TTL_SECONDS = 90.0

# Generous timeout for the background index build. No client is awaiting it, so a
# slow scan only delays freshness rather than causing a user-facing timeout.
_INDEX_BUILD_TIMEOUT_SECONDS = 240.0

# Internal time budget (seconds) handed to the AppleScript search_notes scan. The
# script checks it between chunks and stops COOPERATIVELY once exceeded, returning
# partial results with a "timeout" status — a full/hard-killed live scan wedges
# EventKit and stalls every later Reminders call (see issue #17).
_NOTES_SCAN_BUDGET_SECONDS = 40
# Extra wall-clock granted to the osascript call beyond the internal budget, so
# the script's own cooperative abort fires before run_osascript hard-kills it (a
# hard kill can't interrupt an in-flight Apple event, which is what wedges).
_NOTES_SCAN_TIMEOUT_MARGIN_SECONDS = 15

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
            "slower on large accounts; leave it off unless you specifically need "
            "to match note text (it does populate notes). It is bounded by an "
            "internal time budget — if it can't finish in time it returns the "
            "matches found so far with status 'timeout' (partial results) plus a "
            "message, rather than hanging; narrow the query and retry, or page "
            "with reminder_get."
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
# Section C — osascript wrapper
# ------------------------------------------------------------

async def _run_script(
    action: str,
    *arguments: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run reminders.applescript through the shared osascript runner."""
    return await run_osascript(
        _REMINDERS_SCRIPT, action, *arguments, timeout=timeout
    )


# ------------------------------------------------------------
# Section D — Output parsers (pure)
# ------------------------------------------------------------

def _parse_list_line(line: str) -> dict | None:
    """Parse one "name|count" reminder-list line into a dict.

    Returns None if the line does not split into exactly two fields.
    """
    parts = line.split("|", maxsplit=1)
    if len(parts) != 2:
        return None
    name, count_text = parts
    return {"name": name, "count": int(count_text)}


def _parse_reminder(line: str) -> dict | None:
    """Parse one pipe-delimited reminder record into a dict.

    Expected format: id|title|due_date|notes|is_completed|list

    Empty due_date/notes become None. Returns None if the line does not contain
    exactly six fields.
    """
    parts = line.split("|", maxsplit=5)
    if len(parts) != 6:
        return None
    reminder_id, title, due_date, notes, is_completed, list_name = parts
    return {
        "id": reminder_id,
        "title": title,
        "due_date": due_date or None,
        "notes": notes or None,
        "is_completed": is_completed == "true",
        "list": list_name,
    }


# ------------------------------------------------------------
# Section E — Background search index
#
# reminder_search must never block on an AppleScript scan: on a large account a
# full scan takes tens of seconds and exceeds the MCP client timeout, and
# repeated scans cumulatively wedge EventKit until searches stop returning. So
# searches are served purely from an in-memory index that is (re)built in the
# BACKGROUND, off the client clock — a search returns instantly from whatever the
# index currently holds and triggers a refresh only when the data is stale
# (serve-stale-while-revalidate). Mutations update the index in place so a
# create-then-search sees the new reminder without waiting for a scan.
#
# The index carries id/title/list/due/completed. Notes are NOT indexed: default
# search returns the notes field as null. Use search_notes for a live note-text
# scan (slower, may time out), or reminder_get for a specific reminder's notes.
# ------------------------------------------------------------

@dataclass
class IndexedReminder:
    """A single reminder as held in the in-memory search index."""

    id: str
    title: str
    list_name: str
    due_date: str | None
    is_completed: bool


class ReminderSearchIndex:
    """An in-memory snapshot of reminders for fast, non-blocking title search.

    Holds no I/O logic of its own, so it can be unit-tested in isolation: build
    it with ``replace``, mutate it with ``add``/``edit``/``remove``, and query
    it with ``search``.
    """

    def __init__(self) -> None:
        # None means "never built yet"; an empty list means "built and empty".
        self._reminders: list[IndexedReminder] | None = None
        self._built_at_monotonic: float = 0.0

    @property
    def is_built(self) -> bool:
        """True once the index has been populated at least once."""
        return self._reminders is not None

    def is_stale(self, ttl_seconds: float, now: float | None = None) -> bool:
        """True if the index is older than ``ttl_seconds``.

        ``now`` defaults to the current monotonic clock; it is a parameter so
        staleness can be tested deterministically.
        """
        current_time = time.monotonic() if now is None else now
        return (current_time - self._built_at_monotonic) >= ttl_seconds

    def replace(self, reminders, built_at: float | None = None) -> None:
        """Replace the whole index with a freshly scanned set of reminders."""
        self._reminders = list(reminders)
        self._built_at_monotonic = (
            time.monotonic() if built_at is None else built_at
        )

    def add(
        self,
        reminder_id: str,
        title: str,
        list_name: str,
        due_date: str,
        is_completed: bool,
    ) -> None:
        """Add a newly created reminder, if the index has been built."""
        if self._reminders is None:
            return
        self._reminders.append(IndexedReminder(
            id=reminder_id,
            title=title,
            list_name=list_name,
            due_date=due_date or None,
            is_completed=is_completed,
        ))

    def remove(self, reminder_id: str) -> None:
        """Drop a deleted reminder, if the index has been built."""
        if self._reminders is None:
            return
        self._reminders = [
            reminder for reminder in self._reminders
            if reminder.id != reminder_id
        ]

    def edit(
        self,
        reminder_id: str,
        *,
        title: str | None = None,
        list_name: str | None = None,
        due_date: str | None = None,
        is_completed: bool | None = None,
    ) -> None:
        """Apply known field changes to one reminder, if the index is built.

        Only non-empty arguments are applied (a None or "" leaves that field
        untouched), matching the "omit to keep unchanged" mutation semantics.
        """
        if self._reminders is None:
            return
        for reminder in self._reminders:
            if reminder.id != reminder_id:
                continue
            if title:
                reminder.title = title
            if list_name:
                reminder.list_name = list_name
            if due_date:
                reminder.due_date = due_date
            if is_completed is not None:
                reminder.is_completed = is_completed
            return

    def search(self, query: str, include_completed: bool) -> list[dict]:
        """Return reminders whose title contains ``query`` (case-insensitive).

        Each result is a dict in the tool's output shape, with notes set to None
        because notes are not indexed. Returns an empty list if not yet built.
        """
        if self._reminders is None:
            return []
        lowered_query = query.lower()
        results = []
        for reminder in self._reminders:
            if lowered_query not in reminder.title.lower():
                continue
            if not include_completed and reminder.is_completed:
                continue
            results.append({
                "id": reminder.id,
                "title": reminder.title,
                "due_date": reminder.due_date,
                "notes": None,
                "is_completed": reminder.is_completed,
                "list": reminder.list_name,
            })
        return results


# The single process-wide index instance and its background-refresh state.
_search_index = ReminderSearchIndex()
_refresh_in_progress = False


def _parse_index_line(line: str) -> IndexedReminder | None:
    """Parse one "id|title|list|due_date|is_completed" index line.

    Returns None if the line does not split into exactly five fields.
    """
    parts = line.split("|", maxsplit=4)
    if len(parts) != 5:
        return None
    reminder_id, title, list_name, due_date, completed_flag = parts
    return IndexedReminder(
        id=reminder_id,
        title=title,
        list_name=list_name,
        due_date=due_date or None,
        is_completed=completed_flag == "true",
    )


async def _build_index() -> None:
    """Rebuild the in-memory index from a background AppleScript scan.

    Runs off the client hot path, so a slow or failed scan costs freshness, not
    a user-facing timeout. The previous index is kept on failure so the next
    refresh can retry.
    """
    global _refresh_in_progress
    try:
        raw = await _run_script(
            "build_index", timeout=_INDEX_BUILD_TIMEOUT_SECONDS
        )
        reminders = [
            parsed for parsed in
            (_parse_index_line(line) for line in raw.splitlines())
            if parsed is not None
        ]
        _search_index.replace(reminders)
    except Exception:
        # Keep serving the previous index; the next refresh will retry.
        pass
    finally:
        _refresh_in_progress = False


def _kick_refresh() -> None:
    """Start a background index rebuild unless one is already in flight."""
    global _refresh_in_progress
    if _refresh_in_progress:
        return
    _refresh_in_progress = True
    try:
        asyncio.create_task(_build_index())
    except RuntimeError:
        # No running event loop (e.g. imported outside asyncio); allow a retry.
        _refresh_in_progress = False


def warm_index() -> None:
    """Kick an initial background index build.

    Called once at server startup so the index is usually ready before the first
    search arrives.
    """
    _kick_refresh()


# ------------------------------------------------------------
# Section F — Per-tool handlers
# ------------------------------------------------------------

async def _handle_list_lists(arguments: dict) -> list[TextContent]:
    del arguments  # no arguments; the parameter exists for a uniform dispatch signature
    raw = await _run_script("list_lists")
    lists = [
        parsed for parsed in
        (_parse_list_line(line) for line in raw.splitlines())
        if parsed is not None
    ]
    return json_content(lists)


async def _handle_get(arguments: dict) -> list[TextContent]:
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

    # The script paginates and puts the full matching count on the first line.
    lines = raw.splitlines() if raw else []
    total = int(lines[0]) if lines else 0
    reminders = [
        parsed for parsed in
        (_parse_reminder(line) for line in lines[1:])
        if parsed is not None
    ]
    returned = len(reminders)
    return json_content({
        "total": total,
        "offset": offset,
        "returned": returned,
        "has_more": offset + returned < total,
        "reminders": reminders,
    })


async def _search_reminder_notes_live(
    query: str, include_completed: bool
) -> tuple[str, list[dict]]:
    """Run the opt-in live full-text scan (matches notes as well as titles).

    Reads every reminder's body via AppleScript, so it is slow on large accounts
    and is not served from the index. Populates the notes field.

    The scan is bounded by an internal time budget: its first output line is a
    status ("ok" if it finished, "timeout" if it hit the budget and returned
    partial results), and the remaining lines are reminder records. Returns a
    (status, reminders) tuple. The osascript timeout is set above the budget so
    the script aborts cooperatively before it can be hard-killed mid-scan.
    """
    raw = await _run_script(
        "search",
        query,
        str(include_completed).lower(),
        "true",
        str(_NOTES_SCAN_BUDGET_SECONDS),
        timeout=_NOTES_SCAN_BUDGET_SECONDS + _NOTES_SCAN_TIMEOUT_MARGIN_SECONDS,
    )
    lines = raw.splitlines() if raw else []
    status = lines[0] if lines else "ok"
    reminders = [
        parsed for parsed in
        (_parse_reminder(line) for line in lines[1:])
        if parsed is not None
    ]
    return status, reminders


def _warming_search_response(offset: int) -> dict:
    """The response returned while the index is still building on a cold start."""
    return {
        "status": "warming",
        "total": 0,
        "offset": offset,
        "returned": 0,
        "has_more": False,
        "reminders": [],
        "message": "Reminder search index is building; retry in a few seconds.",
    }


async def _handle_search(arguments: dict) -> list[TextContent]:
    query = arguments["query"]
    include_completed = bool(arguments.get("include_completed", False))
    search_notes = bool(arguments.get("search_notes", False))
    count = int(arguments.get("count", 50))
    offset = int(arguments.get("offset", 0))

    status = "ok"
    if search_notes:
        # Opt-in live scan: it self-bounds and reports "ok" or "timeout" (partial
        # results) so it can't wedge EventKit by running/being killed to the end.
        status, matches = await _search_reminder_notes_live(query, include_completed)
    elif not _search_index.is_built:
        # Cold start: trigger a build and ask the caller to retry rather than
        # blocking on a scan that could exceed the client timeout. Startup
        # warm_index() usually makes this branch rare.
        _kick_refresh()
        return json_content(_warming_search_response(offset))
    else:
        # Serve instantly from the index; refresh in the background if stale so
        # the search itself never waits on a scan.
        if _search_index.is_stale(_INDEX_TTL_SECONDS):
            _kick_refresh()
        matches = _search_index.search(query, include_completed)

    page, total, has_more = paginate(matches, offset, count)
    response = {
        "status": status,
        "total": total,
        "offset": offset,
        "returned": len(page),
        "has_more": has_more,
        "reminders": page,
    }
    if status == "timeout":
        # Partial results: the note scan hit its time budget before finishing.
        response["message"] = (
            "Note search reached its time budget and returned partial results. "
            "Narrow the query, or use reminder_get to read a specific list."
        )
    return json_content(response)


async def _handle_create(arguments: dict) -> list[TextContent]:
    title = arguments["title"]
    list_name = arguments.get("list", "default")
    due_date = arguments.get("due_date", "")
    notes = arguments.get("notes", "")

    # create returns "id|resolved_list_name"; use the resolved name so the index
    # carries the real list (e.g. "Reminders") rather than the "default" alias.
    raw = await _run_script("create", title, list_name, due_date, notes)
    reminder_id, _, resolved_list_name = raw.partition("|")

    # Update the index in place so a create-then-search finds the new reminder
    # immediately. Deliberately do NOT kick a full background rebuild here: that
    # scan contends with the next mutation's write and can make it time out.
    _search_index.add(
        reminder_id=reminder_id,
        title=title,
        list_name=resolved_list_name or list_name,
        due_date=due_date,
        is_completed=False,
    )
    return text_content(reminder_id)


async def _handle_complete(arguments: dict) -> list[TextContent]:
    reminder_id = arguments["reminder_id"]
    await _run_script("complete", reminder_id)
    _search_index.edit(reminder_id, is_completed=True)
    return text_content(f"Completed {reminder_id!r}.")


async def _handle_update(arguments: dict) -> list[TextContent]:
    reminder_id = arguments["reminder_id"]
    title = arguments.get("title", "")
    notes = arguments.get("notes", "")
    list_name = arguments.get("list", "")
    # Distinguish "omitted" (leave unchanged) from an explicit value via the
    # sentinel, so a title-only update does not touch the due date.
    due_date = arguments["due_date"] if "due_date" in arguments else _KEEP_DUE_DATE

    await _run_script("update", reminder_id, title, due_date, notes, list_name)

    # Mirror the applied changes in the index. A "__KEEP__"/"" due date means
    # unchanged, so pass None for it in that case.
    due_date_changed = due_date not in (_KEEP_DUE_DATE, "")
    _search_index.edit(
        reminder_id,
        title=title or None,
        list_name=list_name or None,
        due_date=due_date if due_date_changed else None,
    )
    return text_content(f"Updated {reminder_id!r}.")


async def _handle_delete(arguments: dict) -> list[TextContent]:
    reminder_id = arguments["reminder_id"]
    await _run_script("delete", reminder_id)
    _search_index.remove(reminder_id)
    return text_content(f"Deleted {reminder_id!r}.")


_TOOL_HANDLERS = {
    "reminder_list_lists": _handle_list_lists,
    "reminder_get": _handle_get,
    "reminder_search": _handle_search,
    "reminder_create": _handle_create,
    "reminder_complete": _handle_complete,
    "reminder_update": _handle_update,
    "reminder_delete": _handle_delete,
}


# ------------------------------------------------------------
# Section G — Public interface
# ------------------------------------------------------------

async def list_tools() -> list[Tool]:
    """Return the Tool definitions for the reminders domain."""
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a reminder tool call to its handler.

    Args:
        name:      The tool name, e.g. "reminder_create".
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
        raise ValueError(f"Unknown reminder tool: '{name}'.")
    return await tool_handler(arguments)
