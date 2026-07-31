"""Shared helpers for the osascript-backed tool modules."""

import re

_EXEC_ERR = re.compile(r"execution error:\s*(.*)$", re.DOTALL)
_TRAILING_CODE = re.compile(r"\s*\(-?\d+\)\s*$")


def clean_osascript_error(stderr_text: str) -> str:
    """Reduce raw osascript stderr to its human-readable message.

    osascript reports errors like

        /path/to/script.applescript:12:34: execution error: Mailbox not found: X (-2700)

    which leak the script path, character offsets, and a generic numeric code —
    noise for a consuming client. Return just the message ("Mailbox not found:
    X"), falling back to the raw text if the expected shape isn't present (e.g. a
    compile error), so no information is ever lost.
    """
    text = stderr_text.strip()
    match = _EXEC_ERR.search(text)
    if not match:
        return text
    return _TRAILING_CODE.sub("", match.group(1)).strip() or text
