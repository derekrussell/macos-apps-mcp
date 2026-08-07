"""Unit tests for tools/notes.py.

Covers the pure parsers and pagination helper, and the async tool handlers with
a faked _run_script so no real osascript process is spawned.
"""

import asyncio
import json

import pytest

from tools import notes


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_parse_folder_line_valid():
    assert notes._parse_folder_line("Notes|12") == {"name": "Notes", "count": 12}


def test_parse_folder_line_wrong_field_count_returns_none():
    assert notes._parse_folder_line("no-delimiter") is None


def test_parse_note_valid_record():
    line = "id-1|Shopping list|Groceries|2026-07-31T19:18:43"
    assert notes._parse_note(line) == {
        "id": "id-1",
        "title": "Shopping list",
        "folder": "Groceries",
        "modified_date": "2026-07-31T19:18:43",
    }


def test_parse_note_empty_modified_date_becomes_none():
    parsed = notes._parse_note("id-2|Untitled|Notes|")
    assert parsed["modified_date"] is None


def test_parse_note_wrong_field_count_returns_none():
    assert notes._parse_note("id|title|folder") is None  # only 3 fields


# ---------------------------------------------------------------------------
# _paginate
# ---------------------------------------------------------------------------

def test_paginate_first_page_has_more():
    page, total, has_more = notes._paginate(list(range(10)), offset=0, count=3)
    assert page == [0, 1, 2]
    assert total == 10
    assert has_more is True


def test_paginate_last_page_no_more():
    page, total, has_more = notes._paginate(list(range(5)), offset=3, count=3)
    assert page == [3, 4]
    assert has_more is False


def test_paginate_negative_count_returns_rest():
    page, _total, has_more = notes._paginate(list(range(5)), offset=1, count=-1)
    assert page == [1, 2, 3, 4]
    assert has_more is False


# ---------------------------------------------------------------------------
# handle() dispatch and async handlers (faked _run_script)
# ---------------------------------------------------------------------------

def test_handle_rejects_unknown_tool():
    with pytest.raises(ValueError, match="Unknown notes tool"):
        asyncio.run(notes.handle("notes_bogus", {}))


def test_handle_list_folders_parses_output(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "list_folders"
        return "Notes|12\nGroceries|3"

    monkeypatch.setattr(notes, "_run_script", fake_run)
    result = asyncio.run(notes.handle("notes_list_folders", {}))
    assert json.loads(result[0].text) == [
        {"name": "Notes", "count": 12},
        {"name": "Groceries", "count": 3},
    ]


def test_handle_get_builds_pagination_envelope(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "get_notes"
        # First line is the total; then one note record.
        return "1\nid-1|Shopping|Groceries|2026-07-31T19:18:43"

    monkeypatch.setattr(notes, "_run_script", fake_run)
    result = asyncio.run(notes.handle("notes_get", {"folder": "Groceries"}))
    payload = json.loads(result[0].text)

    assert payload["total"] == 1
    assert payload["returned"] == 1
    assert payload["has_more"] is False
    assert payload["notes"][0]["id"] == "id-1"


def test_handle_search_paginates(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "search"
        return "\n".join(
            f"id-{n}|Note {n}|Notes|2026-07-31T00:00:0{n}" for n in range(3)
        )

    monkeypatch.setattr(notes, "_run_script", fake_run)
    result = asyncio.run(notes.handle(
        "notes_search", {"query": "note", "count": 2, "offset": 0}
    ))
    payload = json.loads(result[0].text)

    assert payload["status"] == "ok"
    assert payload["total"] == 3
    assert payload["returned"] == 2
    assert payload["has_more"] is True


def test_handle_create_returns_note_id(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "create"
        return "x-note-id-1"

    monkeypatch.setattr(notes, "_run_script", fake_run)
    result = asyncio.run(notes.handle("notes_create", {"title": "Hi"}))
    assert result[0].text == "x-note-id-1"


def test_handle_append_confirms(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "append"
        return ""

    monkeypatch.setattr(notes, "_run_script", fake_run)
    result = asyncio.run(notes.handle(
        "notes_append", {"note_id": "id-1", "text": "more"}
    ))
    assert result[0].text == "Appended to 'id-1'."
