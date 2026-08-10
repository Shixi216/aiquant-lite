from __future__ import annotations

from datetime import datetime
from time import perf_counter
from typing import Any, Callable

from config.settings import settings
from database.db import get_connection
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.full_market import (
    AnalysisMode,
    DataExpansionRequest,
    ExpansionType,
    MarketSnapshotSyncRequest,
    UniverseSyncRequest,
)
from data_hub.schemas.history import (
    HistoryBackfillRequest,
    SelectionStrategy,
)
from data_hub.services.coverage_service import DataCoverageService
from data_hub.services.data_expansion_service import DataExpansionService
from data_hub.services.entity_linking_service import EntityLinkingService
from data_hub.services.full_market_common import stable_hash
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.market_snapshot_service import MarketSnapshotService
from data_hub.services.universe_service import StockUniverseService
class DailyDataUpdateService:
    """Auditable orchestration; unsafe/unavailable steps are explicit skips."""

    def __init__(
        self,
        *,
        universe: StockUniverseService | None = None,
        snapshot: MarketSnapshotService | None = None,
        expansion: DataExpansionService | None = None,
        entity_linker: EntityLinkingService | None = None,
        coverage: DataCoverageService | None = None,
        history: HistoryBackfillService | None = None,
        history_repository: HistoryRepository | None = None,
        clock: Any | None = None,
    ) -> None:
        self.universe = universe or StockUniverseService()
        self.snapshot = snapshot or MarketSnapshotService()
        self.expansion = expansion or DataExpansionService()
        self.entity_linker = entity_linker or EntityLinkingService()
        self.coverage = coverage or DataCoverageService()
        self.history_repository = history_repository or HistoryRepository()
        self.history = history or HistoryBackfillService(
            repository=self.history_repository
        )
        self.clock = clock or (lambda: datetime.now().astimezone())

    def run(
        self,
        *,
        data_cutoff: datetime,
        apply: bool,
        request_budget: int,
    ) -> dict[str, Any]:
        run_started_at = self.clock()
        run_id = f"daily_{stable_hash({'cutoff': data_cutoff, 'apply': apply})[:24]}"
        remaining = request_budget
        steps: list[dict[str, Any]] = []

        def execute(
            name: str,
            function: Callable[[], Any],
            *,
            budget_cost: int,
        ) -> None:
            nonlocal remaining
            started_at = self.clock()
            started = perf_counter()
            if remaining < budget_cost:
                steps.append(
                    {
                        "name": name,
                        "status": "SKIPPED_REQUEST_BUDGET",
                        "started_at": started_at,
                        "completed_at": self.clock(),
                        "processed_count": 0,
                        "elapsed_seconds": 0.0,
                    }
                )
                return
            try:
                result = function()
                payload = (
                    result.model_dump(mode="json")
                    if hasattr(result, "model_dump")
                    else result
                )
                actual_cost = int(payload.get("request_count", budget_cost))
                remaining = max(0, remaining - actual_cost)
                processed = int(
                    payload.get(
                        "processed_count",
                        payload.get(
                            "received_count",
                            payload.get(
                                "valid_symbol_count",
                                payload.get("completed_symbol_count", 0),
                            ),
                        ),
                    )
                )
                result_status = str(payload.get("status", "SUCCESS"))
                steps.append(
                    {
                        "name": name,
                        "status": result_status,
                        "started_at": started_at,
                        "completed_at": self.clock(),
                        "processed_count": processed,
                        "elapsed_seconds": max(0.0, perf_counter() - started),
                        "result": payload,
                    }
                )
            except Exception as exc:
                remaining -= budget_cost
                steps.append(
                    {
                        "name": name,
                        "status": "FAILED",
                        "started_at": started_at,
                        "completed_at": self.clock(),
                        "processed_count": 0,
                        "elapsed_seconds": max(0.0, perf_counter() - started),
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                    }
                )

        execute(
            "sync_stock_universe",
            lambda: self.universe.sync(
                UniverseSyncRequest(
                    analysis_mode=AnalysisMode.RESEARCH,
                    dry_run=not apply,
                    provider="AUTO",
                    request_budget=min(3, remaining),
                    data_cutoff=data_cutoff,
                )
            ),
            budget_cost=min(3, remaining),
        )
        execute(
            "sync_market_snapshot",
            lambda: self.snapshot.sync(
                MarketSnapshotSyncRequest(
                    analysis_mode=AnalysisMode.SCREENING,
                    dry_run=not apply,
                    provider="AKSHARE",
                    request_budget=1,
                    data_cutoff=data_cutoff,
                )
            ),
            budget_cost=1,
        )
        trade_date = self.history_repository.latest_completed_trade_date(
            data_cutoff
        )
        cutoff_date = data_cutoff.astimezone().date()
        daily_symbols: list[str] = []
        if trade_date == cutoff_date and remaining > 0:
            with get_connection() as connection:
                daily_symbols = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        WITH latest_version AS (
                            SELECT universe_version
                            FROM stock_universe_versions
                            WHERE data_cutoff <= ?
                            ORDER BY effective_at DESC
                            LIMIT 1
                        ),
                        latest_snapshot AS (
                            SELECT snapshot_id
                            FROM market_snapshot_runs
                            WHERE snapshot_time <= ?
                            ORDER BY snapshot_time DESC
                            LIMIT 1
                        )
                        SELECT u.symbol
                        FROM stock_universe u
                        JOIN market_snapshot_items snapshot
                          ON snapshot.symbol = u.symbol
                         AND snapshot.snapshot_id = (
                            SELECT snapshot_id FROM latest_snapshot
                         )
                        WHERE u.universe_version = (
                            SELECT universe_version FROM latest_version
                        )
                          AND u.listing_status = 'ACTIVE'
                          AND snapshot.is_suspended = FALSE
                        ORDER BY u.symbol
                        LIMIT ?
                        """,
                        [
                            data_cutoff,
                            data_cutoff,
                            min(remaining, settings.history_batch_size),
                        ],
                    ).fetchall()
                ]
        if trade_date != cutoff_date:
            steps.append(
                {
                    "name": "backfill_daily_bars",
                    "status": "SKIPPED_NOT_COMPLETED_TRADING_DAY",
                    "started_at": self.clock(),
                    "completed_at": self.clock(),
                    "processed_count": 0,
                    "reason": (
                        "daily update only fetches the completed current "
                        "trading day"
                    ),
                }
            )
        elif not daily_symbols:
            steps.append(
                {
                    "name": "backfill_daily_bars",
                    "status": "SKIPPED_NO_NORMAL_TRADING_SYMBOLS",
                    "started_at": self.clock(),
                    "completed_at": self.clock(),
                    "processed_count": 0,
                    "reason": (
                        "no bounded normal-trading symbol set is available"
                    ),
                }
            )
        else:
            execute(
                "backfill_daily_bars",
                lambda: self.history.run(
                    HistoryBackfillRequest(
                        dry_run=not apply,
                        provider="AUTO",
                        symbols=daily_symbols,
                        start_date=trade_date,
                        end_date=trade_date,
                        target_trading_days=1,
                        batch_size=len(daily_symbols),
                        concurrency=1,
                        request_budget=remaining,
                        max_retries=1,
                        data_cutoff=data_cutoff,
                        selection_strategy=SelectionStrategy.EXPLICIT,
                        max_symbols=len(daily_symbols),
                        minimum_free_bytes=0,
                    )
                ),
                budget_cost=len(daily_symbols),
            )
        for expansion_type, name in (
            (ExpansionType.ANNOUNCEMENTS, "sync_announcements"),
            (ExpansionType.FINANCE_NEWS, "sync_finance_news"),
        ):
            execute(
                name,
                lambda kind=expansion_type: self.expansion.run(
                    DataExpansionRequest(
                        analysis_mode=AnalysisMode.RESEARCH,
                        dry_run=not apply,
                        provider="AKSHARE",
                        request_budget=1,
                        data_cutoff=data_cutoff,
                        expansion_type=kind,
                        trade_date=data_cutoff.date(),
                        start_date=data_cutoff.date(),
                        end_date=data_cutoff.date(),
                        batch_size=100,
                    )
                ),
                budget_cost=1,
            )
        execute(
            "build_entity_links",
            lambda: self.entity_linker.build(
                DataExpansionRequest(
                    analysis_mode=AnalysisMode.RESEARCH,
                    dry_run=not apply,
                    provider="LOCAL_RULES",
                    request_budget=0,
                    data_cutoff=data_cutoff,
                    expansion_type=ExpansionType.ENTITY_LINKS,
                )
            ),
            budget_cost=0,
        )
        for name in (
            "update_sentiment_snapshots",
            "update_policy_news_snapshots",
            "update_capital_flow_snapshots",
        ):
            steps.append(
                {
                    "name": name,
                    "status": "SKIPPED_NO_SAFE_FULL_MARKET_PIPELINE",
                    "started_at": self.clock(),
                    "completed_at": self.clock(),
                    "processed_count": 0,
                    "reason": (
                        "existing analysis service is retained; this stage does "
                        "not create a second business pipeline"
                    ),
                }
            )
        execute(
            "generate_coverage_report",
            lambda: self.coverage.generate(
                data_cutoff=data_cutoff,
                persist=apply,
            ),
            budget_cost=0,
        )
        failed = any(item["status"] == "FAILED" for item in steps)
        partial = any(item["status"] != "SUCCESS" for item in steps)
        completed_at = self.clock()
        result = {
            "run_id": run_id,
            "mode": "APPLY" if apply else "DRY_RUN",
            "data_cutoff": data_cutoff,
            "status": "FAILED" if failed else ("PARTIAL" if partial else "SUCCESS"),
            "request_budget": request_budget,
            "request_count": request_budget - remaining,
            "model_call_count": 0,
            "started_at": run_started_at,
            "completed_at": completed_at,
            "steps": steps,
        }
        if apply:
            self.history_repository.save_daily_update(
                run=result,
                steps=steps,
            )
        return result


__all__ = ["DailyDataUpdateService"]
