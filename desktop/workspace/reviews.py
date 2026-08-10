from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from desktop.workspace.scheduler import ScheduleRepository
from desktop.workspace.watchlists import WatchlistRepository


@dataclass(frozen=True, slots=True)
class WorkspaceReview:
    report_type: str
    generated_at: datetime
    sections: dict[str, Any]
    risk_flags: tuple[str, ...]
    order_created: bool = False
    trade_instruction_created: bool = False


class DesktopReviewService:
    def __init__(
        self,
        *,
        database_path: Path,
        watchlists: WatchlistRepository,
        schedules: ScheduleRepository,
    ) -> None:
        self.database_path = database_path
        self.watchlists = watchlists
        self.schedules = schedules

    def _snapshot_state(self, now: datetime) -> dict[str, Any]:
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            row = connection.execute(
                """
                SELECT snapshot_time, valid_symbol_count
                FROM market_snapshot_runs
                WHERE completeness_status != 'FAILED'
                ORDER BY snapshot_time DESC LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {
                "snapshot_time": None,
                "valid_symbol_count": 0,
                "stale": True,
                "flags": ["MISSING_DATA", "REFRESH_REQUIRED"],
            }
        snapshot_time = row[0]
        if snapshot_time.tzinfo is None:
            snapshot_time = snapshot_time.replace(tzinfo=now.tzinfo)
        stale = snapshot_time < now - timedelta(minutes=15)
        return {
            "snapshot_time": snapshot_time.isoformat(),
            "valid_symbol_count": int(row[1]),
            "stale": stale,
            "flags": ["STALE_DATA", "REFRESH_REQUIRED"] if stale else [],
        }

    def daily_review(
        self, review_date: date, *, now: datetime | None = None
    ) -> WorkspaceReview:
        checked_at = now or datetime.now().astimezone()
        snapshot = self._snapshot_state(checked_at)
        watchlist_items = sum(
            len(self.watchlists.items(item.watchlist_id))
            for item in self.watchlists.list_watchlists()
        )
        with duckdb.connect(str(self.database_path), read_only=True) as connection:
            manual_positions = int(
                connection.execute(
                    """
                    SELECT count(*) FROM (
                        SELECT portfolio_id, symbol
                        FROM manual_trades
                        GROUP BY portfolio_id, symbol
                        HAVING sum(
                            CASE WHEN side='BUY' THEN quantity ELSE -quantity END
                        ) != 0
                    )
                    """
                ).fetchone()[0]
            )
            paper_accounts = int(
                connection.execute("SELECT count(*) FROM paper_accounts").fetchone()[0]
            )
            decisions = int(
                connection.execute(
                    "SELECT count(*) FROM decision_packets WHERE CAST(created_at AS DATE)=?",
                    [review_date],
                ).fetchone()[0]
            )
        sections = {
            "market_overview": snapshot,
            "major_anomalies": {"status": "LOCAL_SNAPSHOT_ONLY"},
            "watchlist_performance": {"item_count": watchlist_items},
            "manual_position_performance": {"position_count": manual_positions},
            "paper_account_performance": {"account_count": paper_accounts},
            "scanner_tasks": {"scheduled_count": len(self.schedules.list())},
            "research_tasks": {"status": "DESKTOP_STATE_ONLY"},
            "decision_tasks": {"count": decisions},
            "veto_records": {"status": "READ_ONLY"},
            "missing_data": snapshot["flags"],
            "stale_data": snapshot["stale"],
            "risk_reminders": [
                "正式60/40基本面覆盖不足",
                "SHADOW_COMPOSITE不参与正式动作",
            ],
            "next_day_todos": ["刷新数据后再解释扫描结果"],
        }
        return WorkspaceReview(
            report_type="DAILY_REVIEW",
            generated_at=checked_at,
            sections=sections,
            risk_flags=tuple(snapshot["flags"]),
        )

    def premarket_brief(
        self, *, now: datetime | None = None
    ) -> WorkspaceReview:
        checked_at = now or datetime.now().astimezone()
        snapshot = self._snapshot_state(checked_at)
        watchlist_risks = [
            {
                "symbol": item.symbol,
                "risk_flags": item.risk_flags,
                "freshness": item.freshness,
            }
            for watchlist in self.watchlists.list_watchlists()
            for item in self.watchlists.items(watchlist.watchlist_id)
            if item.risk_flags or item.freshness != "FRESH"
        ]
        sections = {
            "previous_trade_date_market_summary": snapshot,
            "watchlist_risks": watchlist_risks,
            "position_announcements_and_events": {"status": "READ_ONLY"},
            "data_freshness": snapshot,
            "planned_scans": [
                item.name
                for item in self.schedules.list()
                if item.enabled and item.task_type.value == "MARKET_SCAN"
            ],
            "pending_research": {"status": "DESKTOP_STATE_ONLY"},
            "system_status": "DEGRADED" if snapshot["stale"] else "HEALTHY",
            "data_gaps": snapshot["flags"],
        }
        return WorkspaceReview(
            report_type="PREMARKET_BRIEF",
            generated_at=checked_at,
            sections=sections,
            risk_flags=tuple(snapshot["flags"]),
        )


__all__ = ["DesktopReviewService", "WorkspaceReview"]
