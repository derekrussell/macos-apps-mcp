"""Apple Mail tools for the MCP server.

Exposes eight tools that let the client read, organise, and delete
emails in Apple Mail via AppleScript:

  mail_get_messages    — List messages in a mailbox, with pagination
  mail_search          — Search a mailbox by sender/subject/body/date/read
  mail_count_messages  — Count messages in a mailbox
  mail_list_mailboxes  — List all mailboxes with their message counts
  mail_get_body        — Fetch the full plain-text body of a message
  mail_move            — Move a message to another mailbox
  mail_delete          — Move a message to the Trash
  mail_rename_mailbox  — Rename a mailbox (deletion isn't scriptable in Mail)

AppleScript is invoked via osascript, passing scripts/mail.applescript
as the script file and an action keyword as the first argument. All
actions are handled by a single script file to keep the scripts/
directory tidy.

Note:
  Message IDs used by these tools are RFC 2822 Message-IDs (the
  value of the Message-ID header), not internal Apple Mail indices.
  Always use the id field returned by mail_get_messages when calling
  the other tools.

  Mailboxes are addressed by their account-qualified path, e.g.
  "iCloud/Church/Transactions", exactly as returned by
  mail_list_mailboxes. This disambiguates mailboxes that share a
  leaf name. Pass "inbox" as a shortcut for the unified inbox.
"""

import asyncio
import json
from pathlib import Path

from mcp.types import TextContent, Tool

from ._osascript import clean_osascript_error

# Path to the AppleScript that handles all mail actions.
# Resolved relative to this file so it works regardless of the
# working directory the server is launched from.
_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
_MAIL_SCRIPT = _SCRIPTS_DIR / "mail.applescript"


