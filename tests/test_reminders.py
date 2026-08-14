"""Unit tests for tools/reminders.py.

Covers the pure parsers, the ReminderSearchIndex class in isolation (no globals,
no asyncio), and the async tool handlers with a faked _run_script so no real
osascript process is spawned. Pagination lives in tools/_responses.py and is
tested in test_responses.py.
"""

import asyncio
import json
import time
from datetime import datetime

import pytest

from tools import reminders
from tools.reminders import IndexedReminder, ReminderSearchIndex


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_parse_list_line_valid():
    assert reminders._parse_list_line("Reminders|21") == {
        "name": "Reminders", "count": 21
    }


def test_parse_list_line_wrong_field_count_returns_none():
    assert reminders._parse_list_line("no-delimiter") is None


def test_parse_reminder_valid_record():
    line = "id-1|Buy milk|2026-08-01T09:00:00|get oat|false|Shopping"
    assert reminders._parse_reminder(line) == {
        "id": "id-1",
        "title": "Buy milk",
        "due_date": "2026-08-01T09:00:00",
        "notes": "get oat",
        "is_completed": False,
        "list": "Shopping",
    }


def test_parse_reminder_empty_due_and_notes_become_none():
    # Fields: id | title | due_date | notes | is_completed | list
    parsed = reminders._parse_reminder("id-2|Task|||true|Reminders")
    assert parsed["due_date"] is None
    assert parsed["notes"] is None
    assert parsed["is_completed"] is True


def test_parse_reminder_wrong_field_count_returns_none():
    assert reminders._parse_reminder("too|few|fields") is None


def test_parse_index_line_valid():
    entry = reminders._parse_index_line("id-9|Take out bins|Routines|2026-08-04T17:30:00|false")
    assert entry == IndexedReminder(
        id="id-9",
        title="Take out bins",
        list_name="Routines",
        due_date="2026-08-04T17:30:00",
        is_completed=False,
    )


def test_parse_index_line_wrong_field_count_returns_none():
    assert reminders._parse_index_line("id|title|list|due") is None  # only 4 fields


# ---------------------------------------------------------------------------
# ReminderSearchIndex
# ---------------------------------------------------------------------------

def _sample_index():
    index = ReminderSearchIndex()
    index.replace([
        IndexedReminder("1", "Buy milk", "Shopping", None, False),
        IndexedReminder("2", "Buy bread", "Shopping", "2026-08-02", False),
        IndexedReminder("3", "Old milk task", "Shopping", None, True),
    ], built_at=1000.0)
    return index


def test_index_not_built_by_default():
    index = ReminderSearchIndex()
    assert index.is_built is False
    assert index.search("anything", include_completed=True) == []


def test_index_mutations_are_noops_before_build():
    index = ReminderSearchIndex()
    index.add("1", "x", "L", "", False)  # must not raise or build
    index.remove("1")
    index.edit("1", title="y")
    assert index.is_built is False


def test_index_search_is_case_insensitive_and_excludes_completed():
    index = _sample_index()
    results = index.search("MILK", include_completed=False)
    assert [r["id"] for r in results] == ["1"]  # completed "3" excluded
    assert results[0]["notes"] is None
    assert results[0]["list"] == "Shopping"


def test_index_search_can_include_completed():
    index = _sample_index()
    results = index.search("milk", include_completed=True)
    assert {r["id"] for r in results} == {"1", "3"}


def test_index_add_then_search_finds_new_reminder():
    index = _sample_index()
    index.add("4", "Buy milkshake", "Shopping", "", False)
    results = index.search("milkshake", include_completed=False)
    assert [r["id"] for r in results] == ["4"]


def test_index_remove_drops_reminder():
    index = _sample_index()
    index.remove("1")
    assert index.search("buy milk", include_completed=True) == []


def test_index_edit_changes_fields_and_skips_empty_values():
    index = _sample_index()
    index.edit("1", title="Buy almond milk", due_date="", list_name="")
    milk = index.search("almond", include_completed=False)[0]
    assert milk["title"] == "Buy almond milk"
    assert milk["due_date"] is None  # empty due_date left unchanged
    assert milk["list"] == "Shopping"  # empty list_name left unchanged


