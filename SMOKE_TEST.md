# apple-mcp smoke test

Canonical, versioned manual test plan for all **23 tools** (Reminders 7 · Notes 7 ·
Mail 9). Run it through the MCP client (e.g. Claude Desktop) after any change to the
tools or their AppleScript. Keep this file in sync with the tool surface — when a tool
is added or removed, update the steps and the count here in the same change, so the plan
never drifts (that drift is why earlier runs needed ad-hoc `step 21'` patches).

Record the report as `apple-mcp-smoke-test-run<N>-<YYYY-MM-DD>.md`. The archive so far
lives in `~/Desktop/apple_mcp/`; if the client running the tests can't write there (that
folder isn't connected to its session), save it somewhere it can and move it afterward.

## Prerequisites

- macOS with Mail, Reminders, and Notes signed in and populated.
- The client (Claude Desktop) has been granted **Automation** permission for all three
  apps, and **Full Disk Access** if needed for Mail.
- For reliable Reminders search timing, prefer a **freshly restarted** machine and avoid
  hammering Reminders beforehand (repeated full scans degrade EventKit — see CLAUDE.md).

## Conventions

- Tag test data with a unique run marker: **`MCP-SMOKE-<YYYY-MM-DD>-R<N>`** so created
  items are identifiable and greppable.
- Every created item is cleaned up by the plan except a created mailbox, which cannot be
  deleted via AppleScript — step 23 renames it to `DELETE ME - …` for manual removal.
- Pick a **disposable** inbox message for the move/delete chain (a newsletter or promo),
  never anything important; it is moved then trashed (recoverable from Trash).

## Expected shapes (verify throughout)

- **Search tools** (`reminder_search`, `notes_search`, `mail_search`) return a JSON
  object `{status, total, offset, returned, has_more, <items>}` and paginate via
  `count`/`offset`.
- **Get tools** (`reminder_get`, `notes_get`, `mail_get_messages`) return
  `{total, offset, returned, has_more, <items>}`.
- **Dates** are ISO 8601 (`YYYY-MM-DDTHH:MM:SS`) everywhere.
- **No field corruption**: pipe-delimited fields are sanitised — no stray `|`, no
  shifted columns, no non-ISO dates.

---

## Reminders — steps 1–7

| # | Tool | Action | Pass criteria |
|---|---|---|---|
| 1 | `reminder_list_lists` | List all lists. | Each list has a name and integer count. Note the count of a chosen target list. |
| 2 | `reminder_create` | Create `MCP-SMOKE-<tag>` in a known list with a due date and notes. | Returns a reminder id; no duplicate created. |
| 3 | `reminder_get` | Fetch that list with `include_completed: true`. | The scratch reminder appears with correct title, ISO `due_date`, `notes`, `is_completed:false`, and the resolved `list` name (not `"default"`). |
| 4 | `reminder_search` | Search `MCP-SMOKE` (title). Then a broad term with a small `count`. Then the same term with `search_notes:true`. | Envelope shape correct; the scratch is found. Broad term paginates (`has_more:true`, `offset` advances). A cold first call may return `status:"warming"` with an empty array — retry after a few seconds. Default search returns `notes:null`; `search_notes:true` populates notes (may be slower). |
| 5 | `reminder_update` | Update the scratch's title and notes. | Returns promptly (no timeout); a follow-up `reminder_get` shows both applied. |
| 6 | `reminder_complete` | Complete the scratch. | Marked completed; visible only with `include_completed:true`. |
| 7 | `reminder_delete` | Delete the scratch. | Gone. A `reminder_list_lists` now matches the step-1 count for that list (see count note below). |

**Count note (not a bug):** `reminder_list_lists` and `reminder_get(include_completed:true)`
agree only when sampled at the same instant. Compare counts *before* create or *after*
delete — comparing step 1 (before create) against a mid-run `get` shows a spurious `+1`
that is just the scratch reminder.

---

## Notes — steps 8–14

| # | Tool | Action | Pass criteria |
|---|---|---|---|
| 8 | `notes_list_folders` | List folders. | Each has a name and count. ("Recently Deleted" only appears when non-empty — its absence is not a regression.) |
| 9 | `notes_create` | Create `MCP-SMOKE-<tag>` note with a title and body. | Returns a note id; title is the note's first line. |
| 10 | `notes_get` | Fetch the folder. | The scratch note appears with id, title, folder, ISO `modified_date`. |
| 11 | `notes_search` | Search `MCP-SMOKE`. Then a broad term with small `count`. | Envelope shape correct; scratch found; broad term paginates. |
| 12 | `notes_update` | Update the title and/or body. | Change applied; the title (first line) is preserved/updated as intended. |
| 13 | `notes_append` | Append a line. | Existing content preserved; the appended text is present. |
| 14 | `notes_delete` | Delete the scratch note. | Soft delete: it moves to "Recently Deleted" (which now appears), recoverable. |

---

## Mail — steps 15–23

