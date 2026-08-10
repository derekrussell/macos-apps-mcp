"""Apple Mail tools for the MCP server.

Exposes nine tools that let the client read, organise, search, and delete emails
in Apple Mail via AppleScript:

  mail_get_messages    — List messages in a mailbox, with pagination
  mail_search          — Search a mailbox by sender/subject/body/date/read
  mail_count_messages  — Count messages in a mailbox
  mail_list_mailboxes  — List all mailboxes with their message counts
  mail_get_body        — Fetch the full plain-text body of a message
  mail_get_images      — Fetch the full plain-text body of a message
  mail_move            — Move a message to another mailbox
  mail_delete          — Move a message to the Trash
  mail_rename_mailbox  — Rename a mailbox (deletion isn't scriptable in Mail)
  mail_create_mailbox  — Create a mailbox under an account

AppleScript is invoked via osascript, passing scripts/mail.applescript as the
script file and an action keyword as the first argument.

Note:
    Message IDs used by these tools are RFC 2822 Message-IDs (the value of the
    Message-ID header), not internal Apple Mail indices. Always use the id field
    returned by mail_get_messages or mail_search when calling the other tools.

    Mailboxes are addressed by their account-qualified path, e.g.
    "iCloud/Church/Transactions", exactly as returned by mail_list_mailboxes.
    This disambiguates mailboxes that share a leaf name. Pass "inbox" as a
    shortcut for the unified inbox.
"""

import email
import re
from html.parser import HTMLParser
from pathlib import Path

from mcp.types import TextContent, Tool

