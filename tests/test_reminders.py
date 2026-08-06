"""Unit tests for tools/reminders.py.

Covers the pure parsers and pagination helper, the ReminderSearchIndex class in
isolation (no globals, no asyncio), and the async tool handlers with a faked
_run_script so no real osascript process is spawned.
"""

import asyncio
import json
import time

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
# _paginate
# ---------------------------------------------------------------------------

def test_paginate_first_page_has_more():
    page, total, has_more = reminders._paginate(list(range(10)), offset=0, count=3)
    assert page == [0, 1, 2]
    assert total == 10
    assert has_more is True


def test_paginate_last_page_no_more():
    page, total, has_more = reminders._paginate(list(range(5)), offset=3, count=3)
    assert page == [3, 4]
    assert total == 5
    assert has_more is False


def test_paginate_negative_count_returns_rest():
    page, total, has_more = reminders._paginate(list(range(5)), offset=1, count=-1)
    assert page == [1, 2, 3, 4]
    assert has_more is False


def test_paginate_offset_past_end():
    page, total, has_more = reminders._paginate([1, 2], offset=5, count=3)
    assert page == []
    assert total == 2
    assert has_more is False


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
