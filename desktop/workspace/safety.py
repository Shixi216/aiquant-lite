from __future__ import annotations

import json
import re
from typing import Any


FORBIDDEN_KEYS = {
    "analysis",
    "chain_of_thought",
    "hidden_reasoning",
    "internal_reasoning",
    "reasoning",
    "sql",
    "token",
    "secret",
    "api_key",
    "password",
}
_HIDDEN_BLOCK = re.compile(
    r"(?is)<(?:analysis|reasoning)>.*?</(?:analysis|reasoning)>|"
    r"\b(?:chain[-_ ]of[-_ ]thought|hidden reasoning)\b"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY)\s*=\s*\S+"
)
_SQL = re.compile(
    r"(?is)\bselect\b.{0,500}\bfrom\b|"
    r"\b(?:insert|replace)\s+into\b|"
    r"\bupdate\s+[A-Za-z_][A-Za-z0-9_]*\s+set\b|"
    r"\bdelete\s+from\b|"
    r"\b(?:drop|alter|create)\s+(?:table|database|view|index)\b"
)


def sanitize_user_visible_text(text: str) -> str:
    cleaned = _HIDDEN_BLOCK.sub("[INTERNAL_REASONING_REMOVED]", text)
    cleaned = _SECRET_ASSIGNMENT.sub("[CREDENTIAL_REMOVED]", cleaned)
    return cleaned[:8000]


def validate_persistable(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in FORBIDDEN_KEYS:
                raise ValueError("forbidden workspace state field")
            validate_persistable(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_persistable(item)
        return
    if isinstance(value, str):
        if _HIDDEN_BLOCK.search(value) or _SECRET_ASSIGNMENT.search(value):
            raise ValueError("sensitive or hidden content cannot be persisted")
        if _SQL.search(value):
            raise ValueError("SQL cannot be persisted in task state")


def safe_json_dumps(value: Any) -> str:
    validate_persistable(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


__all__ = [
    "safe_json_dumps",
    "sanitize_user_visible_text",
    "validate_persistable",
]
