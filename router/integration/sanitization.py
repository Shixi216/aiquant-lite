from __future__ import annotations

import re


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY))\s*=\s*\S+"
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\s,;]+")
_TRACEBACK = re.compile(r"(?is)traceback \(most recent call last\):.*")


def sanitize_error(exc: Exception | str) -> str:
    """Return a bounded error message without secrets, paths, or stack traces."""

    text = str(exc).strip() or "request failed"
    text = _TRACEBACK.sub("internal stack trace removed", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", text)
    text = _WINDOWS_PATH.sub("[LOCAL_PATH]", text)
    text = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[REDACTED]",
        text,
    )
    return text[:500]


__all__ = ["sanitize_error"]
