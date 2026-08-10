from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_NON_NAME = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if bool(math.isnan(float(value))):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return "" if text.casefold() in {"nan", "none", "nat"} else text


def as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def parse_date(value: Any) -> date | None:
    text = clean_text(value).replace("-", "")
    if len(text) != 8 or not text.isdigit() or text == "00000000":
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def normalize_symbol(value: Any) -> str | None:
    text = clean_text(value).upper()
    if text.startswith(("SH.", "SZ.", "BJ.")):
        exchange, code = text.split(".", 1)
    elif "." in text:
        code, exchange = text.split(".", 1)
    else:
        code = text
        if code.startswith(("4", "8", "920")):
            exchange = "BJ"
        elif code.startswith(("5", "6", "9")):
            exchange = "SH"
        else:
            exchange = "SZ"
    if len(code) != 6 or not code.isdigit() or exchange not in {"SH", "SZ", "BJ"}:
        return None
    return f"{code}.{exchange}"


def is_a_share_symbol(symbol: str) -> bool:
    code, exchange = symbol.split(".", 1)
    if exchange == "SH":
        return code.startswith("6")
    if exchange == "SZ":
        return code.startswith(("0", "3"))
    return exchange == "BJ" and code.startswith(("4", "8", "920"))


def normalize_alias(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return _NON_NAME.sub("", normalized)


def board_for(symbol: str) -> str:
    code, exchange = symbol.split(".", 1)
    if exchange == "BJ":
        return "BEIJING"
    if exchange == "SH" and code.startswith(("688", "689")):
        return "STAR"
    if exchange == "SZ" and code.startswith(("300", "301")):
        return "CHINEXT"
    return "SH_MAIN" if exchange == "SH" else "SZ_MAIN"


def price_limit_type(symbol: str, is_st: bool) -> str:
    if is_st:
        return "ST_5_PERCENT"
    board = board_for(symbol)
    if board in {"STAR", "CHINEXT"}:
        return "GROWTH_20_PERCENT"
    if board == "BEIJING":
        return "BEIJING_30_PERCENT"
    return "MAIN_10_PERCENT"


__all__ = [
    "SHANGHAI_TZ",
    "as_float",
    "board_for",
    "clean_text",
    "is_a_share_symbol",
    "normalize_alias",
    "normalize_symbol",
    "parse_date",
    "price_limit_type",
    "stable_hash",
]
