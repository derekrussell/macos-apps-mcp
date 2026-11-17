# macOS Apps MCP — Build Roadmap

Companion to `architecture-decisions.md`. That doc is the *why*; this is the
*order*. Each milestone is a small, themed deliverable: it is broken into
GitHub issues just before it starts, each finishable on its own and usually
one pull request. Which release ships a milestone is decided when it is done.

*Created at the 2026-11-16 check-in.* Before then, work was tracked in
version-named GitHub milestones (v0.2.0, v0.3.0). See
`docs/check-ins/2026-11-16-after-v0.1.0.md`.

## So far

**v0.1.0** — released 7 August 2026. 23 tools across Mail, Reminders and
Notes.

**Since v0.1.0** (unreleased; ships with M1 as v0.2.0): `mail_get_images`
(#11), `reminder_completed_stats` (#24), and Reminders reliability fixes
(#13, #17, #20, #26, and the first half of #21).

## Milestones

**M1 — Tool presets**
The server limits which tools a client session gets, chosen by one setting,
so an AI session that reads email can't be steered by a malicious email into
deleting, trashing or restructuring anything. Five presets (`read-only`,
`triage`, `structure`, `notes-reminders`, `full`), a destination allow-list
for `mail_move` in `triage`, a `server_status` tool, and tool annotations.
See "Tool presets" in the architecture doc.
*Done when:* with each preset set, the client sees only that preset's tools
and any other tool is refused if called; `triage` moves only into listed
branches; a missing preset gives `read-only` and a misspelt one stops the
server with a clear message; `server_status` reports the active preset; unit
tests and the new preset smoke checks pass. Ends with the v0.2.0 release.
*Status:* Next

**M2 — Reminders: open-only index**
The background search index holds open reminders only. Completed history
(`include_completed` searches, `reminder_completed_stats`) becomes opt-in and
is read on demand within a time budget. Absorbs #18 and the second half of #21.
*Done when (provisional):* the index reads details only for open reminders;
completed history is read on demand within a time budget; a smoke run with a
reminder edit straight after searches passes.
*Decide at the start:* whether open reminders can be read cheaply, by reading
only the completion flags in bulk and then full details for open reminders
alone — a one-off live investigation, never a loop. If not, fall back to
#22's lighter read plus fixing #18, and revise "Done when" to match. Also
judge whether the index should move out of `tools/reminders.py` into its own
module.
*Status:* Planned

**M3 — mcp 2.0 upgrade**
Move from mcp 1.x to 2.x (#6), which replaces the decorator API the server is
built on and targets the 2026-07-28 MCP specification.
*Done when:* the server runs on mcp 2.x, Claude Desktop works with it, and
all tests plus a full smoke run pass.
*Decide at the start:* whether Claude Desktop works with a 2.x server at all,
and why it starts two copies of one server entry.
*Status:* Planned

## Not on a milestone

Tracked on GitHub, not listed here, so the two can't drift apart:

- **Questions:** issues labelled `question`, each saying when it will be
  decided.
- **Candidates:** ideas driven by use, labelled `candidate`, with no milestone
  until real use shows the need.
- **Housekeeping:** small chores, labelled `housekeeping`.

## Out of scope

- Anything outside Apple's built-in apps, or that AppleScript can't drive
  (see the README's Scope section). This is why the Filesystem module (#5)
  was closed.
- Sending mail. Drafts are a candidate (#4); sending is not.
- Deleting mailboxes: Mail can't do it by script (see `CLAUDE.md`).
