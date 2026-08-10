from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


SENSITIVE_KEY_FRAGMENTS = ("token", "secret", "password", "api_key", "credential")


def _default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): sanitize(item)
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in SENSITIVE_KEY_FRAGMENTS)
        }
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        sanitize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_default,
    )


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def stable_id(prefix: str, value: Any, *, size: int = 24) -> str:
    return f"{prefix}_{stable_hash(value)[:size]}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_git_head(project_root: Path) -> str:
    head_path = project_root / ".git" / "HEAD"
    try:
        head = head_path.read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head
        ref_path = project_root / ".git" / head.removeprefix("ref: ")
        return ref_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "UNKNOWN"


__all__ = [
    "canonical_json",
    "file_sha256",
    "read_git_head",
    "sanitize",
    "stable_hash",
    "stable_id",
]
