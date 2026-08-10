from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from config.settings import settings
from database.db import get_connection


router = APIRouter(prefix="/v1/internal/database-owner", tags=["database-owner"])

COUNT_KEYWORDS = (
    "decision",
    "historical_bar",
    "financial",
    "fundamental",
    "announcement",
    "data_records",
    "event",
    "news",
    "shadow",
    "manual_trade",
    "trading_order",
    "holding",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@router.get("/consistency")
def consistency() -> dict[str, Any]:
    path = Path(settings.opc_database_path).resolve()
    with get_connection() as connection:
        tables = [str(row[0]) for row in connection.execute("SHOW TABLES").fetchall()]
        selected = [
            table
            for table in tables
            if any(keyword in table.casefold() for keyword in COUNT_KEYWORDS)
        ]
        counts = {
            table: int(
                connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            )
            for table in selected
        }
        duplicate_packets = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT decision_id, decision_version, COUNT(*) AS n
                    FROM decision_packets
                    GROUP BY decision_id, decision_version
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
        )
    return {
        "owner_pid": os.getpid(),
        "database_path": str(path),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "table_counts": counts,
        "duplicate_decision_packets": duplicate_packets,
    }


__all__ = ["router"]
