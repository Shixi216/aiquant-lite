"""Block wrapper: every section of a v2 response degrades independently."""
from __future__ import annotations

from typing import Any


def block(
    data: Any | None = None,
    *,
    reason_code: str | None = None,
    stale: bool = False,
    source: str | None = None,
) -> dict:
    if data is not None:
        return {
            "available": True,
            "reason_code": None,
            "stale": stale,
            "source": source,
            "data": data,
        }
    return {
        "available": False,
        "reason_code": reason_code or "not_found",
        "stale": stale,
        "source": source,
        "data": None,
    }


__all__ = ["block"]