from ._osascript import DEFAULT_TIMEOUT_SECONDS, run_osascript
from ._responses import json_content, text_content

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
        name="mail_get_images",
        description=(
            "Extract the images reference in an Apple Mail message's HTML body. "
            "mail_get_body returns only Mail's plain-text rendering, which drops "
            "linked <img> images (e.g. newsletter or Patreon artwork); this reads "
            "the message's raw MIM source and returns the image URLs so the "
            "client can fetch or view them. Searches all mailboxes for the "
            "message. Returns a JSON object {status, message_id, total, "
            "tracker_count, images}, where each image has url, alt, width, height "
            "(width/height are integers or null), and likely_tracker (true for "
            "probable 1x1 tracking pixels / beacons). The server extracts URLs "
            "only — it does NOT fetch remote content. Use the id from "
            "mail_get_message or mail_search as message_id."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "string",
                    "description": "RFC 2822 Message-ID of the message to extract images from."
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
    Tool(
        name="mail_create_mailbox",
        description=(
            "Create a mailbox in Apple Mail under an existing account. "
            "mailbox is an account-qualified path: the first segment is the "
            "account name (as shown by mail_list_mailboxes, e.g. 'iCloud') and the "
            "rest is the mailbox to create ('iCloud/Receipts', or nested "
            "'iCloud/Projects/2026'). "
            "Nested paths are created level by level, top-down, so every "
            "intermediate is a normal selectable mailbox (listable, resolvable, and "
            "a valid mail_move destination) rather than a non-selectable container. "
            "Idempotent: if the mailbox already exists it is left unchanged. "
            "Returns a JSON object {status, path, created}, where created is false "
            "if it already existed. "
            "Note: creation syncs to iCloud/IMAP, but a mailbox cannot be DELETED "
            "via AppleScript (a long-standing Mail limitation); deletion is manual, "
            "and mail_rename_mailbox is the way to flag one, so create deliberately."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mailbox": {
                    "type": "string",
                    "description": "Account-qualified path to create, e.g. 'iCloud/Receipts' or 'iCloud/Projects/2026'. The first segment is the account name."
                },
            },
            "required": ["mailbox"],
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
    """Run mail.applescript through the shared osascript runner."""
    return await run_osascript(
        _MAIL_SCRIPT, action, *arguments, timeout=timeout
    )


# ------------------------------------------------------------
# Section D — Output parsers (pure)
# ------------------------------------------------------------

def _parse_message(line: str) -> dict | None:
    """Parse one pipe-delimited message record into a dict.

    Expected format: message_id|subject|sender|date|is_read

    Returns None if the line does not contain exactly five fields.
    """
    parts = line.split("|", maxsplit=4)
    if len(parts) != 5:
        return None
    message_id, subject, sender, date, is_read = parts
    return {
        "id": message_id,
        "subject": subject,
        "sender": sender,
        "date": date,
        "is_read": is_read == "true",
    }


def _parse_messages(raw: str):
    """Parse a "<total>\\n<record>..." messages response.

    The get_messages and search actions both paginate in AppleScript and put the
    full matching count on the first line, followed by one record per message in
    the returned page.

    Returns a (total, messages) tuple; (0, []) for empty output.
    """
    if not raw:
        return 0, []
    lines = raw.splitlines()
    total = int(lines[0])
    messages = [
        parsed for parsed in
        (_parse_message(line) for line in lines[1:])
        if parsed is not None
    ]
    return total, messages


def _parse_mailbox_line(line: str) -> dict | None:
    """Parse one "path|count" mailbox line into a dict.

    Splits on the LAST "|" so a mailbox path containing "|" is not mangled (the
    count is always the final field). Returns None if the line does not split
    into exactly two fields.
    """
    parts = line.rsplit("|", maxsplit=1)
    if len(parts) != 2:
        return None
    path, count_text = parts
    return {"path": path, "count": int(count_text)}


def _extract_html_parts(source: str) -> list[str]:
    """Return the decoded text/html parts of a raw RFC-822/MIME message.

    Uses the stdlib email parser (no new dependency). Each text/html part is
    decoded from its transfer-encoding (quoted-printable / base64) and its
    declared charset; a missing or unknown charset falls back to UTF-8. Returns
    an empty list for a message with no HTML part (e.g. plain-text-only mail).
    """
    message = email.message_from_string(source)
    html_parts: list[str] = []
    for part in message.walk():
        if part.get_content_type() != "text/html":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            html_parts.append(payload.decode(charset, errors="replace"))
        except LookupError:
            html_parts.append(payload.decode("utf-8", errors="replace"))
    return html_parts


class _ImageTagCollector(HTMLParser):
    """Collect the attributes of every <img> tag encountered in an HTML body."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "img":
            # A later duplicate attribute wins, matching browser behaviour.
            self.images.append(
                {name.lower(): (value or "") for name, value in attrs}
            )


def _parse_dimension(value: str) -> int | None:
    """Parse an <img> width/height attribute to an int, or None if not
    numeric.

    Handles a bare number ("600") and a trailing-unit form ("600px"); a
    percentage or other non-integer value (e.g. "100%) yields None.
    """
    match = re.match(r"\s*(\d+)\s*(?:px)?\s*$", value, re.IGNORECASE)
    return int(match.group(1)) if match else None


# High-confidence substrings that mark a URL as an open-tracking pixel rather
# than real content. Kept deliberately small to avoid false positives on legit
# CDN paths (a bare "track" substring is intentionally NOT included).
_TRACKER_URL_MARKERS = ("/wf/open", "/open?",
                        "pixel", "beacon", "spacer.gif")


def _looks_like_tracker(url: str, width: int | None, height: int | None) -> bool:
    """Heuristically flag a likely tracking pixel.

    Two signals, either sufficient:
        * Tiny declared dimensions — a 1x1 (or 0-sized) image is a spacer/beacon,
          never real content.
        * A URL containing a high-confidence tracking marker.
    """
    if (width is not None and width <= 1) or (height is not None and height <= 1):
        return True
    lowered = url.lower()
    return any(marker in lowered for marker in _TRACKER_URL_MARKERS)


def _build_image(attrs: dict) -> dict | None:
    """Turn one <img>'s attributes into an image record, or None to skip it.

    Only remotely-fetchable images are surfaced: an http(s) or protocol-relative
    ("//") src. Inline "data:" URIs (bytes already present) and "cid:" part
    references (attachment bytes, out of scope for this tool) are skipped, as is
    an <img> with no src. `alt` whitespace is collapsed — the JSON output already
    escapes safely, so this just tidies free text (the Python analog of the wire
    format's sanitise_field).
    """
    src = attrs.get("src", "").strip()
    if not src:
        return None
    lowered = src.lower()
    if not (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or src.startswith("//")
    ):
        return None
    width = _parse_dimension(attrs.get("width", ""))
    height = _parse_dimension(attrs.get("height", ""))
    alt = " ".join(attrs.get("alt", "").split())
    return {
        "url": src,
        "alt": alt,
        "width": width,
        "height": height,
        "likely_tracker": _looks_like_tracker(src, width, height),
    }


def _extract_images(source: str) -> list[dict]:
    """Extract the linked images referenced by a message's HTML body.

    Parses every text/html part, collects <img> tags, and builds one record per
    remotely-fetchable image. Duplicate URLs are collapsed (keeping the first
    occurrence), so a logo repeated throughout a newsletter appears once.
    """
    images: list[dict] = []
    seen_urls: set[str] = set()
    for html in _extract_html_parts(source):
        collector = _ImageTagCollector()
        collector.feed(html)
        for attrs in collector.images:
            image = _build_image(attrs)
            if image is None or image["url"] in seen_urls:
                continue
            seen_urls.add(image["url"])
            images.append(image)
    return images


# ------------------------------------------------------------
# Section E — Per-tool handlers
# ------------------------------------------------------------


async def _handle_get_messages(arguments: dict) -> list[TextContent]:
    count = int(arguments.get("count", 50))
    offset = int(arguments.get("offset", 0))
    unread_only = bool(arguments.get("unread_only", False))
    mailbox = arguments.get("mailbox", "inbox")

    raw = await _run_script(
        "get_messages",
        str(count),
        str(offset),
        str(unread_only).lower(),
        mailbox,
    )
    total, messages = _parse_messages(raw)
    returned = len(messages)
    return json_content({
        "total": total,
        "offset": offset,
        "returned": returned,
        "has_more": offset + returned < total,
        "messages": messages,
    })


async def _handle_search(arguments: dict) -> list[TextContent]:
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

    raw = await _run_script(
        "search",
        mailbox, sender, subject, body, since, until,
        str(unread_only).lower(), str(count), str(offset),
    )
    total, messages = _parse_messages(raw)
    returned = len(messages)
    return json_content({
        "status": "ok",
        "total": total,
        "offset": offset,
        "returned": returned,
        "has_more": offset + returned < total,
        "messages": messages,
    })


async def _handle_count_messages(arguments: dict) -> list[TextContent]:
    unread_only = bool(arguments.get("unread_only", False))
    mailbox = arguments.get("mailbox", "inbox")
    raw = await _run_script("count_messages", str(unread_only).lower(), mailbox)
    # Round-trip through int to validate the script returned a number.
    return text_content(str(int(raw)))


async def _handle_list_mailboxes(arguments: dict) -> list[TextContent]:
    del arguments  # no arguments; the parameter exists for a uniform dispatch signature
    raw = await _run_script("list_mailboxes")
    mailboxes = [
        parsed for parsed in
        (_parse_mailbox_line(line) for line in raw.splitlines())
        if parsed is not None
    ]
    return json_content(mailboxes)


async def _handle_get_body(arguments: dict) -> list[TextContent]:
    message_id = arguments["message_id"]
    body = await _run_script("get_body", message_id)
    return text_content(body)


async def _handle_get_images(arguments: dict) -> list[TextContent]:
    message_id = arguments["message_id"]
    source = await _run_script("get_source", message_id)
    images = _extract_images(source)
    return json_content({
        "status": "ok",
        "message_id": message_id,
        "total": len(images),
        "tracker_count": sum(1 for image in images if image["likely_tracker"]),
        "images": images,
    })


async def _handle_move(arguments: dict) -> list[TextContent]:
    message_id = arguments["message_id"]
    mailbox = arguments["mailbox"]
    await _run_script("move", message_id, mailbox)
    return text_content(f"Moved {message_id!r} to '{mailbox}'.")


async def _handle_delete(arguments: dict) -> list[TextContent]:
    message_id = arguments["message_id"]
    await _run_script("delete", message_id)
    return text_content(f"Deleted {message_id!r}.")


async def _handle_rename_mailbox(arguments: dict) -> list[TextContent]:
    mailbox = arguments["mailbox"]
    new_name = arguments["new_name"]
    if "/" in new_name:
        raise ValueError(
            "new_name must be a plain mailbox name, not a path (no '/')."
        )
    new_path = await _run_script("rename_mailbox", mailbox, new_name)
    return text_content(f"Renamed '{mailbox}' to '{new_path}'.")


async def _handle_create_mailbox(arguments: dict) -> list[TextContent]:
    mailbox = arguments["mailbox"]
    # The script returns "created|<path>" or "exists|<path>".
    raw = await _run_script("create_mailbox", mailbox)
    state, _, path = raw.partition("|")
    return json_content({
        "status": "ok",
        "path": path,
        "created": state == "created",
    })


_TOOL_HANDLERS = {
    "mail_get_messages": _handle_get_messages,
    "mail_search": _handle_search,
    "mail_count_messages": _handle_count_messages,
    "mail_list_mailboxes": _handle_list_mailboxes,
    "mail_get_body": _handle_get_body,
    "mail_get_images": _handle_get_images,
    "mail_move": _handle_move,
    "mail_delete": _handle_delete,
    "mail_rename_mailbox": _handle_rename_mailbox,
    "mail_create_mailbox": _handle_create_mailbox,
}


# ------------------------------------------------------------
# Section F — Public interface
# ------------------------------------------------------------

async def list_tools() -> list[Tool]:
    """Return the Tool definitions for the mail domain."""
    return TOOLS


async def handle(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch a mail tool call to its handler.

    Args:
        name:      The tool name, e.g. "mail_get_messages".
        arguments: Dict of validated arguments from the client.

    Returns:
        A single-element list containing a TextContent with the result.

    Raises:
        ValueError:   If the tool name is not recognised, or a tool's own
                      argument validation fails.
        RuntimeError: If the underlying AppleScript call fails.
    """
    try:
        tool_handler = _TOOL_HANDLERS[name]
    except KeyError:
        raise ValueError(f"Unknown mail tool: '{name}'.")
    return await tool_handler(arguments)