# ------------------------------------------------------------
# Section B — Tool definitions
# ------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="mail_get_messages",
        description=(
            "List messages in an Apple Mail mailbox, with pagination. "
            "Returns a JSON object with: total (count matching the filter), "
            "offset, returned (messages in this batch), has_more, and a "
            "messages array where each element has id, subject, sender, "
            "date (ISO 8601: YYYY-MM-DDTHH:MM:SS), and is_read. "
            "Defaults to all messages in the inbox. Set unread_only to "
            "true to filter to unread messages only. Set mailbox to query "
            "a different mailbox by its account-qualified path. "
            "Page through large mailboxes by incrementing offset by count "
            "until has_more is false. "
            "Messages are returned in Mail's native order (roughly most-recently-"
            "received first), which is NOT strictly sorted by the returned date "
            "(the sent date), so two messages sent seconds apart can appear out of "
            "order. To reliably select messages by date, use mail_search with "
            "since/until rather than taking the top of this list."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of messages to return in this batch.",
                    "default": 50,
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based index of the first message to return.",
                    "default": 0,
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, return only unread messages. Defaults to false.",
                    "default": False,
                },
                "mailbox": {
                    "type": "string",
                    "description": "Account-qualified mailbox path from mail_list_mailboxes (e.g. 'iCloud/Church/Transactions'), or 'inbox'. Defaults to the inbox."
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="mail_search",
        description=(
            "Search messages within a single Apple Mail mailbox by sender, "
            "subject, body text, date range, and read state — instead of paging "
            "mail_get_messages over a large mailbox. "
            "All supplied criteria are combined with AND; omit a field to ignore "
            "it. At least one of sender, subject, body, since, or until is "
            "required (to list a whole mailbox, use mail_get_messages). "
            "Returns a JSON object {status, total, offset, returned, has_more, "
            "messages}, where each message has id, subject, sender, date "
            "(ISO 8601: YYYY-MM-DDTHH:MM:SS), and is_read. Paginated via "
            "count/offset; page with offset while has_more is true. "
            "Searches the inbox by default; set mailbox to an account-qualified "
            "path to search a different one. "
            "Note: body search reads message bodies and is slower — it is most "
            "effective combined with sender/subject/date criteria; a body-only "
            "search of a large mailbox may time out. This searches one mailbox, "
            "not across all mailboxes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "sender": {
                    "type": "string",
                    "description": "Substring to match in the sender name or address (case-insensitive).",
                },
                "subject": {
                    "type": "string",
                    "description": "Substring to match in the subject (case-insensitive).",
                },
                "body": {
                    "type": "string",
                    "description": "Substring to match in the message body (case-insensitive). Slower — reads bodies; best combined with other criteria.",
                },
                "since": {
                    "type": "string",
                    "description": "Only messages sent on or after this date (ISO 8601 YYYY-MM-DD).",
                },
                "until": {
                    "type": "string",
                    "description": "Only messages sent on or before this date, inclusive of the whole day (ISO 8601 YYYY-MM-DD).",
                },
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, only unread messages. Defaults to false.",
                    "default": False,
                },
                "mailbox": {
                    "type": "string",
                    "description": "Account-qualified mailbox path from mail_list_mailboxes (e.g. 'iCloud/Church/Transactions'), or 'inbox'. Defaults to the inbox.",
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
            "required": [],
        },
    ),
    Tool(
        name="mail_count_messages",
        description=(
            "Return the total number of messages in an Apple Mail mailbox. "
            "Defaults to all messages in the inbox. Set unread_only to true "
            "to count only unread messages. Set mailbox to query a different "
            "mailbox by its account-qualified path. "
            "Call this before mail_get_messages when processing large "
            "mailboxes to determine how many batches are needed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, count only unread messages. Defaults to false.",
                    "default": False
                },
                "mailbox": {
                    "type": "string",
                    "description": "Account-qualified mailbox path from mail_list_mailboxes (e.g. 'iCloud/Church/Transactions'), or 'inbox'. Defaults to the inbox."
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="mail_list_mailboxes",
        description=(
            "List all mailboxes in Apple Mail with the message count for each. "
            "Returns a JSON array where each element has path and count. The "
            "path is account-qualified (e.g. 'iCloud/Church/Transactions') and "
            "is the exact value to pass as the mailbox argument to other mail "
            "tools. Use this to discover existing mailboxes before moving "
            "messages or planning an organisational structure."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    Tool(
        name="mail_get_body",
        description=(
            "Fetch the full plain-text body of a specific Apple Mail message. "
            "Searches all mailboxes. "
            "Use the id returned by mail_get_messages as message_id."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "RFC 2822 Message-ID of the message to retrieve."
                },
            },
            "required": ["message_id"],
        },
    ),
    Tool(
        name="mail_move",
        description=(
            "Move an Apple Mail message to another mailbox. "
            "Searches all mailboxes for the message. "
            "The destination mailbox must already exist in Apple Mail."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "RFC 2822 Message-ID of the message to move.",
                },
                "mailbox": {
                    "type": "string",
                    "description": "Account-qualified path of the destination mailbox from mail_list_mailboxes (e.g. 'iCloud/Church/Transactions')."
                },
            },
            "required": ["message_id", "mailbox"],
        },
    ),
    Tool(
        name="mail_delete",
        description=(
            "Move an Apple Mail message to the Trash. "
            "Searches all mailboxes for the message. "
            "Soft delete only — the message is recoverable from Trash."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "RFC 2822 Message-ID of the message to delete."
                },
            },
            "required": ["message_id"],
        },
    ),
    Tool(
        name="mail_rename_mailbox",
        description=(
            "Rename an Apple Mail mailbox (change its leaf name; it stays in the "
            "same account and parent). Returns the new account-qualified path. "
            "Use mail_list_mailboxes to get the current path first. "
            "Deleting a mailbox is NOT possible via AppleScript — Apple Mail's "
            "`delete` fails with a -10000 error for every mailbox type (local and "
            "iCloud/IMAP), a long-standing limitation. Renaming is therefore the "
            "supported way to flag a mailbox for manual removal: rename it (e.g. "
            "prefix with 'DELETE ME - ') and tell the user to delete it in Mail.app "
            "(right-click the mailbox -> Delete Mailbox)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mailbox": {
                    "type": "string",
                    "description": "Current account-qualified path of the mailbox from mail_list_mailboxes (e.g. 'iCloud/Old Project')."
                },
                "new_name": {
                    "type": "string",
                    "description": "New leaf name for the mailbox. A plain name, not a path — must not contain '/'."
                },
            },
            "required": ["mailbox", "new_name"],
        },
    ),
]


# ------------------------------------------------------------
# Section C — Private helpers
# ------------------------------------------------------------

