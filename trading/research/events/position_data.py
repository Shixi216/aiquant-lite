from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from data_hub.services.announcement_service import AnnouncementService
from data_hub.services.finance_news_service import FinanceNewsService
from data_hub.services.financial_statement_service import FinancialStatementService
from data_hub.services.realtime_quote_service import RealtimeQuoteService
from database.db import get_connection


@dataclass(frozen=True)
class PersistedResearchRecord:
    record_id: str
    symbol: str
    data_type: str
    event_time: datetime
    fetched_at: datetime
    source_name: str
    source_level: str
    verified: bool
    payload: dict[str, Any]


@dataclass(frozen=True)
class PositionResearchSnapshot:
    records: tuple[PersistedResearchRecord, ...]
    missing_information: tuple[str, ...]


class PositionResearchReader(Protocol):
    async def refresh(
        self,
        *,
        symbol: str,
        reviewed_at: datetime,
        lookback_days: int,
    ) -> PositionResearchSnapshot: ...


class ResearchDataReadRepository:
    """Read persisted Data Hub evidence after provider refresh attempts."""

    _LIMITS = {
        "realtime_quote": 1,
        "announcement": 20,
        "finance_news": 20,
        "financial_statement": 6,
    }

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> PersistedResearchRecord:
        payload = json.loads(row[8]) if isinstance(row[8], str) else row[8]
        return PersistedResearchRecord(
            record_id=row[0],
            symbol=row[1],
            data_type=row[2],
            event_time=row[3],
            fetched_at=row[4],
            source_name=row[5],
            source_level=row[6],
            verified=bool(row[7]),
            payload=payload,
        )

    def latest_for_symbol(
        self,
        *,
        symbol: str,
        reviewed_at: datetime,
        lookback_days: int,
    ) -> list[PersistedResearchRecord]:
        earliest = reviewed_at - timedelta(days=lookback_days)
        records: list[PersistedResearchRecord] = []
        with get_connection() as connection:
            for data_type, limit in self._LIMITS.items():
                rows = connection.execute(
                    """
                    SELECT record_id, symbol, data_type, event_time, fetched_at,
                           source_name, source_level, verified, payload_json
                    FROM data_records
                    WHERE symbol = ? AND data_type = ?
                      AND event_time >= ? AND event_time <= ?
                    ORDER BY event_time DESC, fetched_at DESC, record_id
                    LIMIT ?
                    """,
                    [symbol, data_type, earliest, reviewed_at, limit],
                ).fetchall()
                records.extend(self._row_to_record(row) for row in rows)
        return sorted(
            records,
            key=lambda record: (
                record.event_time,
                record.data_type,
                record.record_id,
            ),
        )


class PositionResearchDataService:
    """Refresh four research data families, then read their persisted IDs."""

    def __init__(
        self,
        repository: ResearchDataReadRepository | None = None,
    ) -> None:
        self.repository = repository or ResearchDataReadRepository()

    async def refresh(
        self,
        *,
        symbol: str,
        reviewed_at: datetime,
        lookback_days: int,
    ) -> PositionResearchSnapshot:
        start_date = (reviewed_at.date() - timedelta(days=lookback_days)).strftime(
            "%Y%m%d"
        )
        end_date = reviewed_at.date().strftime("%Y%m%d")
        failures: list[str] = []
        calls = (
            (
                "latest quote",
                lambda: RealtimeQuoteService().get_realtime_quote(
                    symbol=symbol,
                    persist=True,
                ),
            ),
            (
                "announcements",
                lambda: AnnouncementService().get_announcements(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    persist=True,
                ),
            ),
            (
                "finance news",
                lambda: FinanceNewsService().get_finance_news(
                    symbol=symbol,
                    limit=20,
                    persist=True,
                ),
            ),
            (
                "financial statements",
                lambda: FinancialStatementService().get_financial_statement(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    persist=True,
                ),
            ),
        )
        for label, call in calls:
            try:
                await asyncio.to_thread(call)
            except Exception as exc:
                failures.append(
                    f"{label} refresh failed: {type(exc).__name__}: {str(exc)[:300]}"
                )

        records = self.repository.latest_for_symbol(
            symbol=symbol,
            reviewed_at=reviewed_at,
            lookback_days=lookback_days,
        )
        available_types = {record.data_type for record in records}
        labels = {
            "realtime_quote": "latest quote",
            "announcement": "announcements",
            "finance_news": "finance news",
            "financial_statement": "financial statements",
        }
        missing = [
            f"No traceable {label} record is available"
            for data_type, label in labels.items()
            if data_type not in available_types
        ]
        return PositionResearchSnapshot(
            records=tuple(records),
            missing_information=tuple([*failures, *missing]),
        )
