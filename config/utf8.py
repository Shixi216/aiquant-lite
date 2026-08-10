from __future__ import annotations

import os
import sys
from typing import IO, Any, Mapping


UTF8_ENCODING = "utf-8"
UTF8_ERRORS = "backslashreplace"


def configure_utf8_stdio() -> None:
    """Make Python text streams deterministic on Windows and redirected pipes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding=UTF8_ENCODING, errors=UTF8_ERRORS)


def utf8_child_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment that forces Python stdio and text to UTF-8."""
    result = dict(os.environ if environment is None else environment)
    result["PYTHONUTF8"] = "1"
    result["PYTHONIOENCODING"] = f"{UTF8_ENCODING}:{UTF8_ERRORS}"
    return result


def write_utf8(text: Any, *, stream: IO[str] | None = None) -> None:
    """Write once without allowing a legacy console codec to fail the workflow."""
    target = stream or sys.stdout
    value = str(text)
    try:
        target.write(value + "\n")
        target.flush()
    except UnicodeError:
        buffer = getattr(target, "buffer", None)
        if buffer is None:
            raise
        buffer.write((value + "\n").encode(UTF8_ENCODING, errors=UTF8_ERRORS))
        buffer.flush()


__all__ = [
    "UTF8_ENCODING",
    "UTF8_ERRORS",
    "configure_utf8_stdio",
    "utf8_child_environment",
    "write_utf8",
]
