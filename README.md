# macos-apps-mcp

[![CI](https://github.com/derekrussell/macos-apps-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/derekrussell/macos-apps-mcp/actions/workflows/ci.yml)
[![v0.2.0 milestone](https://img.shields.io/github/milestones/progress-percent/derekrussell/macos-apps-mcp/1?label=v0.2.0%20milestone)](https://github.com/derekrussell/macos-apps-mcp/milestone/1)
[![v0.3.0 milestone](https://img.shields.io/github/milestones/progress-percent/derekrussell/macos-apps-mcp/2?label=v0.3.0%20milestone)](https://github.com/derekrussell/macos-apps-mcp/milestone/2)

A local [MCP](https://modelcontextprotocol.io) server that gives an MCP client
(Claude Desktop, Cursor, Zed, VS Code, and others) read-and-organize access to
Apple's built-in productivity apps on **macOS**: **Mail**, **Reminders**, and
**Notes**.

Every operation is performed on your own machine by scripting the apps through
**AppleScript** (`osascript`). Nothing is sent to a third party, and the server
only reaches the apps you already have open on your Mac.

> **Not affiliated with or endorsed by Apple.** "Apple," "Mail," "Reminders,"
> and "Notes" are trademarks of Apple Inc. This is an independent project.

## Status

Early and actively developed — versioned **`0.x`** (see
[Releases](../../releases)). The set of apps and the tool arguments may still
change between minor versions. Not yet feature-complete; see
[Scope](#scope) and the [issue tracker](../../issues) for what's planned.

## Scope

**In scope:** local, user-authorized access to Apple's built-in
personal-information apps on macOS, performed **exclusively through
AppleScript / `osascript`**.

- **Today:** Mail, Reminders, Notes.
- **Being considered:** Calendar, Contacts, Messages, and composing/sending
  mail — tracked as [issues](../../issues). These are candidates, not
  commitments: the roadmap is driven by real everyday use of the server, so
  what gets built next (and its exact shape) follows what that use shows is
  actually needed.

**Out of scope:** non-Apple apps, creative apps (Photos editing, Music),
system administration, and anything that can't be driven through AppleScript.
AppleScript is both the design choice and the hard boundary — it's why some
operations are possible and others simply aren't (see
[Known limitations](#known-limitations)).

## Tools (23)

| Domain | Tools |
| --- | --- |
| **Mail** (9) | `mail_get_messages` · `mail_search` · `mail_count_messages` · `mail_list_mailboxes` · `mail_get_body` · `mail_move` · `mail_delete` · `mail_rename_mailbox` · `mail_create_mailbox` |
| **Reminders** (7) | `reminder_list_lists` · `reminder_get` · `reminder_search` · `reminder_create` · `reminder_complete` · `reminder_update` · `reminder_delete` |
| **Notes** (7) | `notes_list_folders` · `notes_get` · `notes_search` · `notes_create` · `notes_delete` · `notes_update` · `notes_append` |

All three `*_search` tools share one response envelope
(`{status, total, offset, returned, has_more, <items>}`) with `count`/`offset`
pagination. Dates are ISO 8601 (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`) on the
way in and out.

## Requirements

- **macOS** (the tools script native Apple apps; the server won't do anything
  useful on other platforms).
- **Python 3.10+**.
- The Apple apps you want to use (Mail / Reminders / Notes) set up and signed in.

## Install

```sh
git clone https://github.com/derekrussell/macos-apps-mcp.git
cd macos-apps-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run it standalone (stdio) to confirm it starts:

```sh
python server.py
```

Normally you don't run it by hand — your MCP client launches it (see below).

## Connect an MCP client

The server speaks MCP over **stdio**. Point your client at `server.py` with the
Python from your virtualenv. For **Claude Desktop**, add this to
`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "macos-apps": {
      "command": "/absolute/path/to/macos-apps-mcp/.venv/bin/python",
      "args": ["/absolute/path/to/macos-apps-mcp/server.py"]
    }
  }
}
```

Use absolute paths for both the interpreter and the script. Restart the client
after editing the config.

## Permissions

The first time a tool scripts an app, macOS prompts to allow the controlling
process (your MCP client / terminal) to control Mail, Reminders, or Notes.
**Approve each prompt**, or the calls fail. You can review and change these
later under **System Settings → Privacy & Security → Automation**. Reminders
also relies on EventKit access under the hood.

## Usage

Once the server is connected, you don't call the tools directly — you ask your
MCP client (e.g. Claude) in plain language, and it picks the right tool. It works
entirely on your Mac against the apps you're already signed in to; nothing is
sent anywhere else. Today it's focused on **reading and organizing** — it doesn't
send mail.

Example things you can ask:

**Mail**
- "Show me my unread mail from this week."
- "Search my mail for messages from Jane about the invoice."
- "What's in the body of the latest message from my bank?"
- "How many messages are in my Archive mailbox?"
- "Move that message to my 'Receipts' mailbox."

**Reminders**
- "Add a reminder to call the dentist tomorrow at 3pm."
- "What's on my Today list?"
- "Find my reminders mentioning 'passport'."
- "Mark the 'buy milk' reminder as done."

**Notes**
- "Create a note titled 'Trip packing list' with a few starter items."
- "Find my notes that mention the Q3 budget."
- "Append 'remember chargers' to my packing list note."
- "What folders do I have in Notes?"

You can also combine them — for example, "Find the reminder about the dentist and
add its date to a new note" — and the client will chain the relevant tools.

## Known limitations

These come from AppleScript / the apps themselves, not from this server:

- **Mailboxes can't be deleted or moved via AppleScript** — the app returns a
  generic error for every account type. There is deliberately no
  `mail_delete_mailbox`; delete a mailbox manually in Mail.app. Renaming and
  creating mailboxes *do* work, so `mail_rename_mailbox` can be used to flag a
  mailbox (e.g. prefix `"DELETE ME - "`) for manual removal.
- **A reminder's due date can't be cleared** once set (AppleScript limitation);
  it can only be changed to another date.
- **Notes has no separate title field** — a note's title is the first line of
  its body, which the create/update tools compose for you.
- **`reminder_search` is served from a background-built index**, not a live
  scan: repeated full scans of a large Reminders store wedge EventKit. Title
  search is instant; full-text (`notes`) search is an opt-in live scan that can
  be slow. A cold start briefly returns `status: "warming"` — retry shortly.

## Development

```sh
pip install -r requirements-dev.txt   # test deps
pytest                                 # fast unit tests, no osascript spawned
python3 -m py_compile tools/*.py server.py   # syntax check
```

The unit tests cover the pure Python logic in isolation — parsers, pagination,
prefix routing, the reminder index, and each tool handler against a faked
script runner — so they run fast and never touch real Apple apps. End-to-end
validation against the live apps is the manual sequence in
[`SMOKE_TEST.md`](SMOKE_TEST.md). Architecture notes live in
[`CLAUDE.md`](CLAUDE.md).

Bug reports and feature requests (including new apps) are welcome in the
[issue tracker](../../issues). If you'd like to contribute code, please read the
[contributing guide](CONTRIBUTING.md) first — it covers setup, conventions, the
two-tier test model, and the CI-gated PR workflow.

## License

See [`LICENSE`](LICENSE).