def test_index_is_stale_uses_ttl_and_injected_now():
    index = _sample_index()  # built_at=1000.0
    assert index.is_stale(90.0, now=1050.0) is False
    assert index.is_stale(90.0, now=1200.0) is True


# ---------------------------------------------------------------------------
# Rebuild-vs-mutation race guard (issue #20)
# ---------------------------------------------------------------------------

def test_mutation_before_build_does_not_stamp_mutation_time():
    # Not built yet: mutations are no-ops and must not arm the guard, or the
    # first real scan would be discarded forever.
    index = ReminderSearchIndex()
    index.remove("x")
    index.add("y", "T", "L", "", False)
    index.edit("z", title="T")
    assert index._last_mutation_at_monotonic == 0.0


def test_applied_mutation_stamps_mutation_time():
    index = _sample_index()
    index._last_mutation_at_monotonic = 0.0  # reset after the build in _sample_index
    index.remove("1")
    assert index._last_mutation_at_monotonic > 0.0


def test_replace_discards_scan_that_a_mutation_has_overtaken():
    # A delete lands in-place at t=101, after a scan that started at t=100. The
    # scan's snapshot still holds the deleted reminder; applying it would
    # resurrect a phantom, so replace() must discard it.
    index = _sample_index()
    index.remove("1")
    index._last_mutation_at_monotonic = 101.0  # mutation after scan start

    applied = index.replace(
        [
            IndexedReminder("1", "Buy milk", "Shopping", None, False),  # stale
            IndexedReminder("2", "Buy bread", "Shopping", "2026-08-02", False),
        ],
        not_mutated_since=100.0,
    )

    assert applied is False
    # The in-place removal is preserved — no phantom served.
    assert index.search("buy milk", include_completed=True) == []


def test_replace_applies_when_no_mutation_since_scan_start():
    index = _sample_index()
    index._last_mutation_at_monotonic = 50.0  # last mutation well before the scan

    applied = index.replace(
        [IndexedReminder("9", "Fresh eggs", "Shopping", None, False)],
        not_mutated_since=100.0,
    )

    assert applied is True
    assert index.search("eggs", include_completed=True)[0]["id"] == "9"


def test_replace_without_guard_is_unconditional():
    # Back-compat: omitting not_mutated_since always replaces.
    index = _sample_index()
    index._last_mutation_at_monotonic = 999.0
    applied = index.replace([IndexedReminder("9", "Eggs", "Shopping", None, False)])
    assert applied is True


# ---------------------------------------------------------------------------
# completed_stats (issue #24, Tier 1 — from the index)
# ---------------------------------------------------------------------------

def test_completed_stats_none_before_build():
    assert ReminderSearchIndex().completed_stats() is None


def test_completed_stats_counts_pct_and_sorts_most_completed_first():
    index = ReminderSearchIndex()
    index.replace([
        IndexedReminder("1", "Buy milk", "Shopping", None, False),
        IndexedReminder("2", "Old bread", "Shopping", None, True),
        IndexedReminder("3", "Old eggs", "Shopping", None, True),
        IndexedReminder("4", "Take bins", "Routines", None, True),
    ], built_at=1000.0)

    stats = index.completed_stats()
    # Shopping (2 completed) sorts before Routines (1 completed).
    assert [s["list"] for s in stats] == ["Shopping", "Routines"]
    shopping = stats[0]
    assert shopping == {
        "list": "Shopping", "total": 3, "completed": 2,
        "incomplete": 1, "completed_pct": 67,  # round(2/3*100)
    }


def test_completed_stats_filters_to_single_list():
    index = ReminderSearchIndex()
    index.replace([
        IndexedReminder("1", "a", "Shopping", None, True),
        IndexedReminder("2", "b", "Routines", None, True),
    ], built_at=1000.0)
    stats = index.completed_stats("Routines")
    assert [s["list"] for s in stats] == ["Routines"]