| # | Tool | Action | Pass criteria |
|---|---|---|---|
| 15 | `mail_list_mailboxes` | List all mailboxes. | Each row is `path` (account-qualified, e.g. `iCloud/Church/Transactions`) + `count`; each mailbox appears once. |
| 16 | `mail_count_messages` | Count the inbox; then unread-only. | Two plausible integers; unread ≤ total. |
| 17 | `mail_get_messages` | Page the inbox (`count`/`offset`). Choose a **disposable** target message. | Envelope + ISO dates + `is_read`. Ordering follows Mail's native (received) order, **not** strictly the reported sent date — don't rely on "top = newest sent". |
| 18 | `mail_search` | Find the disposable message by `sender` (and/or `subject`). Test `count`/`offset` on a broad match, a `since`/`until` window, and `unread_only`-alone rejection. | Envelope shape; AND semantics across criteria; pagination; `unread_only` with no other criterion is rejected with a clear message. Body search (`body:`) works but is slower. |
| 19 | `mail_get_body` | Fetch the full body of a message. | Clean plain text. **Non-mutating** — read state unchanged afterward. |
| 20 | `mail_create_mailbox` | Create `iCloud/MCP Test Run <N>` (top-level). Then a nested child `…/Sub`. Then a path with a **genuinely missing intermediate**, e.g. `…/Deep/Deeper` where `Deep` does not yet exist, to exercise auto-parent creation. Then an idempotent repeat, and error paths (unknown account; non-account-qualified name). | Returns `{status, path, created}`; `created:true` first time, `false` on repeat. The missing intermediate `Deep` is created as a normal **selectable** mailbox — confirm both `mail_count_messages(iCloud/MCP Test Run <N>/Deep)` (returns 0, not "not found") and its presence in `mail_list_mailboxes`. Bad account and non-account-qualified inputs raise clear, unwrapped errors (no script path/offsets leaked). |
| 21 | `mail_move` | Move the disposable target message **into `iCloud/MCP Test Run <N>`** (created in step 20). | Counts change correctly: destination 0 → 1, inbox −1. |
| 22 | `mail_delete` | Delete (Trash) the disposable target. | Destination back to 0; message located in Trash (e.g. via `mail_search`), recoverable. |
| 23 | `mail_rename_mailbox` | Rename `iCloud/MCP Test Run <N>` → `DELETE ME - MCP Test Run <N>`. Also test a `new_name` containing `/` (rejected) and a non-existent source (rejected). | Returns the new account-qualified path; the whole subtree re-parents and is still addressable at the new path. `/` in `new_name` and a missing source are both rejected with clear messages. |

---

## Known limitations (do NOT report as defects)

- **Mailbox deletion is impossible via AppleScript** — `delete`/`move` of a *mailbox*
  fail `-10000` for every type (local, POP, iCloud/IMAP), a long-standing Mail
  limitation. There is no `mail_delete_mailbox`; deletion is manual in Mail.app. This is
  why step 23 flags the created mailbox rather than deleting it.
- **`reminder_search` cold start** may return `status:"warming"` with an empty array —
  retry shortly (the index builds in the background).
- **`reminder_search` default is title-only** and returns `notes:null`; `search_notes`
  and **`mail_search` `body`** are live scans that are slower and can time out on large
  accounts.
- **`mail_get_messages` order** is Mail's native received-date order, not strictly the
  reported sent date; use `mail_search` with `since`/`until` for date-precise selection.
- **Notes "Recently Deleted"** only appears when it contains notes.

---

## Results template

```
# apple-mcp smoke test — run <N>
Tested: <YYYY-MM-DD>, tag MCP-SMOKE-<YYYY-MM-DD>-R<N>
Result: <P> PASS / <F> FAIL

| # | Tool | Result | Notes |
|---|---|---|---|
| 1 | reminder_list_lists | | |
| 2 | reminder_create | | |
| 3 | reminder_get | | |
| 4 | reminder_search | | |
| 5 | reminder_update | | |
| 6 | reminder_complete | | |
| 7 | reminder_delete | | |
| 8 | notes_list_folders | | |
| 9 | notes_create | | |
| 10 | notes_get | | |
| 11 | notes_search | | |
| 12 | notes_update | | |
| 13 | notes_append | | |
| 14 | notes_delete | | |
| 15 | mail_list_mailboxes | | |
| 16 | mail_count_messages | | |
| 17 | mail_get_messages | | |
| 18 | mail_search | | |
| 19 | mail_get_body | | |
| 20 | mail_move | | |
| 21 | mail_delete | | |
| 22 | mail_create_mailbox | | |
| 23 | mail_rename_mailbox | | |
```

## Post-run cleanup checklist

- [ ] Scratch reminder deleted (step 7).
- [ ] Scratch note in Recently Deleted (step 14) — purge if desired.
- [ ] Disposable message recoverable in Trash (step 22) — restore if it mattered.
- [ ] **Manually delete** `iCloud/DELETE ME - MCP Test Run <N>` in Mail.app
      (right-click → Delete Mailbox) — cannot be removed programmatically.
