"""Unit tests for tools/_osascript.py.

The pure functions (normalize_line_endings, clean_osascript_error,
interpret_osascript_result) are tested directly. The asynchronous
``run_osascript`` wrapper is tested against a fake subprocess so no real
osascript process is spawned -- the tests stay fast and deterministic.
"""

import asyncio

import pytest

from tools import _osascript


# ---------------------------------------------------------------------------
# normalize_line_endings
# ---------------------------------------------------------------------------

def test_normalize_line_endings_converts_crlf_and_cr_to_newline():
    assert _osascript.normalize_line_endings("a\r\nb\rc\n") == "a\nb\nc"


def test_normalize_line_endings_strips_surrounding_whitespace():
    assert _osascript.normalize_line_endings("  hello \n") == "hello"


def test_normalize_line_endings_leaves_clean_text_unchanged():
    assert _osascript.normalize_line_endings("one\ntwo") == "one\ntwo"


# ---------------------------------------------------------------------------
# clean_osascript_error
# ---------------------------------------------------------------------------

def test_clean_osascript_error_strips_path_offsets_and_code():
    raw = (
        "/Users/x/scripts/mail.applescript:4998:5033: "
        "execution error: Mailbox not found: iCloud/Nope (-2700)"
    )
    assert _osascript.clean_osascript_error(raw) == "Mailbox not found: iCloud/Nope"


def test_clean_osascript_error_without_trailing_code():
    raw = "path.applescript:1:2: execution error: Something went wrong"
    assert _osascript.clean_osascript_error(raw) == "Something went wrong"


def test_clean_osascript_error_falls_back_when_no_execution_marker():
    # A compile error has no "execution error:" marker, so it is returned as-is.
    compile_error = "script.applescript: syntax error: Expected end of line (-2741)"
    assert _osascript.clean_osascript_error(compile_error) == compile_error


def test_clean_osascript_error_strips_outer_whitespace():
    raw = "  path:1:2: execution error: Boom (-1)  "
    assert _osascript.clean_osascript_error(raw) == "Boom"


# ---------------------------------------------------------------------------
# interpret_osascript_result
# ---------------------------------------------------------------------------

def test_interpret_result_success_decodes_and_normalizes():
    result = _osascript.interpret_osascript_result(0, b"total\r\n1|x\n", b"")
    assert result == "total\n1|x"


def test_interpret_result_nonzero_raises_cleaned_message():
    stderr = b"p.applescript:1:2: execution error: Reminder not found: 42 (-1728)"
    with pytest.raises(RuntimeError, match="^Reminder not found: 42$"):
        _osascript.interpret_osascript_result(1, b"", stderr)


# ---------------------------------------------------------------------------
# run_osascript (asynchronous wrapper, tested with a fake subprocess)
# ---------------------------------------------------------------------------

class _FakeProcess:
    """Minimal stand-in for an asyncio subprocess used by run_osascript."""

    def __init__(self, return_code=0, stdout=b"", stderr=b"", hang=False):
        self.returncode = return_code
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self.was_killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(10)  # long enough to trip run_osascript's timeout
        return self._stdout, self._stderr

    def kill(self):
        self.was_killed = True

    async def wait(self):
        return self.returncode


def _patch_subprocess(monkeypatch, fake_process):
    """Make asyncio.create_subprocess_exec return the given fake process."""
    async def fake_create_subprocess_exec(*args, **kwargs):
        fake_process.launched_argv = args
        return fake_process

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", fake_create_subprocess_exec
    )


def test_run_osascript_returns_clean_stdout_on_success(monkeypatch):
    fake = _FakeProcess(return_code=0, stdout=b"ok\r\n")
    _patch_subprocess(monkeypatch, fake)

    result = asyncio.run(_osascript.run_osascript("script.applescript", "action"))

    assert result == "ok"
    # The action and its arguments are forwarded to osascript in order.
    assert fake.launched_argv == ("osascript", "script.applescript", "action")


def test_run_osascript_forwards_extra_arguments(monkeypatch):
    fake = _FakeProcess(return_code=0, stdout=b"")
    _patch_subprocess(monkeypatch, fake)

    asyncio.run(_osascript.run_osascript("s", "search", "query", "true"))

    assert fake.launched_argv == ("osascript", "s", "search", "query", "true")


def test_run_osascript_raises_cleaned_error_on_failure(monkeypatch):
    fake = _FakeProcess(
        return_code=1,
        stderr=b"s.applescript:1:2: execution error: Nope (-2700)",
    )
    _patch_subprocess(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="^Nope$"):
        asyncio.run(_osascript.run_osascript("s", "action"))


def test_run_osascript_kills_child_and_raises_on_timeout(monkeypatch):
    fake = _FakeProcess(hang=True)
    _patch_subprocess(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="timed out"):
        asyncio.run(_osascript.run_osascript("s", "slow", timeout=0.01))

    assert fake.was_killed is True
