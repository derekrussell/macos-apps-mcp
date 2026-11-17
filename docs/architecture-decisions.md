# macOS Apps MCP — Architecture Decisions

The *why* behind the project. The order of work is in `build-roadmap.md`;
technical detail and platform gotchas are in `CLAUDE.md`. Decisions are
revised, not deleted: a changed decision records what it was, what replaced
it, and why.

## Purpose

A local MCP server that gives an MCP client (such as Claude Desktop) access
to Apple Mail, Reminders and Notes on macOS. It has two goals: everyday
personal use, and learning how MCP works.

## Settled from the outset

1. **AppleScript only, Apple's built-in apps only.** Every operation runs
   through `osascript`. That is both the design choice and the hard boundary:
   it is why some operations are possible and others simply aren't. Non-Apple
   apps, and anything AppleScript can't drive, are out of scope.
2. **Local only.** The server talks to its client over stdio (a direct pipe
   between two programs on the same Mac). Nothing leaves the machine.

## Decisions made before the first check-in

### The roadmap is driven by real use

New apps and tools are candidates, not commitments. What gets built next
follows what everyday use shows is needed. In practice, use pointed at
Reminders reliability rather than new apps. *Kept at the 2026-11-16 check-in;
how it is tracked changed (see below).*

### Discoverability deferred

Publishing to PyPI, the MCP registry and directories, and a desktop bundle
(#7–#10), are deferred until the server is proven in real use. Distribution
serves neither goal yet. The planning is preserved on the closed issues;
reopen them to resume.

### Reminder search uses a background index

A full AppleScript scan of a large Reminders account takes longer than the
client will wait, and repeated scans jam EventKit (the macOS service behind
Reminders). So searches are served from an in-memory index that is rebuilt in
the background. Edits update the index in place and never trigger a rebuild,
because a rebuild competes with the next edit and makes it time out. Details
are in `CLAUDE.md`. *Revised at the 2026-11-16 check-in: the index will hold
open reminders only (see below).*

### mcp pinned below 2.0

mcp 2.0 removed the low-level decorator API this server is built on. The
upgrade is milestone M3.

### No mailbox-delete tool

Mail cannot delete or move a mailbox by script, for any account type.
`mail_rename_mailbox` can flag a mailbox (for example "DELETE ME - …") for
deleting by hand in Mail. Details are in `CLAUDE.md`.

## Decisions made at the 2026-11-16 check-in

### Tool presets

*Was:* every tool available to every client session. This was never decided
deliberately; it is how the server started.

*Now:* the server itself limits which tools a session gets. An AI client that
reads email can be steered by hidden instructions inside an email (prompt
injection), and no model is fully immune. A rule the server enforces can't be
talked around, and doesn't depend on client settings, which can reset.

- One setting, `MACOS_APPS_PRESET`, picks one of five presets: `read-only`
  (the default), `triage`, `structure`, `notes-reminders` and `full`.
- Tools are grouped by the worst an injected email could make them do:
  read, move, add, change, delete and structure. **Change is treated like
  delete** in any preset that reads mail, because overwriting a note destroys
  its content just as deleting does.
- Switched-off tools are left out of the tool list *and* refused if called.
- `triage` allows `mail_move` only into the branches listed in
  `MACOS_APPS_MOVE_TO`, because a move can reach Trash and Junk too. An empty
  list allows no moves.
- A missing setting means `read-only`. A misspelt one stops the server, so a
  typo can't go unnoticed.
- Every preset includes `server_status`, so a client can check which preset
  is active before doing anything.
- Tool annotations (`readOnlyHint`, `destructiveHint`) are added as hints
  for clients, not as enforcement.
- Not done: sending the preset in the server's `instructions` text, because
  the 2026-07-28 MCP specification removes the handshake that carries it.

The tool lists for each preset live in `tools/_presets.py`. The check-in note
has the full detail.

### Milestones by deliverable, not by version

*Was:* GitHub milestones named after versions (v0.2.0, v0.3.0).

*Now:* numbered milestones (M1, M2, …), each a small, themed deliverable
broken into issues just before it starts. The release that ships each one is
chosen when it is done.

*Why:* the version milestones became catch-all lists that no longer described
the actual work. Small themed milestones keep progress steady and visible.

### Reminders index holds open reminders only (M2)

*Was:* every rebuild reads every reminder in full, including completed ones.

*Now:* the index holds open reminders only. Completed history becomes opt-in
and is read on demand within a time budget.

*Why:* smoke run 15 counted 1,582 completed reminders against 15 open, so
about 99% of each rebuild read history that the default views never show. That
weight is the likely cause of rebuilds colliding with edits (#18, #22).

*Consequence:* `reminder_completed_stats` will no longer be instant, because
its counts can no longer come free from the index. This reverses a design
point from #24.

### Filesystem and sending mail are out of scope

- The Filesystem module (#5) is closed. It isn't an Apple app or
  AppleScript, and file access in a server that reads mail would widen what a
  prompt-injected session could reach.
- Sending mail is out. A drafts-only `mail_create_draft` (#4) remains a
  candidate.

## Open for future sessions

Open questions are GitHub issues labelled `question`, each saying when it will
be decided.