def test_handle_completed_stats_builds_envelope(monkeypatch):
    index = ReminderSearchIndex()
    index.replace([
        IndexedReminder("1", "Buy milk", "Shopping", None, False),
        IndexedReminder("2", "Old eggs", "Shopping", None, True),
    ], built_at=time.monotonic())
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    result = asyncio.run(reminders.handle("reminder_completed_stats", {}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "ok"
    assert payload["source"] == "index"
    assert payload["lists"][0]["list"] == "Shopping"
    assert payload["totals"] == {"completed": 1, "incomplete": 1}


def test_handle_completed_stats_warming_when_index_not_built(monkeypatch):
    monkeypatch.setattr(reminders, "_search_index", ReminderSearchIndex())
    refreshed = []
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: refreshed.append(True))

    result = asyncio.run(reminders.handle("reminder_completed_stats", {}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "warming"
    assert payload["lists"] == []
    assert refreshed  # a background build was kicked


# ---------------------------------------------------------------------------
# completed_stats age breakdown (issue #24 Tier 2)
# ---------------------------------------------------------------------------

def test_parse_completion_iso_handles_valid_empty_and_junk():
    assert reminders._parse_completion_iso("2026-08-12T09:00:00") == datetime(2026, 8, 12, 9, 0, 0)
    assert reminders._parse_completion_iso(None) is None
    assert reminders._parse_completion_iso("") is None
    assert reminders._parse_completion_iso("not-a-date") is None


def test_bucket_completed_ages_nests_windows_counts_undatable_and_oldest():
    now = datetime(2026, 8, 12, 12, 0, 0)
    rows = [
        ("Shopping", "2024-07-01T09:00:00"),  # ~2y  -> 30d, 90d, 365d
        ("Shopping", "2026-06-01T09:00:00"),  # ~72d -> 30d only
        ("Shopping", "2026-08-10T09:00:00"),  # 2d   -> none
        ("Shopping", None),                    # undatable
        ("Routines", "2025-01-01T00:00:00"),  # >1y  -> all three
    ]
    buckets = reminders._bucket_completed_ages(rows, now)
    shopping = buckets["Shopping"]
    assert shopping["older_than"] == {"30d": 2, "90d": 1, "365d": 1}
    assert shopping["undatable_completed"] == 1
    assert shopping["oldest_completed"] == "2024-07-01T09:00:00"
    assert buckets["Routines"]["older_than"] == {"30d": 1, "90d": 1, "365d": 1}


def test_scan_completed_ages_parses_status_and_rows(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "completed_age_scan"
        return (
            "timeout\n"
            "Shopping|2026-01-01T00:00:00\n"
            "Routines|\n"          # undatable -> None
            "malformed-no-pipe"    # skipped
        )

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    status, rows = asyncio.run(reminders._scan_completed_ages("all"))
    assert status == "timeout"
    assert rows == [("Shopping", "2026-01-01T00:00:00"), ("Routines", None)]


def test_handle_completed_stats_age_breakdown_annotates_lists(monkeypatch):
    index = ReminderSearchIndex()
    index.replace([
        IndexedReminder("1", "a", "Shopping", None, True),
        IndexedReminder("2", "b", "Shopping", None, False),
    ], built_at=time.monotonic())
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    captured = {}

    async def fake_run(action, *args, **kwargs):
        captured["action"] = action
        captured["filter"] = args[1]
        return "ok\nShopping|2024-01-01T00:00:00\nShopping|"

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle(
        "reminder_completed_stats", {"list": "Shopping", "age_breakdown": True}
    ))
    payload = json.loads(result[0].text)

    assert captured["action"] == "completed_age_scan"
    assert captured["filter"] == "Shopping"  # single-list filter forwarded
    assert payload["source"] == "index+scan"
    entry = payload["lists"][0]
    assert entry["completed_older_than"]["365d"] == 1
    assert entry["undatable_completed"] == 1
    assert entry["oldest_completed"] == "2024-01-01T00:00:00"


def test_handle_completed_stats_age_breakdown_timeout_annotates_and_warns(monkeypatch):
    index = ReminderSearchIndex()
    index.replace([IndexedReminder("1", "a", "Shopping", None, True)], built_at=time.monotonic())
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    async def fake_run(action, *args, **kwargs):
        return "timeout\n"  # budget hit before any rows

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle(
        "reminder_completed_stats", {"age_breakdown": True}
    ))
    payload = json.loads(result[0].text)

    assert payload["status"] == "timeout"
    assert "partial" in payload["message"]
    # Lists are still annotated (with zeros) even when the scan returned nothing.
    assert payload["lists"][0]["completed_older_than"] == {"30d": 0, "90d": 0, "365d": 0}


def test_handle_completed_stats_echoes_requested_list(monkeypatch):
    index = ReminderSearchIndex()
    index.replace([IndexedReminder("1", "a", "Shopping", None, True)], built_at=time.monotonic())
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    result = asyncio.run(reminders.handle(
        "reminder_completed_stats", {"list": "Groceries"}
    ))
    payload = json.loads(result[0].text)
    assert payload["requested_list"] == "Groceries"
    assert payload["lists"] == []  # unknown or empty — indistinguishable, hence the echo


def test_handle_completed_stats_age_breakdown_skips_scan_when_no_lists(monkeypatch):
    index = ReminderSearchIndex()
    index.replace([IndexedReminder("1", "a", "Shopping", None, True)], built_at=time.monotonic())
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    calls = {"n": 0}

    async def fake_run(action, *args, **kwargs):
        calls["n"] += 1
        return "ok\n"

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle(
        "reminder_completed_stats", {"list": "Groceries", "age_breakdown": True}
    ))
    payload = json.loads(result[0].text)

    assert calls["n"] == 0  # nothing to annotate -> no live scan
    assert payload["source"] == "index"
    assert payload["lists"] == []


# ---------------------------------------------------------------------------
# handle() dispatch and async handlers (faked _run_script)
# ---------------------------------------------------------------------------

def test_handle_rejects_unknown_tool():
    with pytest.raises(ValueError, match="Unknown reminder tool"):
        asyncio.run(reminders.handle("reminder_bogus", {}))


def test_handle_list_lists_parses_output(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "list_lists"
        return "Reminders|21\nShopping|5"

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle("reminder_list_lists", {}))
    assert json.loads(result[0].text) == [
        {"name": "Reminders", "count": 21},
        {"name": "Shopping", "count": 5},
    ]


def test_handle_list_lists_retries_once_on_timeout(monkeypatch):
    calls = {"n": 0}

    async def fake_run(action, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("osascript timed out after 60s (action='list_lists').")
        return "Reminders|21"

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle("reminder_list_lists", {}))
    assert calls["n"] == 2  # first timed out, retry succeeded
    assert json.loads(result[0].text) == [{"name": "Reminders", "count": 21}]


def test_handle_list_lists_does_not_retry_non_timeout_error(monkeypatch):
    calls = {"n": 0}

    async def fake_run(action, *args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("No such list: Groceries")

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    with pytest.raises(RuntimeError, match="No such list"):
        asyncio.run(reminders.handle("reminder_list_lists", {}))
    assert calls["n"] == 1  # a genuine error is not retried


def test_handle_search_serves_from_index(monkeypatch):
    index = ReminderSearchIndex()
    index.replace([
        IndexedReminder("1", "Buy milk", "Shopping", None, False),
        IndexedReminder("2", "Milk done", "Shopping", None, True),
    ], built_at=time.monotonic())
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    result = asyncio.run(reminders.handle("reminder_search", {"query": "milk"}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "ok"
    assert payload["total"] == 1  # completed reminder excluded by default
    assert payload["reminders"][0]["id"] == "1"
    assert payload["reminders"][0]["notes"] is None


def test_handle_search_reports_warming_when_index_not_built(monkeypatch):
    monkeypatch.setattr(reminders, "_search_index", ReminderSearchIndex())
    refresh_calls = []
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: refresh_calls.append(True))

    result = asyncio.run(reminders.handle("reminder_search", {"query": "x"}))
    payload = json.loads(result[0].text)

    assert payload["status"] == "warming"
    assert payload["reminders"] == []
    assert refresh_calls  # a background build was triggered


def test_handle_search_notes_ok_forwards_budget_and_timeout(monkeypatch):
    captured = {}

    async def fake_run(action, *args, **kwargs):
        captured["action"] = action
        captured["args"] = args
        captured["timeout"] = kwargs.get("timeout")
        # First line is the status; then one record.
        return "ok\n1|Buy milk|2026-01-01T09:00:00|call the shop|false|Shopping"

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)
    result = asyncio.run(reminders.handle(
        "reminder_search", {"query": "shop", "search_notes": True}
    ))
    payload = json.loads(result[0].text)

    # search <query> <include_completed> <search_notes> <time_budget>
    assert captured["action"] == "search"
    assert captured["args"] == (
        "shop", "false", "true", str(reminders._NOTES_SCAN_BUDGET_SECONDS)
    )
    # The osascript timeout must exceed the internal budget so the cooperative
    # abort fires before a hard kill.
    assert captured["timeout"] == (
        reminders._NOTES_SCAN_BUDGET_SECONDS
        + reminders._NOTES_SCAN_TIMEOUT_MARGIN_SECONDS
    )
    assert captured["timeout"] > reminders._NOTES_SCAN_BUDGET_SECONDS

    assert payload["status"] == "ok"
    assert payload["total"] == 1
    assert payload["reminders"][0]["id"] == "1"
    assert payload["reminders"][0]["notes"] == "call the shop"  # notes populated
    assert "message" not in payload


def test_handle_search_notes_timeout_returns_partial_with_message(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        # Budget hit: status "timeout" with the one match found so far.
        return "timeout\n7|Book flights|2026-02-02T00:00:00||false|Travel"

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)
    result = asyncio.run(reminders.handle(
        "reminder_search", {"query": "book", "search_notes": True}
    ))
    payload = json.loads(result[0].text)

    assert payload["status"] == "timeout"
    assert payload["total"] == 1  # the partial match is still returned
    assert payload["reminders"][0]["id"] == "7"
    assert "partial results" in payload["message"]


def test_handle_search_notes_empty_result_is_ok(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        return "ok"  # status only, no matches

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)
    result = asyncio.run(reminders.handle(
        "reminder_search", {"query": "nothing", "search_notes": True}
    ))
    payload = json.loads(result[0].text)

    assert payload["status"] == "ok"
    assert payload["total"] == 0
    assert payload["reminders"] == []
    assert "message" not in payload


# ---------------------------------------------------------------------------
# search_notes seeds from the title index (issue #21)
# ---------------------------------------------------------------------------

def test_merge_note_matches_unions_and_live_wins():
    seed = [
        {"id": "a", "title": "Buy milk", "notes": None, "list": "Shopping"},
        {"id": "b", "title": "Take bins", "notes": None, "list": "Routines"},
    ]
    live = [
        {"id": "a", "title": "Buy milk", "notes": "at the corner shop", "list": "Shopping"},
        {"id": "c", "title": "Pay rent", "notes": "milk fund", "list": "Bills"},
    ]
    merged = {m["id"]: m for m in reminders._merge_note_matches(seed, live)}
    assert set(merged) == {"a", "b", "c"}            # union
    assert merged["a"]["notes"] == "at the corner shop"  # live wins (notes populated)
    assert merged["b"]["notes"] is None               # seed-only title match kept


def test_handle_search_notes_seeds_title_match_the_live_scan_missed(monkeypatch):
    # The exact run 13/14 defect: a title match in a small list that the budgeted
    # live scan never reaches. Seeding from the index must still return it.
    index = ReminderSearchIndex()
    index.replace(
        [IndexedReminder("s1", "MCP-SMOKE scratch", "Reminders", None, False)],
        built_at=time.monotonic(),
    )
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    async def fake_run(action, *args, **kwargs):
        return "ok\n"  # live scan finished but matched nothing

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle(
        "reminder_search", {"query": "MCP-SMOKE", "search_notes": True}
    ))
    payload = json.loads(result[0].text)

    assert payload["total"] == 1  # was 0 before #21 — never fewer than default
    assert payload["reminders"][0]["id"] == "s1"


def test_handle_search_notes_live_note_hit_overrides_seed(monkeypatch):
    index = ReminderSearchIndex()
    index.replace(
        [IndexedReminder("x", "Buy milk", "Shopping", None, False)],
        built_at=time.monotonic(),
    )
    monkeypatch.setattr(reminders, "_search_index", index)
    monkeypatch.setattr(reminders, "_kick_refresh", lambda: None)

    async def fake_run(action, *args, **kwargs):
        # Live scan re-finds x with notes, plus a body-only match y.
        return (
            "ok\n"
            "x|Buy milk|2026-01-01T09:00:00|oat milk|false|Shopping\n"
            "y|Pay rent|||false|Bills"  # 'milk' only in this reminder's notes
        )

    monkeypatch.setattr(reminders, "_run_script", fake_run)
    result = asyncio.run(reminders.handle(
        "reminder_search", {"query": "milk", "search_notes": True}
    ))
    by_id = {r["id"]: r for r in json.loads(result[0].text)["reminders"]}

    assert set(by_id) == {"x", "y"}
    assert by_id["x"]["notes"] == "oat milk"  # live populated notes, not the seed's null


def test_handle_create_returns_id_and_seeds_index_with_resolved_list(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "create"
        return "x-id-123|Reminders"  # resolved list name follows the id

    index = ReminderSearchIndex()
    index.replace([])
    monkeypatch.setattr(reminders, "_run_script", fake_run)
    monkeypatch.setattr(reminders, "_search_index", index)

    result = asyncio.run(reminders.handle(
        "reminder_create", {"title": "Test task", "list": "default"}
    ))

    assert result[0].text == "x-id-123"
    seeded = index.search("test task", include_completed=False)
    assert seeded and seeded[0]["list"] == "Reminders"


def test_handle_delete_removes_from_index(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "delete"
        return ""

    index = ReminderSearchIndex()
    index.replace([IndexedReminder("1", "Buy milk", "Shopping", None, False)])
    monkeypatch.setattr(reminders, "_run_script", fake_run)
    monkeypatch.setattr(reminders, "_search_index", index)

    asyncio.run(reminders.handle("reminder_delete", {"reminder_id": "1"}))
    assert index.search("milk", include_completed=True) == []


# ---------------------------------------------------------------------------
# _build_index wiring for the race guard (issue #20)
# ---------------------------------------------------------------------------

def test_build_index_discards_scan_when_a_mutation_raced_it(monkeypatch):
    # Simulate a delete that lands WHILE the background scan is in flight: the
    # scan still returns the (now-deleted) reminder, but the guard must stop it
    # from resurrecting the phantom. (Asserts live outside fake_run because
    # _build_index swallows exceptions.)
    index = ReminderSearchIndex()
    index.replace([IndexedReminder("1", "Old task", "L", None, False)])
    monkeypatch.setattr(reminders, "_search_index", index)

    captured = {}

    async def fake_run(action, *args, **kwargs):
        captured["action"] = action
        index.remove("1")  # mutation lands mid-scan
        index._last_mutation_at_monotonic = time.monotonic() + 100  # after scan start
        return "1|Old task|L||false"  # snapshot predates the delete

    monkeypatch.setattr(reminders, "_run_script", fake_run)

    asyncio.run(reminders._build_index())

    assert captured["action"] == "build_index"
    assert index.search("old task", include_completed=True) == []  # no phantom


def test_build_index_applies_scan_when_no_mutation_raced_it(monkeypatch):
    index = ReminderSearchIndex()
    monkeypatch.setattr(reminders, "_search_index", index)

    async def fake_run(action, *args, **kwargs):
        return "9|Fresh task|L||false"

    monkeypatch.setattr(reminders, "_run_script", fake_run)

    asyncio.run(reminders._build_index())

    assert index.search("fresh", include_completed=True)[0]["id"] == "9"
