from __future__ import annotations

from uuid import uuid4


def new_request_id() -> str:
    return "req_" + uuid4().hex[:24]


__all__ = ["new_request_id"]
