"""Unit tests for tools/mail.py.

Covers the pure parsers and the async tool handlers with a faked _run_script so
no real osascript process is spawned.
"""

import asyncio
import json

import pytest

from tools import mail


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_parse_message_valid_record():
    line = "msg-1|Invoice|Apple <no_reply@apple.com>|2026-07-30T03:11:57|true"
    assert mail._parse_message(line) == {
        "id": "msg-1",
        "subject": "Invoice",
        "sender": "Apple <no_reply@apple.com>",
        "date": "2026-07-30T03:11:57",
        "is_read": True,
    }


def test_parse_message_wrong_field_count_returns_none():
    assert mail._parse_message("too|few|fields") is None


def test_parse_messages_reads_total_header_and_records():
    raw = "2\nid-1|A|x|2026-01-01T00:00:00|false\nid-2|B|y|2026-01-02T00:00:00|true"
    total, messages = mail._parse_messages(raw)
    assert total == 2
    assert [m["id"] for m in messages] == ["id-1", "id-2"]
    assert messages[1]["is_read"] is True


def test_parse_messages_empty_output():
    assert mail._parse_messages("") == (0, [])


def test_parse_messages_total_can_exceed_returned_records():
    # A paginated page: total 50, but only one record on this page.
    raw = "50\nid-1|A|x|2026-01-01T00:00:00|false"
    total, messages = mail._parse_messages(raw)
    assert total == 50
    assert len(messages) == 1


def test_parse_mailbox_line_splits_on_last_pipe():
    # The path itself contains slashes but no pipe; count is the final field.
    assert mail._parse_mailbox_line("iCloud/Church/Transactions|42") == {
        "path": "iCloud/Church/Transactions",
        "count": 42,
    }


def test_parse_mailbox_line_wrong_field_count_returns_none():
    assert mail._parse_mailbox_line("no-delimiter") is None


# ---------------------------------------------------------------------------
# handle() dispatch and async handlers (faked _run_script)
# ---------------------------------------------------------------------------

def test_handle_rejects_unknown_tool():
    with pytest.raises(ValueError, match="Unknown mail tool"):
        asyncio.run(mail.handle("mail_bogus", {}))


def test_handle_get_messages_builds_envelope(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "get_messages"
        return "1\nid-1|Hello|a@b.com|2026-07-30T10:00:00|false"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle("mail_get_messages", {}))
    payload = json.loads(result[0].text)

    assert payload["total"] == 1
    assert payload["returned"] == 1
    assert payload["has_more"] is False
    assert payload["messages"][0]["id"] == "id-1"


def test_handle_get_messages_reports_has_more(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        return "50\nid-1|Hello|a@b.com|2026-07-30T10:00:00|false"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle(
        "mail_get_messages", {"count": 1, "offset": 0}
    ))
    payload = json.loads(result[0].text)
    assert payload["total"] == 50
    assert payload["has_more"] is True


def test_handle_search_requires_a_criterion():
    with pytest.raises(ValueError, match="needs at least one of"):
        asyncio.run(mail.handle("mail_search", {"unread_only": True}))


def test_handle_search_forwards_arguments_in_order(monkeypatch):
    captured = {}

    async def fake_run(action, *args, **kwargs):
        captured["action"] = action
        captured["args"] = args
        return "0"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    asyncio.run(mail.handle("mail_search", {
        "sender": "apple", "mailbox": "inbox", "count": 10, "offset": 0,
    }))

    assert captured["action"] == "search"
    # search <mailbox> <sender> <subject> <body> <since> <until> <unread> <count> <offset>
    assert captured["args"] == (
        "inbox", "apple", "", "", "", "", "false", "10", "0",
    )


def test_handle_search_builds_status_envelope(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        return "1\nid-1|Hi|a@b.com|2026-07-30T10:00:00|false"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle("mail_search", {"sender": "a"}))
    payload = json.loads(result[0].text)
    assert payload["status"] == "ok"
    assert payload["total"] == 1


def test_handle_count_messages_returns_number(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "count_messages"
        return "1177"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle("mail_count_messages", {}))
    assert result[0].text == "1177"


def test_handle_list_mailboxes_parses_paths(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "list_mailboxes"
        return "inbox|10\niCloud/Church/Transactions|42"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle("mail_list_mailboxes", {}))
    assert json.loads(result[0].text) == [
        {"path": "inbox", "count": 10},
        {"path": "iCloud/Church/Transactions", "count": 42},
    ]


def test_handle_get_body_returns_text(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "get_body"
        return "Dear customer,\nyour receipt is attached."

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle("mail_get_body", {"message_id": "m1"}))
    assert result[0].text.startswith("Dear customer,")


def test_handle_move_confirms(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "move"
        return ""

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle(
        "mail_move", {"message_id": "m1", "mailbox": "iCloud/Archive"}
    ))
    assert result[0].text == "Moved 'm1' to 'iCloud/Archive'."


def test_handle_rename_mailbox_rejects_slash_in_new_name():
    with pytest.raises(ValueError, match="must be a plain mailbox name"):
        asyncio.run(mail.handle(
            "mail_rename_mailbox",
            {"mailbox": "iCloud/Old", "new_name": "a/b"},
        ))


def test_handle_rename_mailbox_returns_new_path(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "rename_mailbox"
        return "iCloud/DELETE ME - Old"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle(
        "mail_rename_mailbox",
        {"mailbox": "iCloud/Old", "new_name": "DELETE ME - Old"},
    ))
    assert result[0].text == "Renamed 'iCloud/Old' to 'iCloud/DELETE ME - Old'."


def test_handle_create_mailbox_created(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        assert action == "create_mailbox"
        return "created|iCloud/Receipts"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle(
        "mail_create_mailbox", {"mailbox": "iCloud/Receipts"}
    ))
    payload = json.loads(result[0].text)
    assert payload == {"status": "ok", "path": "iCloud/Receipts", "created": True}


def test_handle_create_mailbox_already_exists(monkeypatch):
    async def fake_run(action, *args, **kwargs):
        return "exists|iCloud/Receipts"

    monkeypatch.setattr(mail, "_run_script", fake_run)
    result = asyncio.run(mail.handle(
        "mail_create_mailbox", {"mailbox": "iCloud/Receipts"}
    ))
    payload = json.loads(result[0].text)
    assert payload["created"] is False