async def _run_script(action: str, *args: str) -> str:
    """Run mail.applescript with the given action and arguments.

    Invokes osascript as a subprocess, passing the script file path
    followed by the action keyword and any additional string arguments.
    Raises on non-zero exit so callers never have to inspect returncode.

    Args:
        action: The action keyword the AppleScript handler dispatches on
                (e.g. "get_unread", "get_body", "move", "delete").
        *args:  Zero or more additional string arguments appended after
                the action (e.g. a message_id, a mailbox name).

    Returns:
        The trimmed stdout produced by the script.

    Raises:
        RuntimeError: If osascript exits with a non-zero return code,
                      with the stderr output included in the message.
    """
    proc = await asyncio.create_subprocess_exec(
        "osascript", str(_MAIL_SCRIPT), action, *args,
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
        raise RuntimeError(clean_osascript_error(stderr.decode()))

    # Normalise line endings so splitlines() does not treat a stray CR as a
    # record boundary and shift fields (mirrors the reminders/notes tools).
    return stdout.decode().replace('\r\n', '\n').replace('\r', '\n').strip()


async def _get_messages(
    count: int,
    offset: int = 0,
    unread_only: bool = False,
    mailbox: str = "inbox",
) -> dict:
    """Fetch a paginated batch of messages from an Apple Mail mailbox.

    Calls the AppleScript get_messages action, which returns a header line containing the total message count, followed by one pipe-delimited record per message in the form:
        <message_id>|<subject>|<sender>|<date>|<is_read>

    Args:
        count:          Number of messages to return in this batch.
        offset:         Zero-based index of the first message to return.
        unread_only:    If True, return only unread messages.
        mailbox:        Name of the mailbox to query. Defaults to inbox.

    Returns:
        A dict with keys:
            total:      Total messages in the mailbox matching the filter.
            offset:     The offset used for this batch.
            returned:   Number of messages in this batch.
            has_more:   True if more messages remain after this batch.
            messages:   List of dicts with keys id, subject, sender, date, is_read.
    """
    raw = await _run_script(
        "get_messages",
        str(count),
        str(offset),
        str(unread_only).lower(),
        mailbox,
    )

    if not raw:
        return {
            "total": 0,
            "offset": offset,
            "returned": 0,
            "has_more": False,
            "messages": [],
        }

    lines = raw.splitlines()
    # First line is the total message count returned by the script.
    total = int(lines[0])

    messages = []
    for line in lines[1:]:
        # Each record is pipe-delimited: message_id|subject|sender|date|is_read
        parts = line.split("|", maxsplit=4)
        if len(parts) == 5:
            message_id, subject, sender, date, is_read = parts
            messages.append({
                "id": message_id,
                "subject": subject,
                "sender": sender,
                "date": date,
                "is_read": is_read == "true",
            })

    return {
        "total": total,
        "offset": offset,
        "returned": len(messages),
        "has_more": offset + len(messages) < total,
        "messages": messages,
    }


async def _search_messages(
    mailbox: str,
    sender: str,
    subject: str,
    body: str,
    since: str,
    until: str,
    unread_only: bool,
    count: int,
    offset: int,
) -> dict:
    """Search a single mailbox and return a paginated batch of matches.

    Calls the AppleScript search action, which pushes sender/subject/date/read
    filters into a native `whose` clause and returns a header line with the
    total match count followed by one pipe-delimited record per message:
        <message_id>|<subject>|<sender>|<date>|<is_read>

    The script paginates, so `total` is the full match count while the record
    lines are just the requested page.

    Returns:
        A dict with keys: status, total, offset, returned, has_more, messages
        (each message a dict with id, subject, sender, date, is_read).
    """
    raw = await _run_script(
        "search",
        mailbox, sender, subject, body, since, until,
        str(unread_only).lower(), str(count), str(offset),
    )

    if not raw:
        return {
            "status": "ok",
            "total": 0,
            "offset": offset,
            "returned": 0,
            "has_more": False,
            "messages": [],
        }

    lines = raw.splitlines()
    total = int(lines[0])

    messages = []
    for line in lines[1:]:
        # Each record is pipe-delimited: message_id|subject|sender|date|is_read
        parts = line.split("|", maxsplit=4)
        if len(parts) == 5:
            message_id, subject_f, sender_f, date, is_read = parts
            messages.append({
                "id": message_id,
                "subject": subject_f,
                "sender": sender_f,
                "date": date,
                "is_read": is_read == "true",
            })

    return {
        "status": "ok",
        "total": total,
        "offset": offset,
        "returned": len(messages),
        "has_more": offset + len(messages) < total,
        "messages": messages,
    }


async def _count_messages(
    unread_only: bool = False,
    mailbox: str = "inbox",
) -> int:
    """Return the total number of messages in an Apple Mail mailbox.

    Calls the AppleScript count_messages action, which returns a single integer as a string. This is a lightweight call — no message data is fetched — making it suitable for checking scale before paginating.

    Args:
        unread_only:    If True, count only unread messages.
        mailbox:        Name of the mailbox to query. Defaults to inbox.

    Returns:
        The total number of messages matching the filter.
    """
    raw = await _run_script(
        "count_messages",
        str(unread_only).lower(),
        mailbox,
    )
    return int(raw)


async def _list_mailboxes() -> list[dict]:
    """Return all mailboxes in Apple Mail with their message counts.

    Calls the AppleScript list_mailboxes action, which returns one
    pipe-delimited record per mailbox in the form:
        <path>|<count>

    The path is account-qualified (e.g. "iCloud/Church/Transactions")
    and is the value callers pass back as the mailbox argument.

    Returns:
        A list of dicts, each with keys: path, count.
        Returns an empty list if no mailboxes are found.
    """
    raw = await _run_script("list_mailboxes")

    if not raw:
        return []

    mailboxes = []
    for line in raw.splitlines():
        # Each record is pipe-delimited: path|count
        parts = line.rsplit("|", maxsplit=1)
        if len(parts) == 2:
            path, count = parts
            mailboxes.append({
                "path": path,
                "count": int(count),
            })
    return mailboxes


# ------------------------------------------------------------
# Section D — Public interface
# ------------------------------------------------------------

async def list_tools() -> list[Tool]:
    """Return the Tool definitions for the mail domain.

    Called once by server.py on startup so the client knows what
    mail actions are available.

    Returns:
        The module-level TOOLS list containing all mail Tool definitions.
    """
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a mail tool call to the correct implementation.

    Called by server.py whenever the client invokes a mail_* tool.
    Unpacks arguments, calls the appropriate helper, and wraps the
    result in a TextContent object for the MCP response.

    Args:
        name:       The tool name, e.g. "mail_get_messages".
        arguments:  Dict of validated arguments from the client.

    Returns:
        A single-element list containing a TextContent with the
        result as text or JSON.

    Raises:
        ValueError:     If the tool name is not recognised.
        RuntimeError:   If the underlying AppleScript call fails.
    """
    if name == "mail_get_messages":
        count = int(arguments.get("count", 50))
        offset = int(arguments.get("offset", 0))
        unread_only = bool(arguments.get("unread_only", False))
        mailbox = arguments.get("mailbox", "inbox")
        result = await _get_messages(count, offset, unread_only, mailbox)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "mail_search":
        sender = arguments.get("sender", "")
        subject = arguments.get("subject", "")
        body = arguments.get("body", "")
        since = arguments.get("since", "")
        until = arguments.get("until", "")
        unread_only = bool(arguments.get("unread_only", False))
        mailbox = arguments.get("mailbox", "inbox")
        count = int(arguments.get("count", 50))
        offset = int(arguments.get("offset", 0))

        # Require a real criterion so we never silently dump a whole mailbox;
        # unread_only is a modifier, not a search term on its own.
        if not any([sender, subject, body, since, until]):
            raise ValueError(
                "mail_search needs at least one of: sender, subject, body, "
                "since, until. To list a whole mailbox, use mail_get_messages."
            )

        result = await _search_messages(
            mailbox, sender, subject, body, since, until,
            unread_only, count, offset,
        )
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "mail_count_messages":
        unread_only = bool(arguments.get("unread_only", False))
        mailbox = arguments.get("mailbox", "inbox")
        total = await _count_messages(unread_only, mailbox)
        return [TextContent(type="text", text=str(total))]

    if name == "mail_list_mailboxes":
        result = await _list_mailboxes()
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name == "mail_get_body":
        message_id = arguments["message_id"]
        body = await _run_script("get_body", message_id)
        return [TextContent(type="text", text=body)]

    if name == "mail_move":
        message_id = arguments["message_id"]
        mailbox = arguments["mailbox"]
        await _run_script("move", message_id, mailbox)
        return [TextContent(type="text", text=f"Moved {message_id!r} to '{mailbox}'.")]

    if name == "mail_delete":
        message_id = arguments["message_id"]
        await _run_script("delete", message_id)
        return [TextContent(type="text", text=f"Deleted {message_id!r}.")]

    if name == "mail_rename_mailbox":
        mailbox = arguments["mailbox"]
        new_name = arguments["new_name"]
        if "/" in new_name:
            raise ValueError(
                "new_name must be a plain mailbox name, not a path (no '/')."
            )
        new_path = await _run_script("rename_mailbox", mailbox, new_name)
        return [TextContent(type="text", text=f"Renamed '{mailbox}' to '{new_path}'.")]

    raise ValueError(f"Unknown mail tool: '{name}'.")
