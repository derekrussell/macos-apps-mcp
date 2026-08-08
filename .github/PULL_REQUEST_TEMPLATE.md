<!--
Thanks for contributing! Please read CONTRIBUTING.md if you haven't yet.
Keep the PR focused; open separate PRs for unrelated changes.
-->

## What and why

<!-- What does this change do, and why? -->

Closes #<!-- issue number, if applicable -->

## Type of change

- [ ] Bug fix
- [ ] New tool in an existing app
- [ ] New app (new `tools/<domain>.py` + `scripts/<domain>.applescript`)
- [ ] Docs / tooling
- [ ] Other:

## Checklist

- [ ] The change fits the project [scope](../blob/main/README.md#scope) (Apple built-in apps, AppleScript only)
- [ ] `pytest` passes locally
- [ ] `python3 -m py_compile tools/*.py server.py` passes
- [ ] Added or updated unit tests for any Python logic I changed
- [ ] Updated `CLAUDE.md` and `SMOKE_TEST.md` if I added/removed a tool (kept the counts in sync)
- [ ] Followed the code conventions in `CONTRIBUTING.md` (self-documenting names, single responsibility, `sanitise_field` on free text, ISO 8601 dates, shared search envelope)

## Manual testing

<!--
CI runs the unit tests, but it can't exercise the real Apple apps.
If this touches AppleScript or live behaviour, run the relevant SMOKE_TEST.md
steps against your own Mail/Reminders/Notes and describe the results here.
(Reminder: avoid rapid repeated Reminders scans — they can wedge EventKit.)
-->
