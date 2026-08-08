# Contributing to macos-apps-mcp

Thanks for your interest in contributing! This is a local macOS MCP server that
exposes Apple's built-in productivity apps (Mail, Reminders, Notes) to MCP
clients by scripting them through AppleScript. This guide covers how to set up,
make a change, and get it merged.

By contributing, you agree that your contributions are licensed under the
project's [MIT License](LICENSE).

## Before you start

- **Check the [scope](README.md#scope) first.** The project is deliberately
  narrow: *local, user-authorized access to Apple's built-in
  personal-information apps, performed exclusively through AppleScript.* Non-Apple
  apps, creative apps, system administration, and anything that can't go through
  AppleScript are out of scope.
- **Open or find an issue before large work.** For a new app, a new tool, or
  anything non-trivial, comment on the relevant [issue](../../issues) (or open
  one) so we can agree on the approach before you invest time. Small fixes can go
  straight to a PR.
- **Read [`CLAUDE.md`](CLAUDE.md).** It's the architecture reference — module
  layout, the osascript boundary, the wire format, and the hard-won platform
  gotchas (AppleScript quirks, the EventKit wedge, mailbox limitations). It will
  save you from rediscovering problems the hard way.

## Prerequisites

- **macOS** (the tools script native Apple apps).
- **Python 3.10+**.
- The Apple apps you're working on (Mail / Reminders / Notes), signed in.

## Set up your environment

```sh
git clone https://github.com/derekrussell/macos-apps-mcp.git
cd macos-apps-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime deps + pytest
```

## Make your change

1. **Branch** off `main` using the naming convention below.
2. Make the change, following the conventions below.
3. **Add or update tests** for any Python logic you touch (see Testing).
4. Keep commits focused and write clear commit messages.

### Branch naming

Name branches `<type>/<short-kebab-description>`, optionally prefixing the
description with the issue number when one exists
(`<type>/<issue#>-<short-kebab-description>`):

| Type | Use | Example |
| --- | --- | --- |
| `feat/` | new tool or capability | `feat/12-calendar-get-events` |
| `new-app/` | a whole new app module | `new-app/1-calendar` |
| `fix/` | bug fix | `fix/reminder-search-timeout` |
| `docs/` | docs / templates only | `docs/contributing-tweak` |
| `chore/` | dependencies, CI, tooling | `chore/6-migrate-mcp-2x` |

The `type/` prefix mirrors the issue labels, and the optional issue number links
the branch to its issue. This is a guideline, not an enforced rule — no push is
blocked for not following it.

### Code conventions

The guiding principle across the codebase is **clean, self-documenting code over
compact or clever code** — optimise for a novice reader.

- **Readability first.** Favour clear, fully spelled-out names; avoid
  abbreviations.
- **Single responsibility.** Keep functions and handlers narrowly scoped.
- **Match the surrounding code.** Each domain module (`tools/<domain>.py`)
  follows the same shape: Tool definitions, one handler per tool behind a
  `_TOOL_HANDLERS` dispatch table, and pure line parsers kept separate from the
  osascript call. Keep the osascript boundary in `tools/_osascript.py` and shared
  response helpers in `tools/_responses.py`.
- **Wire format.** AppleScript returns one pipe-delimited record per line; all
  free-text fields must pass through `sanitise_field` so a stray `|`/newline
  can't break a record.
- **Dates** are ISO 8601 everywhere, via the shared `format_date` /
  `parse_iso_date` helpers. Never use AppleScript's locale-dependent
  `date "..."` coercion.
- **Search tools** all share one envelope
  (`{status, total, offset, returned, has_more, <items>}`) with `count`/`offset`
  pagination — keep them consistent.

AppleScript files follow the same readability and single-responsibility goals
(they have no automated tests). Please **do not** re-introduce any of the
platform gotchas documented in `CLAUDE.md`.

### Adding a new tool or app

- A new tool in an existing domain: add its `Tool` definition, a `_handle_*`
  function, and an entry in that module's `_TOOL_HANDLERS`, plus the AppleScript
  action in `scripts/<domain>.applescript`.
- A new app (e.g. Calendar): add `tools/<domain>.py` and
  `scripts/<domain>.applescript`, and register the `<prefix>_` → module mapping
  in `server.py`.
- Update the tool count and lists in both [`CLAUDE.md`](CLAUDE.md) and
  [`SMOKE_TEST.md`](SMOKE_TEST.md) in the same change, so they never drift.

## Testing

The suite has two tiers:

- **Unit tests (required, run in CI).** Fast, pure-Python tests that fake the
  osascript boundary — no subprocess is spawned and no Apple app is touched.
  Run them locally before pushing:

  ```sh
  pytest
  python3 -m py_compile tools/*.py server.py   # byte-compile check
  ```

  New or changed Python logic (parsers, pagination, routing, handlers, the
  reminder index) should come with tests. See the existing `tests/` for the
  patterns (handlers are tested against a faked `_run_script`).

- **End-to-end validation (manual, cannot run in CI).** The real-app check is
  the 23-tool sequence in [`SMOKE_TEST.md`](SMOKE_TEST.md), run through an MCP
  client against your own Mail/Reminders/Notes. CI can't do this — it has no
  macOS GUI or Apple apps — so if your change affects the AppleScript or the
  live behaviour, please run the relevant smoke-test steps and mention the
  results in your PR.

  > ⚠️ When testing reminder search, avoid rapid repeated Reminders scans — they
  > can wedge EventKit for a while (see `CLAUDE.md`).

## Open a pull request

1. Push your branch and open a PR against `main`.
2. **CI must pass.** `main` is protected: every PR runs the unit suite on Python
   3.10, 3.11, and 3.12, and all three checks must be green before it can be
   merged. Keep your branch up to date with `main` (the branch must be current
   before merging).
3. In the PR description, explain **what** and **why**, link the related issue,
   and note any manual smoke-testing you did.

A maintainer will review and merge. Thanks for contributing!

## Reporting bugs and requesting features

Use the [issue tracker](../../issues). Helpful labels:

- `enhancement` — a new feature or improvement
- `new-app` — support for an additional macOS app
- `applescript-limitation` — behaviour constrained or blocked by AppleScript or
  the app itself

For bugs, include your macOS version, the tool and arguments you called, what you
expected, and what happened.
