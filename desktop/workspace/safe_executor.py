from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from desktop.workspace.refresh import MarketRefreshController
from desktop.workspace.reviews import DesktopReviewService
from desktop.workspace.scheduler import ScheduleType
from desktop.workspace.watchlists import WatchlistRepository
from trading.scanner.schemas import ScannerScanRequest
from trading.scanner.service import MarketScannerService
from trading.schemas import AnalysisMode


class SafeScheduledTaskExecutor:
    """Execute only the local, non-trading schedule allowlist."""

    def __init__(
        self,
        *,
        watchlists: WatchlistRepository,
        reviews: DesktopReviewService,
        scanner_factory: Callable[[], MarketScannerService] = MarketScannerService,
        refresh: MarketRefreshController | None = None,
    ) -> None:
        self.watchlists = watchlists
        self.reviews = reviews
        self.scanner_factory = scanner_factory
        self.refresh = refresh

    def __call__(
        self,
        task_type: ScheduleType,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if task_type == ScheduleType.MARKET_SCAN:
            response = self.scanner_factory().scan(
                ScannerScanRequest(
                    query=str(payload.get("query") or ""),
                    analysis_mode=AnalysisMode.SCREENING,
                    data_cutoff=datetime.now().astimezone(),
                    top_n=min(int(payload.get("top_n", 20)), 30),
                    allow_parser_model=False,
                    persist_run=True,
                )
            )
            result = response.model_dump(mode="json")
            return {
                "task_type": task_type.value,
                "scan_result": result,
                "network_request_count": 0,
                "model_call_count": 0,
            }
        if task_type in {
            ScheduleType.WATCHLIST_CHECK,
            ScheduleType.STOCK_RESEARCH,
            ScheduleType.POSITION_ANNOUNCEMENT_RISK,
        }:
            symbols = [
                item.symbol
                for watchlist in self.watchlists.list_watchlists()
                for item in self.watchlists.items(watchlist.watchlist_id)
            ]
            if task_type == ScheduleType.STOCK_RESEARCH and len(symbols) > 30:
                raise ValueError("scheduled research accepts at most 30 symbols")
            return {
                "task_type": task_type.value,
                "symbols": symbols,
                "network_request_count": 0,
                "model_call_count": 0,
            }
        if task_type == ScheduleType.DAILY_REVIEW:
            report = self.reviews.daily_review(datetime.now().astimezone().date())
            return {
                "task_type": task_type.value,
                "report": report.sections,
                "risk_flags": report.risk_flags,
            }
        if task_type == ScheduleType.PREMARKET_BRIEF:
            report = self.reviews.premarket_brief()
            return {
                "task_type": task_type.value,
                "report": report.sections,
                "risk_flags": report.risk_flags,
            }
        if task_type == ScheduleType.MARKET_DATA_REFRESH:
            if self.refresh is None:
                raise RuntimeError("market refresh is not configured")
            result = self.refresh.refresh(
                explicitly_confirmed=bool(payload.get("explicitly_confirmed"))
            )
            return {
                "task_type": task_type.value,
                "refresh": asdict(result),
            }
        return {
            "task_type": task_type.value,
            "status": "READ_ONLY_LOCAL_TASK",
            "network_request_count": 0,
            "model_call_count": 0,
        }


__all__ = ["SafeScheduledTaskExecutor"]
