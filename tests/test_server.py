"""Unit tests for server.py routing and tool aggregation.

These cover the pure routing helpers extracted from the MCP hooks:
``select_tool_module`` (name -> handler module) and
``collect_tool_definitions`` (aggregate every module's Tool definitions).
Importing server.py is side-effect-free beyond registering the MCP hooks, so
no real server is started here.
"""

import asyncio

import pytest

import server
from tools import mail, notes, reminders


# ---------------------------------------------------------------------------
# select_tool_module
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tool_name, expected_module",
    [
        ("mail_search", mail),
        ("mail_create_mailbox", mail),
        ("notes_get", notes),
        ("reminder_search", reminders),
    ],
)
def test_select_tool_module_routes_by_prefix(tool_name, expected_module):
    assert server.select_tool_module(tool_name) is expected_module


def test_select_tool_module_rejects_unknown_prefix():
    with pytest.raises(ValueError, match="Unknown tool"):
        server.select_tool_module("calendar_add_event")


def test_select_tool_module_rejects_empty_name():
    with pytest.raises(ValueError):
        server.select_tool_module("")


# ---------------------------------------------------------------------------
# collect_tool_definitions
# ---------------------------------------------------------------------------

def test_collect_tool_definitions_aggregates_all_modules():
    definitions = asyncio.run(server.collect_tool_definitions())
    names = [definition.name for definition in definitions]

    # Every collected tool name routes back to a real module (no orphans).
    for name in names:
        assert server.select_tool_module(name) is not None


def test_collect_tool_definitions_has_no_duplicate_names():
    definitions = asyncio.run(server.collect_tool_definitions())
    names = [definition.name for definition in definitions]
    assert len(names) == len(set(names))


def test_collect_tool_definitions_matches_per_module_counts():
    definitions = asyncio.run(server.collect_tool_definitions())
    names = [definition.name for definition in definitions]

    mail_tools = [name for name in names if name.startswith("mail_")]
    notes_tools = [name for name in names if name.startswith("notes_")]
    reminder_tools = [name for name in names if name.startswith("reminder_")]

    assert len(mail_tools) == 10
    assert len(notes_tools) == 7
    assert len(reminder_tools) == 7
