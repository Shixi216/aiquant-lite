"""Immutable research artifacts, separate from formal decision packets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from data_hub.services.full_market_common import normalize_symbol

ROOT = Path(__file__).resolve().parents[2] / "reports" / "research" / "stock_reports"


def publish(snapshot: dict) -> dict:
    symbol = normalize_symbol(snapshot.get("symbol", ""))
    if symbol is None or snapshot.get("source") != "stock_report_service" or snapshot.get("research_only") is not True:
        raise ValueError("invalid research snapshot")
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    directory = ROOT / symbol
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest}.json"
    if not path.exists():
        path.write_text(payload, encoding="utf-8")
    temporary = directory / f"latest-{digest}.tmp"
    temporary.write_text(json.dumps({"sha256": digest}), encoding="utf-8")
    temporary.replace(directory / "latest.json")
    return {**snapshot, "sha256": digest}


def latest(symbol: str) -> dict:
    canonical = normalize_symbol(symbol)
    if canonical is None:
        raise ValueError("invalid symbol")
    directory = ROOT / canonical
    manifest = json.loads((directory / "latest.json").read_text(encoding="utf-8"))
    digest = manifest["sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid research snapshot hash")
    raw = (directory / f"{digest}.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("research snapshot checksum mismatch")
    snapshot = json.loads(raw)
    if snapshot.get("symbol") != canonical or snapshot.get("source") != "stock_report_service" or snapshot.get("research_only") is not True:
        raise ValueError("research snapshot provenance mismatch")
    return {**snapshot, "sha256": digest}
