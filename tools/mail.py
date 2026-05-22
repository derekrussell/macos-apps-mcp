"""Apple Mail tools for the MCP server.

Exposes four tools that let the client read, organise, and delete
emails in Apple Mail via AppleScript:

  mail_get_unread   — List unread messages in the inbox
  mail_get_body     — Fetch the full plain-text body of a message
  mail_move         — Move a message to the named mailbox
  mail_delete       — Move a message to the Trash

AppleScript is invoked via osascript, passing scripts/mail.applescript
as the script file and an action keyword as the first argument. All
four actions are handled by a single script file to keep scripts/
directory tidy.

Note:
  Message IDs used by these tools are RFC 2822 Message-IDs (the
  value of the Message-ID header), not internal Apple Mail indices.
  Always use the id field returned by mail_get_unread when calling
  the other three tools.
"""

import asyncio
import json
from pathlib import Path

from mcp.types import TextContent, Tool

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
            "date, and is_read. "
            "Defaults to all messages in the inbox. Set unread_only to "
            "true to filter to unread messages only. Set mailbox to query "
            "a different mailbox by name. "
            "Page through large mailboxes by incrementing offset by count "
            "until has_more is false."
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
                    "description": "Name of the mailbox to query. Defaults to the inbox."
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
            "mailbox by name. "
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
                    "description": "Name of the mailbox to query. Defaults to the inbox."
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="mail_list_mailboxes",
        description=(
            "List all mailboxes in Apple Mail with the message count for each. "
            "Use this to discover existing mailboxes before moving messages "
            "or planning an organisational structure."
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
            "Move an Apple Mail message to a named mailbox. "
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
                    "description": "Exact name of the destination mailbox."
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
        name="mail_delete_mailbox",
        description=(
            "Permanently delete a named mailbox and all messages it contains. "
            "This cannot be undone. "
            "Use mail_list_mailboxes to confirm the mailbox name before calling this."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mailbox": {
                    "type": "string",
                    "description": "Exact name of the mailbox to delete."
                },
            },
            "required": ["mailbox"],
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
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(
            f"osascript error (action={action!r}): "
            f"{stderr.decode().strip()}"
        )

    return stdout.decode().strip()


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
        <name>|<count>

    Returns:
        A list of dicts, each with keys: name, count.
        Returns an empty list if no mailboxes are found.
    """
    raw = await _run_script("list_mailboxes")

    if not raw:
        return []

    mailboxes = []
    for line in raw.splitlines():
        # Each record is pipe-delimited: name|count
        parts = line.split("|", maxsplit=1)
        if len(parts) == 2:
            name, count = parts
            mailboxes.append({
                "name": name,
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

    if name == "mail_delete_mailbox":
        mailbox = arguments["mailbox"]
        await _run_script("delete_mailbox", mailbox)
        return [TextContent(type="text", text=f"Deleted mailbox '{mailbox}'.")]

    raise ValueError(f"Unknown mail tool: '{name}'.")
