# macos-apps-mcp v0.1.0

First public release. A local [MCP](https://modelcontextprotocol.io) server that
gives an MCP client (Claude Desktop, Cursor, Zed, VS Code, …) read-and-organize
access to Apple's built-in productivity apps on **macOS** — **Mail**,
**Reminders**, and **Notes** — by scripting them locally through AppleScript
(`osascript`). Nothing leaves your machine.

> ⚠️ **Early release (`0.x`).** Not feature-complete. The set of apps and tool
> arguments may change between minor versions. Not affiliated with or endorsed by
> Apple.

## What's included — 23 tools

- **Mail (9):** `mail_get_messages` · `mail_search` · `mail_count_messages` ·
  `mail_list_mailboxes` · `mail_get_body` · `mail_move` · `mail_delete` ·
  `mail_rename_mailbox` · `mail_create_mailbox`
- **Reminders (7):** `reminder_list_lists` · `reminder_get` · `reminder_search` ·
  `reminder_create` · `reminder_complete` · `reminder_update` · `reminder_delete`
- **Notes (7):** `notes_list_folders` · `notes_get` · `notes_search` ·
  `notes_create` · `notes_delete` · `notes_update` · `notes_append`

Consistent search envelope (`{status, total, offset, returned, has_more, …}`)
with `count`/`offset` pagination across all three `*_search` tools; ISO 8601
dates in and out. `reminder_search` is served from a background-built index
(instant title search; opt-in full-text scan) to avoid wedging EventKit on large
stores.

## Requirements

- macOS · Python 3.10+ · the Apple apps you want to use, signed in
- Grant the Automation permission prompts on first use (System Settings →
  Privacy & Security → Automation)

## Getting started

See the [README](https://github.com/derekrussell/macos-apps-mcp#readme) for
install and Claude Desktop configuration.

## Known limitations (AppleScript / the apps, not this server)

- Mailboxes can't be deleted or moved via AppleScript — no `mail_delete_mailbox`
  (delete manually in Mail.app; `mail_rename_mailbox` can flag one for removal)
- A reminder's due date can't be cleared once set
- Notes has no separate title field — the title is the first line of the body

## Scope & roadmap

In scope: local, user-authorized access to Apple's built-in personal-information
apps, **AppleScript only**. Planned next: Calendar, Contacts, Messages (tracked
in [Issues](https://github.com/derekrussell/macos-apps-mcp/issues)).

**Full changelog:** initial public release.
