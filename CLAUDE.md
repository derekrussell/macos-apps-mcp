# macos-apps-mcp

A local macOS **MCP server** that exposes Apple **Mail**, **Reminders**, and **Notes**
to an MCP client (e.g. Claude Desktop) over stdio. Every operation is performed by
invoking **AppleScript** through `osascript`. macOS-only; the server itself is
platform-agnostic.

## Architecture

```
server.py                  MCP entry point (stdio). Registers tools, dispatches by
                           name prefix, warms the reminder index at startup.
tools/<domain>.py          Per-domain Python: Tool definitions + one handler per tool
                           behind a _TOOL_HANDLERS dispatch table, plus pure line
                           parsers separated from the osascript call.
tools/_osascript.py        Shared osascript boundary: run_osascript() (spawn + timeout
                           kill/reap) and pure helpers (normalize_line_endings,
                           clean_osascript_error, interpret_osascript_result).
tools/_responses.py        Shared MCP-response helpers: json_content, text_content,
                           paginate. Used by every domain module.
scripts/<domain>.applescript   Per-domain AppleScript, dispatched on an action keyword
                           passed as the first argv item.
scripts/utilities.applescript  Shared AppleScript handlers, loaded once per invocation:
                           sanitise_field, format_date, parse_iso_date.
tests/                     pytest suite covering the pure Python logic in isolation
                           (parsers, pagination, routing, the reminder index, and the
                           handlers via a faked _run_script). Run: pytest.
config.py                  (empty)
```

- **Dispatch:** `server.py` routes by prefix — `mail_` → `tools/mail.py`, `notes_` →
  `tools/notes.py`, `reminder_` → `tools/reminders.py`.
- **osascript boundary:** the one runner is `run_osascript(script_path, action, *args,
  timeout=60)` in `tools/_osascript.py`; each domain module keeps a thin `_run_script`
  wrapper that binds its own script path. The runner normalises CR/CRLF (so a stray CR
  can't shift pipe fields) and, on timeout, kills **and reaps** the child (an osascript
  blocked in an Apple event ignores SIGKILL until the event returns, so the wait is
  bounded). On a non-zero exit it raises the *unwrapped* error via `clean_osascript_error`
  — stripping the script path, char offsets, and numeric code osascript prepends — with a
  raw-text fallback. The spawn/timeout/decode logic is split into small pure functions so
  it is unit-testable without a subprocess.
- **Shared handlers:** each script calls `load_utilities()` to load
  `utilities.applescript` by a path resolved relative to `path to me`, then calls e.g.
  `util's format_date(...)`. Inside a `tell application` block these need
  `my (util's ...)`.

## Tools (24)

**Mail (10):** `mail_get_messages` · `mail_search` · `mail_count_messages` ·
`mail_list_mailboxes` · `mail_get_body` · `mail_get_images` · `mail_move` ·
`mail_delete` · `mail_rename_mailbox` · `mail_create_mailbox`

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
  mailbox for manual deletion (e.g. prefix "DELETE ME - "). It returns the new
  account-qualified path — built from the input path's parent + the new leaf,
  because after `set name` the resolved mailbox is a stale by-name specifier that
  can no longer be re-read (reading it throws `-1728`).
- **Mailbox creation (`mail_create_mailbox`) targets the account with a
  slash-in-name, one level at a time.** The only form that works is `make new
  mailbox at end of mailboxes of account <acct> with properties
  {name:"<within-account path>"}`. Pitfalls found: a bare `make new mailbox
  {name:"iCloud/X"}` creates a *local* mailbox (plus a stray local "iCloud"
  parent), and nesting via `at end of mailboxes of <parent mailbox>` fails
  `-10000`. Crucially, passing a full nested path in ONE `make` auto-creates any
  missing intermediate as an IMAP `\NoSelect` container (holds children but not
  messages; not in `mail_list_mailboxes`; not a valid move destination or
  resolvable path). So the handler creates each level **explicitly, top-down**,
  skipping existing ones — every intermediate ends up a real selectable mailbox.
  Creation is idempotent (existing path → `created:false`). Beware: created test
  mailboxes can't be deleted programmatically — rename them "DELETE ME - " and
  delete in Mail.app.

## Run & verify

- **Dependency:** `mcp>=1.27.1,<2.0` (`requirements.txt`). The cap is deliberate:
  mcp 2.0 is a breaking major that removed the low-level `Server.list_tools` /
  `call_tool` decorator API this server is built on. Migrating to 2.x is tracked
  separately. Run: `python server.py` (stdio;
  normally launched by the MCP client, e.g. Claude Desktop).
- **Python syntax check:** `python3 -m py_compile tools/*.py server.py`.
- **Unit tests:** `pytest` (install dev deps with `pip install -r requirements-dev.txt`;
  config in `pyproject.toml`). Covers the pure Python logic in isolation — parsers,
  pagination, prefix routing, the reminder search index, and each tool handler with a
  faked `_run_script` — so it runs fast and spawns no osascript. These do NOT exercise
  real AppleScript/Apple apps.
- **AppleScript parse check:** `osascript scripts/<domain>.applescript` with no args —
  a fast `Can't get item 1 of {}` (-1728) means it compiled (that's the argv access,
  after successful parse).
- **End-to-end validation** (real Apple apps) is the manual 24-tool sequence in
  `SMOKE_TEST.md` (keep that file in sync when adding/removing a tool); run reports live
  under `~/Desktop/macos_apps_mcp/`. When testing reminder/mail search, prefer a freshly
  restarted machine and avoid rapid repeated Reminders scans.

## Git

Repo signs commits (SSH signing). If a commit hangs or fails on the passphrase, the
ssh-agent has dropped the key — reload with
`ssh-add --apple-use-keychain ~/.ssh/id_ed25519`. Commits use
`Co-Authored-By: Claude ...`.
