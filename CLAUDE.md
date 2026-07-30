# apple-mcp

A local macOS **MCP server** that exposes Apple **Mail**, **Reminders**, and **Notes**
to an MCP client (e.g. Claude Desktop) over stdio. Every operation is performed by
invoking **AppleScript** through `osascript`. macOS-only; the server itself is
platform-agnostic.

## Architecture

```
server.py                  MCP entry point (stdio). Registers tools, dispatches by
                           name prefix, warms the reminder index at startup.
tools/<domain>.py          Per-domain Python: Tool definitions + handlers. Each wraps
                           osascript via _run_script() and parses pipe-delimited output.
scripts/<domain>.applescript   Per-domain AppleScript, dispatched on an action keyword
                           passed as the first argv item.
scripts/utilities.applescript  Shared AppleScript handlers, loaded once per invocation:
                           sanitise_field, format_date, parse_iso_date.
tools/filesystem.py        Stub (0 tools). The file_* namespace is reserved, unused.
config.py                  (empty)
```

- **Dispatch:** `server.py` routes by prefix — `mail_` → `tools/mail.py`, `notes_` →
  `tools/notes.py`, `reminder_` → `tools/reminders.py`, `file_` → stub.
- **osascript boundary:** `_run_script(action, *args, timeout=60)` runs
  `osascript scripts/<domain>.applescript <action> <args...>`. It normalises CR/CRLF
  (so a stray CR can't shift pipe fields) and, on timeout, kills **and reaps** the
  child (an osascript blocked in an Apple event ignores SIGKILL until the event
  returns, so the wait is bounded).
- **Shared handlers:** each script calls `load_utilities()` to load
  `utilities.applescript` by a path resolved relative to `path to me`, then calls e.g.
  `util's format_date(...)`. Inside a `tell application` block these need
  `my (util's ...)`.

## Tools (22)

**Mail (8):** `mail_get_messages` · `mail_search` · `mail_count_messages` ·
`mail_list_mailboxes` · `mail_get_body` · `mail_move` · `mail_delete` ·
`mail_rename_mailbox`

**Reminders (7):** `reminder_list_lists` · `reminder_get` · `reminder_search` ·
`reminder_create` · `reminder_complete` · `reminder_update` · `reminder_delete`

**Notes (7):** `notes_list_folders` · `notes_get` · `notes_search` · `notes_create` ·
`notes_delete` · `notes_update` · `notes_append`

## Conventions

- **Wire format:** AppleScript returns one **pipe-delimited** record per line; paginated
  readers emit a total-count header line first. All free-text fields pass through
  `sanitise_field` (strips `|`, CR, LF) so records never break.
- **Dates:** ISO 8601 (`YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS`) everywhere, via
  `format_date`. Never coerce with `date "..."` (locale-dependent, mangles ISO
  strings) — build dates component-wise with `parse_iso_date`.
- **Search envelope:** the three search tools (`mail_search`, `reminder_search`,
  `notes_search`) all return `{status, total, offset, returned, has_more, <items>}`
  with `count`/`offset` pagination. Keep them consistent when editing.
- **Pagination:** AppleScript slices `items startIdx thru endIdx` (1-indexed;
  Python offset is 0-based).

## Reminder search: background index (important)

`reminder_search` does **not** scan on the request path. A full AppleScript scan of a
large account exceeds the client timeout, and repeated scans cumulatively **wedge
EventKit** (see gotchas). Instead:

- An in-memory index (`id|title|list|due|completed`) is (re)built by a **background**
  task (`build_index` action), warmed at startup, refreshed serve-stale-while-
  revalidate (TTL 90s) only when a search finds it stale. Searches serve instantly
  from memory and never block on a scan.
- **Mutations update the index in place** (add/edit/remove) — they must NOT trigger a
  rebuild (the scan contends with the next mutation's write and times it out).
- Default search matches **title only**; the `notes` field is returned `null`.
  `search_notes: true` is an opt-in **live** full-text scan (slow, may time out).
- Cold start returns `status: "warming"` with an empty array — retry shortly.

## Platform gotchas (don't rediscover these)

- **Reminders / EventKit wedge:** repeated full-list AppleScript scans wedge EventKit;
  payload reduction only raises the threshold. This is why the background index exists.
  Do **not** benchmark reminder search in a loop — it re-wedges the store for a long
  time. See `~/.claude/.../memory/reminders-search-eventkit-wedge.md`.
- **Mail `whose ... contains ""` matches NOTHING** (unlike plain AppleScript). Omit
  empty text predicates from the clause; `mail_search` enumerates clause shapes.
- **No bulk field read on a unified-`inbox` whose-result** — iterate per-message or use
  `items i thru j of`. See `memory/mail-applescript-whose-quirks.md`.
- **`content contains` inside a Mail `whose`** decodes every body (catastrophic) —
  filter body locally on the already-narrowed set.
- **Reminders due date cannot be cleared** via AppleScript — `reminder_update` rejects
  an explicit empty `due_date` (omit it to leave unchanged).
- **Notes has no title field** — the title is the first line of the body; `create`/
  `update` compose them together so the title isn't clobbered.
- **Mailbox deletion is not scriptable in Mail — at all.** AppleScript `delete`
  of a *mailbox* fails with a generic `-10000` for **every** type (local, POP,
  IMAP/iCloud), a long-standing limitation across macOS versions (reported since
  ~Sierra), not an IMAP round-trip issue. `move` of a mailbox fails the same way.
  Message-level `delete`/`move` work fine — it's specifically mailbox *structure*
  ops that are broken. So there is **no `mail_delete_mailbox`**; deletion must be
  done manually in Mail.app (right-click → Delete Mailbox). Mailbox *rename* and
  *create* do work, so `mail_rename_mailbox` is provided as the way to flag a
  mailbox for manual deletion (e.g. prefix "DELETE ME - ").

## Run & verify

- **Dependency:** `mcp>=1.27.1` (`requirements.txt`). Run: `python server.py` (stdio;
  normally launched by the MCP client, e.g. Claude Desktop).
- **Python syntax check:** `python3 -m py_compile tools/*.py server.py`.
- **AppleScript parse check:** `osascript scripts/<domain>.applescript` with no args —
  a fast `Can't get item 1 of {}` (-1728) means it compiled (that's the argv access,
  after successful parse).
- **No automated test suite.** Validation is a manual 22-tool smoke sequence; reports
  live under `~/Desktop/apple_mcp/`. When testing reminder/mail search, prefer a
  freshly restarted machine and avoid rapid repeated Reminders scans.

## Git

Repo signs commits (SSH signing). If a commit hangs or fails on the passphrase, the
ssh-agent has dropped the key — reload with
`ssh-add --apple-use-keychain ~/.ssh/id_ed25519`. Commits use
`Co-Authored-By: Claude ...`.
