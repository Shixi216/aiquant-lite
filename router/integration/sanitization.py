from __future__ import annotations

import re


_SECRET_KEY = r"(?:[A-Z][A-Z0-9_]*)?(?:TOKEN|SECRET|PASSWORD|API_?KEY)"
_SECRET_ASSIGNMENT = re.compile(
    rf"(?i)\b({_SECRET_KEY})\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SECRET_JSON = re.compile(rf"(?i)([\"']{_SECRET_KEY}[\"']\s*:\s*)([\"'])(.*?)\2")
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\s,;]+")
_TRACEBACK = re.compile(r"(?is)traceback \(most recent call last\):.*")


def sanitize_error(exc: Exception | str) -> str:
    """Return a bounded error message without secrets, paths, or stack traces."""

    text = str(exc).strip() or "request failed"
    text = _TRACEBACK.sub("internal stack trace removed", text)
    text = _SECRET_JSON.sub(lambda match: f"{match[1]}{match[2]}[REDACTED]{match[2]}", text)
    text = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", text)
    text = _WINDOWS_PATH.sub("[LOCAL_PATH]", text)
    text = re.sub(
        r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+",
        r"\1[REDACTED]",
        text,
    )
    return text[:500]


__all__ = ["sanitize_error"]
