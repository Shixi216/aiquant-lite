from __future__ import annotations

from datetime import datetime, timedelta
from time import perf_counter
from typing import Any

from database.db import get_connection
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    AnalysisMode,
    CandidateEnrichmentItem,
    CandidateEnrichmentRequest,
    CandidateEnrichmentResponse,
    DataExpansionRequest,
    ExpansionType,
)
from data_hub.services.data_expansion_service import DataExpansionService
from data_hub.services.full_market_common import stable_hash
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.schemas.history import (
    HistoryBackfillRequest,
    SelectionStrategy,
)


class CandidateEnrichmentService:
    def __init__(
        self,
        *,
        repository: FullMarketRepository | None = None,
        expansion_service: DataExpansionService | None = None,
        history_service: HistoryBackfillService | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or FullMarketRepository()
        self.expansion = expansion_service
        self.history = history_service or HistoryBackfillService()
        self.clock = clock or (lambda: datetime.now().astimezone())

    @staticmethod
    def _coverage(
        symbol: str,
        *,
        data_cutoff: datetime,
        minimum_history_days: int,
    ) -> tuple[list[str], list[str]]:
        window_start = data_cutoff - timedelta(days=30)
        with get_connection() as connection:
            history = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT trade_date)
                    FROM canonical_historical_bars
                    WHERE symbol = ? AND adjustment_type = 'RAW'
                      AND verification_status != 'CONFLICT'
                      AND data_cutoff <= ?
                      AND data_available_time <= ?
                    """,
                    [symbol, data_cutoff, data_cutoff],
                ).fetchone()[0]
            )
            fundamental = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM canonical_financial_records
                    WHERE symbol = ? AND data_cutoff <= ?
                    """,
                    [symbol, data_cutoff],
                ).fetchone()[0]
            )
            event_types = {
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT c.event_type
                    FROM event_clusters c
                    JOIN event_symbol_links l USING (event_cluster_id)
                    WHERE l.symbol = ?
                      AND c.event_time BETWEEN ? AND ?
                      AND c.data_cutoff <= ?
                    """,
                    [symbol, window_start, data_cutoff, data_cutoff],
                ).fetchall()
            }
        available: list[str] = []
        missing: list[str] = []
        if history >= minimum_history_days:
            available.append("DAILY_BARS")
        else:
            missing.append("DAILY_BARS")
        if fundamental:
            available.append("FUNDAMENTAL")
        else:
            missing.append("FUNDAMENTAL")
        if "announcement" in event_types:
            available.append("ANNOUNCEMENTS")
        else:
            missing.append("ANNOUNCEMENTS")
        if "finance_news" in event_types:
            available.append("FINANCE_NEWS")
        else:
            missing.append("FINANCE_NEWS")
        return available, missing

    def enrich(
        self,
        request: CandidateEnrichmentRequest,
    ) -> CandidateEnrichmentResponse:
        if (
            request.analysis_mode != AnalysisMode.RESEARCH
            and request.request_budget > 0
        ):
            raise ValueError("external candidate enrichment is RESEARCH-only")
        started_at = self.clock()
        run_id = f"enrich_{stable_hash({'request': request.model_dump(mode='json'), 'started': started_at})[:24]}"
        remaining_budget = request.request_budget
        items: list[CandidateEnrichmentItem] = []
        for symbol in request.symbols:
            item_started = perf_counter()
            request_count = 0
            risk_flags: list[str] = []
            available, missing = self._coverage(
                symbol,
                data_cutoff=request.data_cutoff,
                minimum_history_days=request.minimum_history_days,
            )
            fetched: list[str] = []
            error_message: str | None = None
            if (
                "DAILY_BARS" in missing
                and request.analysis_mode == AnalysisMode.RESEARCH
                and remaining_budget > 0
            ):
                try:
                    if self.expansion is not None and request.dry_run:
                        end = request.data_cutoff.date()
                        start = end - timedelta(
                            days=max(90, request.minimum_history_days * 2)
                        )
                        expansion = self.expansion.run(
                            DataExpansionRequest(
                                analysis_mode=AnalysisMode.RESEARCH,
                                dry_run=request.dry_run,
                                provider=request.provider,
                                request_budget=1,
                                data_cutoff=request.data_cutoff,
                                expansion_type=ExpansionType.DAILY_BARS,
                                symbols=[symbol],
                                start_date=start,
                                end_date=end,
                                batch_size=request.minimum_history_days,
                            )
                        )
                        used_requests = expansion.request_count
                        successful = expansion.success_count > 0
                        failed = expansion.failed_count > 0
                    else:
                        backfill = self.history.run(
                            HistoryBackfillRequest(
                                dry_run=request.dry_run,
                                provider=request.provider,
                                symbols=[symbol],
                                target_trading_days=(
                                    request.minimum_history_days
                                ),
                                batch_size=1,
                                concurrency=1,
                                request_budget=1,
                                max_retries=0,
                                data_cutoff=request.data_cutoff,
                                selection_strategy=SelectionStrategy.EXPLICIT,
                                max_symbols=1,
                                minimum_free_bytes=0,
                            )
                        )
                        used_requests = backfill.request_count
                        successful = (
                            backfill.successful_symbol_count > 0
                        )
                        failed = backfill.failed_symbol_count > 0
                    request_count += used_requests
                    remaining_budget = max(
                        0,
                        remaining_budget - used_requests,
                    )
                    if successful:
                        fetched.append("DAILY_BARS")
                    elif failed:
                        error_message = "daily-bar enrichment failed"
                except Exception as exc:
                    request_count += 1
                    remaining_budget = max(0, remaining_budget - 1)
                    error_message = f"{type(exc).__name__}: {exc}"
            if fetched and not request.dry_run:
                available, missing = self._coverage(
                    symbol,
                    data_cutoff=max(request.data_cutoff, self.clock()),
                    minimum_history_days=request.minimum_history_days,
                )
                if "DAILY_BARS" in available:
                    risk_flags.extend(
                        [
                            "TECHNICAL_INPUT_REFRESHED",
                            "CAPITAL_FLOW_INPUT_REFRESHED_SHADOW",
                        ]
                    )
                else:
                    fetched.remove("DAILY_BARS")
                    error_message = (
                        "persisted daily bars remain below the required "
                        "history threshold"
                    )
            if request.model_call_budget:
                risk_flags.append("MODEL_CALLS_DISABLED_FOR_THIS_STAGE")
            if request.analysis_mode == AnalysisMode.SCREENING:
                risk_flags.append("SCREENING_LOCAL_ONLY")
            if remaining_budget == 0 and missing:
                risk_flags.append("REQUEST_BUDGET_EXHAUSTED")
            status = (
                "FAILED"
                if error_message
                else (
                    "COMPLETE"
                    if not missing
                    else ("FETCHED_PARTIAL" if fetched else "MISSING_DATA")
                )
            )
            items.append(
                CandidateEnrichmentItem(
                    symbol=symbol,
                    enrichment_status=status,
                    fetched_data_types=fetched,
                    missing_data_types=missing,
                    elapsed_time=max(0.0, perf_counter() - item_started),
                    risk_flags=risk_flags,
                    request_count=request_count,
                    model_call_count=0,
                    error_message=error_message,
                )
            )
        request_count = sum(item.request_count for item in items)
        status = (
            "PARTIAL"
            if any(item.enrichment_status == "FAILED" for item in items)
            else "SUCCESS"
        )
        completed_at = self.clock()
        if not request.dry_run:
            self.repository.save_enrichment(
                run_id=run_id,
                mode="APPLY",
                analysis_mode=request.analysis_mode.value,
                provider=request.provider,
                request_budget=request.request_budget,
                request_count=request_count,
                model_call_budget=request.model_call_budget,
                model_call_count=0,
                data_cutoff=request.data_cutoff,
                status=status,
                started_at=started_at,
                completed_at=completed_at,
                items=items,
            )
        return CandidateEnrichmentResponse(
            run_id=run_id,
            mode="DRY_RUN" if request.dry_run else "APPLY",
            analysis_mode=request.analysis_mode,
            candidate_count=len(request.symbols),
            request_count=request_count,
            model_call_count=0,
            status=status,
            items=items,
        )


__all__ = ["CandidateEnrichmentService"]
