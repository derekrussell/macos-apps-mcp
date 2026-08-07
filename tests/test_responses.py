"""Unit tests for tools/_responses.py (shared MCP-response helpers)."""

import json

from tools import _responses


# ---------------------------------------------------------------------------
# json_content
# ---------------------------------------------------------------------------

def test_json_content_wraps_payload_as_single_text_item():
    result = _responses.json_content({"a": 1, "b": [2, 3]})
    assert len(result) == 1
    assert result[0].type == "text"
    assert json.loads(result[0].text) == {"a": 1, "b": [2, 3]}


def test_json_content_is_pretty_printed():
    # indent=2 produces multi-line output.
    result = _responses.json_content({"a": 1})
    assert "\n" in result[0].text


# ---------------------------------------------------------------------------
# text_content
# ---------------------------------------------------------------------------

def test_text_content_wraps_string_as_single_text_item():
    result = _responses.text_content("hello")
    assert len(result) == 1
    assert result[0].type == "text"
    assert result[0].text == "hello"


# ---------------------------------------------------------------------------
# paginate
# ---------------------------------------------------------------------------

def test_paginate_first_page_has_more():
    page, total, has_more = _responses.paginate(list(range(10)), offset=0, count=3)
    assert page == [0, 1, 2]
    assert total == 10
    assert has_more is True


def test_paginate_last_page_no_more():
    page, total, has_more = _responses.paginate(list(range(5)), offset=3, count=3)
    assert page == [3, 4]
    assert total == 5
    assert has_more is False


def test_paginate_negative_count_returns_rest():
    page, _total, has_more = _responses.paginate(list(range(5)), offset=1, count=-1)
    assert page == [1, 2, 3, 4]
    assert has_more is False


def test_paginate_offset_past_end():
    page, total, has_more = _responses.paginate([1, 2], offset=5, count=3)
    assert page == []
    assert total == 2
    assert has_more is False
