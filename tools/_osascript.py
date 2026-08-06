"""Shared helpers for invoking AppleScript through the `osascript` command.

Every tool module (mail, notes, reminders) talks to macOS by running an
AppleScript file through the `osascript` command-line tool. The work of
launching that subprocess, enforcing a timeout, and turning its result into
either clean text or a clear error is identical across those modules, so it
lives here once.

The module is deliberately split into two layers so the fiddly parts can be
unit-tested without spawning a real subprocess:

  * Pure functions -- ``normalize_line_endings``, ``clean_osascript_error`` and
    ``interpret_osascript_result`` -- hold all of the decision logic and depend
    only on their arguments. These are the functions worth covering with pytest.
  * ``run_osascript`` is the thin asynchronous wrapper that actually spawns the
    process, applies the timeout, and hands every decision to the pure functions
    above.
"""

import asyncio
import re
from pathlib import Path

# Seconds to wait for osascript before giving up, unless a caller overrides it.
# Chosen to stay under the MCP client's own request timeout; callers doing
# background work (for example the reminder index build) may pass a longer
# budget because no client is waiting on the result.
DEFAULT_TIMEOUT_SECONDS = 60.0

# Extra seconds to wait for a killed osascript child to actually exit. An
# osascript blocked inside a synchronous Apple event ignores SIGKILL until that
# event returns, so this wait is bounded rather than allowed to hang forever.
_REAP_TIMEOUT_SECONDS = 5.0

# osascript prefixes an execution error with the script path and character
# offsets, and suffixes it with a numeric code, for example:
#   /path/script.applescript:12:34: execution error: Mailbox not found: X (-2700)
# These patterns isolate the human-readable message ("Mailbox not found: X").
_EXECUTION_ERROR_PATTERN = re.compile(r"execution error:\s*(.*)$", re.DOTALL)
_TRAILING_ERROR_CODE_PATTERN = re.compile(r"\s*\(-?\d+\)\s*$")


def normalize_line_endings(text: str) -> str:
    """Convert CRLF/CR line endings to "\\n" and strip surrounding whitespace.

    AppleScript output can contain carriage returns. If a stray "\\r" survived,
    a later ``splitlines()`` would treat it as a record boundary and shift the
    pipe-delimited fields of that line. Normalising here keeps exactly one
    record per line.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def clean_osascript_error(stderr_text: str) -> str:
    """Reduce raw osascript stderr to just its human-readable message.

    Given::

        /path/script.applescript:12:34: execution error: Mailbox not found: X (-2700)

    this returns ``"Mailbox not found: X"``.

    When the expected shape is absent (for example a compile error, which has no
    "execution error:" marker) the stripped raw text is returned unchanged, so
    no information is ever lost.
    """
    stripped_text = stderr_text.strip()
    execution_error_match = _EXECUTION_ERROR_PATTERN.search(stripped_text)
    if execution_error_match is None:
        return stripped_text
    message = execution_error_match.group(1)
    message_without_code = _TRAILING_ERROR_CODE_PATTERN.sub("", message)
    return message_without_code.strip() or stripped_text


def interpret_osascript_result(
    return_code: int,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
) -> str:
    """Turn a finished osascript process into clean text, or raise a clear error.

    This holds the success/failure decision on its own so it can be unit-tested
    without a real subprocess:

      * On a zero return code, decode stdout and normalise its line endings.
      * On any other return code, raise ``RuntimeError`` carrying the cleaned
        stderr message.
    """
    if return_code != 0:
        raise RuntimeError(clean_osascript_error(stderr_bytes.decode()))
    return normalize_line_endings(stdout_bytes.decode())


async def run_osascript(
    script_path: str | Path,
    action: str,
    *arguments: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Run one AppleScript action through osascript and return its clean output.

    Args:
        script_path: Path to the ``.applescript`` file to execute.
        action:      The action keyword the script dispatches on (its first
                     argv item).
        *arguments:  Additional string arguments passed after the action.
        timeout:     Seconds to wait before killing osascript.

    Returns:
        The script's stdout, decoded and with line endings normalised.

    Raises:
        RuntimeError: If osascript times out, or exits with a non-zero code (in
                      which case the message is the cleaned stderr).
    """
    process = await asyncio.create_subprocess_exec(
        "osascript", str(script_path), action, *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except asyncio.TimeoutError:
        await _kill_and_reap(process)
        raise RuntimeError(
            f"osascript timed out after {timeout:g}s (action={action!r})."
        )

    return interpret_osascript_result(
        process.returncode, stdout_bytes, stderr_bytes
    )


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    """Kill a timed-out osascript child and wait briefly for it to exit.

    Reaping the child stops it lingering with its pipes held open. Because an
    osascript stuck in a synchronous Apple event ignores SIGKILL until that
    event returns, the wait is bounded by ``_REAP_TIMEOUT_SECONDS`` rather than
    allowed to block indefinitely.
    """
    process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=_REAP_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        pass
