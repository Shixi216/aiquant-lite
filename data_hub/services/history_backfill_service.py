from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import Settings, settings
from data_hub.providers import AKShareProvider, BaoStockProvider, TushareProvider
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.history import (
    AdjustmentType,
    BackfillStatus,
    EligibilityStatus,
    HistoryBackfillItem,
    HistoryBackfillRequest,
    HistoryBackfillResponse,
    HistoryRunActionRequest,
    HistoryRunDetail,
    SelectionStrategy,
    ShardType,
)
from data_hub.schemas.market import MarketRecord
from data_hub.services.full_market_common import stable_hash
from data_hub.services.history_provider_service import TradingCalendarService


HISTORY_ALGORITHM_VERSION = "historical-market-bars-v1"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _is_rate_limit(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "too many requests",
            "频率",
            "限流",
        )
    )


class HistoryBackfillService:
    def __init__(
        self,
        *,
        repository: HistoryRepository | None = None,
        calendar: TradingCalendarService | None = None,
        providers: dict[str, Any] | None = None,
        app_settings: Settings = settings,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or HistoryRepository()
        self.calendar = calendar or TradingCalendarService(
            repository=self.repository
        )
        self.settings = app_settings
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.providers = providers or {
            "BAOSTOCK": BaoStockProvider(),
            "AKSHARE": AKShareProvider(),
        }
        if (
            "TUSHARE" not in self.providers
            and app_settings.tushare_token
            and app_settings.tushare_token.strip()
        ):
            self.providers["TUSHARE"] = TushareProvider()

    def _provider_order(self, request: HistoryBackfillRequest) -> list[str]:
        primary = (
            self.settings.history_default_provider
            if request.provider == "AUTO"
            else request.provider
        )
        return list(
            dict.fromkeys([primary, *request.fallback_providers])
        )

    def _completed_end_date(self, data_cutoff: datetime) -> date:
        local = data_cutoff.astimezone()
        end_date = local.date()
        if local.hour < self.settings.history_market_close_available_hour:
            end_date -= timedelta(days=1)
        return end_date

    def _calendar_days(
        self,
        request: HistoryBackfillRequest,
        *,
        persist: bool,
    ) -> tuple[list[date], int]:
        end_date = request.end_date or self._completed_end_date(
            request.data_cutoff
        )
        if end_date > self._completed_end_date(request.data_cutoff):
            raise ValueError("history end_date is not a completed trading date")
        if request.start_date:
            existing = self.repository.trading_days(
                data_cutoff=request.data_cutoff,
                start_date=request.start_date,
                end_date=end_date,
            )
            if existing:
                return existing, 0
            days, count, _ = self.calendar.fetch(
                start_date=request.start_date,
                end_date=end_date,
                data_cutoff=request.data_cutoff,
                persist=persist,
            )
            return days, count
        days, count, _ = self.calendar.ensure_recent(
            data_cutoff=request.data_cutoff,
            target_trading_days=request.target_trading_days,
            persist=persist,
        )
        return days, count

    def _plan(
        self,
        request: HistoryBackfillRequest,
        *,
        persist_calendar: bool,
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[date],
        int,
        list[str],
    ]:
        days, calendar_requests = self._calendar_days(
            request,
            persist=persist_calendar,
        )
        if not days:
            raise RuntimeError("no audited trading days are available")
        threshold_20 = days[-20] if len(days) >= 20 else days[0]
        threshold_target = (
            days[-request.target_trading_days]
            if len(days) >= request.target_trading_days
            else days[0]
        )
        stocks = self.repository.select_universe(
            data_cutoff=request.data_cutoff,
            symbols=request.symbols,
            board=request.board,
            exchange=request.exchange,
            start_symbol=request.start_symbol,
            end_symbol=request.end_symbol,
            liquidity_order=(
                request.selection_strategy == SelectionStrategy.LIQUIDITY
            ),
            limit=request.max_symbols,
        )
        requested_symbols = set(request.symbols)
        selected_symbols = {item["symbol"] for item in stocks}
        warnings: list[str] = []
        missing_requested = sorted(requested_symbols - selected_symbols)
        if missing_requested:
            warnings.append(
                f"{len(missing_requested)} requested symbols are not active "
                "in the point-in-time universe"
            )

        planned_items: list[dict[str, Any]] = []
        for index, stock in enumerate(stocks):
            list_date = stock["list_date"]
            if list_date is None:
                eligibility = EligibilityStatus.UNKNOWN_LIST_DATE
            elif list_date > threshold_20:
                eligibility = EligibilityStatus.NEWLY_LISTED_20D
            elif list_date > threshold_target:
                eligibility = EligibilityStatus.NEWLY_LISTED_60D
            else:
                eligibility = EligibilityStatus.ELIGIBLE
            expected_days = sum(
                list_date is None or day >= list_date for day in days
            )
            status = (
                "PENDING"
                if eligibility == EligibilityStatus.ELIGIBLE
                else "SKIPPED_INELIGIBLE"
            )
            planned_items.append(
                {
                    "item_index": index,
                    "symbol": stock["symbol"],
                    "list_date": list_date,
                    "eligibility_status": eligibility.value,
                    "expected_trading_days": expected_days,
                    "status": status,
                    "error_message": (
                        None
                        if status == "PENDING"
                        else eligibility.value
                    ),
                    "payload": {
                        "board": stock["board"],
                        "exchange": stock["exchange"],
                        "latest_snapshot_special_status": stock[
                            "special_status"
                        ],
                    },
                }
            )

        run_id = "history_" + stable_hash(
            {
                "request": request.model_dump(mode="json"),
                "started_at": self.clock(),
            }
        )[:24]
        shards: list[dict[str, Any]] = []
        for shard_index, start in enumerate(
            range(0, len(planned_items), request.batch_size)
        ):
            chunk = planned_items[start : start + request.batch_size]
            shard_id = f"{run_id}_shard_{shard_index:04d}"
            for item in chunk:
                item["shard_id"] = shard_id
            shards.append(
                {
                    "shard_id": shard_id,
                    "shard_index": shard_index,
                    "start_symbol": chunk[0]["symbol"],
                    "end_symbol": chunk[-1]["symbol"],
                    "symbol_count": len(chunk),
                    "initial_skipped_count": sum(
                        item["status"].startswith("SKIPPED")
                        for item in chunk
                    ),
                }
            )
        return (
            planned_items,
            shards,
            stocks,
            days,
            calendar_requests,
            warnings,
        )

    @staticmethod
    def _normalize_record(
        record: MarketRecord,
        *,
        adjustment_type: AdjustmentType,
        generated_at: datetime,
    ) -> dict[str, Any]:
        payload = dict(record.data)
        observed_adjustment = str(
            payload.get("adjustment_type") or ""
        ).upper()
        if not observed_adjustment:
            raise ValueError("ADJUSTMENT_UNKNOWN: provider omitted adjustment")
        if observed_adjustment != adjustment_type.value:
            raise ValueError(
                "ADJUSTMENT_UNKNOWN: provider adjustment does not match task"
            )
        raw_volume = payload.get("volume")
        if raw_volume is None:
            raw_volume = payload.get("vol")
        volume = float(raw_volume) if raw_volume is not None else None
        amount_value = payload.get("amount")
        amount = float(amount_value) if amount_value is not None else None
        volume_unit = str(payload.get("volume_unit") or "")
        amount_unit = str(payload.get("amount_unit") or "")
        if volume is not None and volume_unit == "LOTS":
            volume *= 100
        if amount is not None and amount_unit == "THOUSAND_CNY":
            amount *= 1000
        compact_date = str(payload["trade_date"]).replace("-", "")
        trade_date = datetime.strptime(compact_date[:8], "%Y%m%d").date()
        normalized_payload = {
            "trade_date": trade_date.isoformat(),
            "open": payload.get("open"),
            "high": payload.get("high"),
            "low": payload.get("low"),
            "close": payload.get("close"),
            "volume": volume,
            "amount": amount,
            "volume_unit": "SHARES",
            "amount_unit": "CNY",
            "adjustment_type": adjustment_type.value,
        }
        value_hash = stable_hash(
            {
                "symbol": record.symbol,
                **normalized_payload,
            }
        )
        data_available_time = datetime.combine(
            trade_date,
            datetime.min.time(),
            tzinfo=SHANGHAI_TZ,
        ).replace(hour=16)
        return {
            "bar_id": f"hbar_{value_hash[:32]}",
            "symbol": record.symbol,
            "trade_date": trade_date,
            "event_time": record.event_time,
            "data_available_time": data_available_time,
            "data_cutoff": record.fetched_at,
            "generated_at": generated_at,
            "adjustment_type": adjustment_type.value,
            "open": payload.get("open"),
            "high": payload.get("high"),
            "low": payload.get("low"),
            "close": payload.get("close"),
            "volume": volume,
            "amount": amount,
            "volume_unit": "SHARES",
            "amount_unit": "CNY",
            "primary_source": record.source_name,
            "source_record_ids": [record.record_id],
            "verification_source_ids": [],
            "verification_status": "SINGLE_SOURCE",
            "confidence": 0.5,
            "content_hash": value_hash,
            "algorithm_version": HISTORY_ALGORITHM_VERSION,
            "raw_payload": payload,
        }

    def _fetch_one(
        self,
        *,
        run_id: str,
        item: dict[str, Any],
        request: HistoryBackfillRequest,
        start_date: date,
        end_date: date,
        budget: dict[str, Any],
        budget_lock: threading.Lock,
    ) -> dict[str, Any]:
        started_at = self.clock()
        started = perf_counter()
        audits: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        attempts = 0
        request_count = 0
        records: list[MarketRecord] = []
        provider_used: str | None = None
        rate_limited = False
        for provider_name in self._provider_order(request):
            provider = self.providers.get(provider_name)
            if provider is None:
                errors.append(
                    {
                        "provider": provider_name,
                        "error_type": "ProviderUnavailable",
                        "error_message": (
                            "SKIPPED_NO_TOKEN"
                            if provider_name == "TUSHARE"
                            else "provider is not registered"
                        ),
                    }
                )
                continue
            for attempt in range(1, request.max_retries + 2):
                with budget_lock:
                    if budget["used"] >= request.request_budget:
                        return {
                            "budget_exhausted": True,
                            "rate_limited": rate_limited,
                            "audits": audits,
                            "errors": errors,
                            "records": [],
                            "provider_used": None,
                            "attempts": attempts,
                            "request_count": request_count,
                            "started_at": started_at,
                            "elapsed": max(0.0, perf_counter() - started),
                        }
                    budget["used"] += 1
                attempts += 1
                request_count += 1
                request_started = self.clock()
                request_timer = perf_counter()
                try:
                    candidate = provider.get_daily_bars(
                        item["symbol"],
                        start_date.strftime("%Y%m%d"),
                        end_date.strftime("%Y%m%d"),
                        adjustment_type=request.adjustment_type.value,
                    )
                    request_completed = self.clock()
                    records = [
                        record
                        for record in candidate
                        if start_date
                        <= record.event_time.astimezone().date()
                        <= end_date
                        and record.event_time <= request.data_cutoff
                    ]
                    provider_used = provider_name
                    audit_status = "SUCCESS" if records else "SUCCESS_EMPTY"
                    audit_identity = {
                        "run_id": run_id,
                        "symbol": item["symbol"],
                        "provider": provider_name,
                        "attempt": attempt,
                        "started": request_started,
                    }
                    audits.append(
                        {
                            "audit_id": (
                                "provider_"
                                + stable_hash(audit_identity)[:32]
                            ),
                            "run_id": run_id,
                            "shard_id": item["shard_id"],
                            "symbol": item["symbol"],
                            "provider": provider_name,
                            "capability": (
                                "PER_SYMBOL_DAILY_"
                                + request.adjustment_type.value
                            ),
                            "attempt": attempt,
                            "request_started_at": request_started,
                            "request_completed_at": request_completed,
                            "status": audit_status,
                            "record_count": len(records),
                            "latency_ms": round(
                                (perf_counter() - request_timer) * 1000
                            ),
                            "rate_limited": False,
                            "request_hash": stable_hash(audit_identity),
                            "request_parameters": {
                                "symbol": item["symbol"],
                                "start_date": start_date,
                                "end_date": end_date,
                                "adjustment_type": (
                                    request.adjustment_type.value
                                ),
                            },
                        }
                    )
                    break
                except Exception as exc:
                    request_completed = self.clock()
                    limited = _is_rate_limit(exc)
                    rate_limited = rate_limited or limited
                    error = {
                        "provider": provider_name,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                    }
                    errors.append(error)
                    audit_identity = {
                        "run_id": run_id,
                        "symbol": item["symbol"],
                        "provider": provider_name,
                        "attempt": attempt,
                        "started": request_started,
                    }
                    audits.append(
                        {
                            "audit_id": (
                                "provider_"
                                + stable_hash(audit_identity)[:32]
                            ),
                            "run_id": run_id,
                            "shard_id": item["shard_id"],
                            "symbol": item["symbol"],
                            "provider": provider_name,
                            "capability": (
                                "PER_SYMBOL_DAILY_"
                                + request.adjustment_type.value
                            ),
                            "attempt": attempt,
                            "request_started_at": request_started,
                            "request_completed_at": request_completed,
                            "status": (
                                "RATE_LIMITED" if limited else "FAILED"
                            ),
                            "record_count": 0,
                            "latency_ms": round(
                                (perf_counter() - request_timer) * 1000
                            ),
                            "rate_limited": limited,
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:500],
                            "request_hash": stable_hash(audit_identity),
                            "request_parameters": {
                                "symbol": item["symbol"],
                                "start_date": start_date,
                                "end_date": end_date,
                                "adjustment_type": (
                                    request.adjustment_type.value
                                ),
                            },
                        }
                    )
                    if limited:
                        break
            if records or provider_used is not None or rate_limited:
                break
        return {
            "budget_exhausted": False,
            "rate_limited": rate_limited,
            "audits": audits,
            "errors": errors,
            "records": records,
            "provider_used": provider_used,
            "attempts": attempts,
            "request_count": request_count,
            "started_at": started_at,
            "elapsed": max(0.0, perf_counter() - started),
        }

    def _execute_existing(
        self,
        *,
        run_id: str,
        request: HistoryBackfillRequest,
        calendar_days: list[date],
        calendar_request_count: int,
    ) -> HistoryBackfillResponse:
        self.repository.mark_running(run_id, started_at=self.clock())
        remaining_capacity = max(
            0,
            request.request_budget - calendar_request_count,
        )
        pending = self.repository.pending_items(
            run_id,
            limit=min(request.batch_size, max(1, remaining_capacity)),
        )
        if not pending or remaining_capacity == 0:
            pause_status = (
                BackfillStatus.SUCCESS
                if not pending
                else BackfillStatus.PAUSED_BUDGET
            )
            self.repository.finalize_run(
                run_id,
                status=pause_status.value,
                completed_at=self.clock(),
                database_bytes_after=self.repository.database_bytes(),
                persisted_raw_count=0,
                persisted_canonical_count=0,
                error_summary=[],
                extra_request_count=calendar_request_count,
            )
            return self._response(run_id)

        start_date = request.start_date or calendar_days[0]
        end_date = request.end_date or calendar_days[-1]
        budget = {"used": calendar_request_count}
        budget_lock = threading.Lock()
        with ThreadPoolExecutor(
            max_workers=min(request.concurrency, len(pending)),
            thread_name_prefix="history-backfill",
        ) as executor:
            results = list(
                executor.map(
                    lambda item: self._fetch_one(
                        run_id=run_id,
                        item=item,
                        request=request,
                        start_date=start_date,
                        end_date=end_date,
                        budget=budget,
                        budget_lock=budget_lock,
                    ),
                    pending,
                )
            )

        generated_at = self.clock()
        shard_payloads: dict[str, dict[str, list[Any]]] = {}
        item_updates: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        saw_rate_limit = False
        for item, result in zip(pending, results, strict=True):
            errors.extend(result["errors"])
            saw_rate_limit = saw_rate_limit or result["rate_limited"]
            if result["budget_exhausted"]:
                continue
            normalized_bars: list[dict[str, Any]] = []
            normalization_error: Exception | None = None
            try:
                for record in result["records"]:
                    normalized_bars.append(
                        self._normalize_record(
                            record,
                            adjustment_type=request.adjustment_type,
                            generated_at=generated_at,
                        )
                    )
            except Exception as exc:
                normalization_error = exc
                errors.append(
                    {
                        "symbol": item["symbol"],
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                    }
                )
            if normalization_error is not None:
                status = "FAILED"
            elif result["provider_used"] is None:
                status = "PENDING" if result["rate_limited"] else "FAILED"
            elif not result["records"]:
                status = "SKIPPED_EMPTY_RESULT"
            elif len(result["records"]) >= item["expected_trading_days"]:
                status = "SUCCESS"
            else:
                status = "SUCCESS_PARTIAL_HISTORY"
            error = result["errors"][-1] if result["errors"] else {}
            update = {
                "run_id": run_id,
                "symbol": item["symbol"],
                "observed_trading_days": len(result["records"]),
                "provider_used": result["provider_used"],
                "status": status,
                "attempt_count": result["attempts"],
                "request_count": result["request_count"],
                "raw_record_count": len(result["records"]),
                "canonical_record_count": len(normalized_bars),
                "fetch_elapsed_seconds": result["elapsed"],
                "write_elapsed_seconds": 0.0,
                "started_at": result["started_at"],
                "completed_at": generated_at,
                "error_type": (
                    type(normalization_error).__name__
                    if normalization_error
                    else error.get("error_type")
                ),
                "error_message": (
                    str(normalization_error)[:500]
                    if normalization_error
                    else error.get("error_message")
                ),
                "payload": {
                    "provider_errors": result["errors"],
                    "data_cutoff_semantics": (
                        "provider fetch time; never backdated"
                    ),
                },
            }
            item_updates.append(update)
            payload = shard_payloads.setdefault(
                item["shard_id"],
                {
                    "raw_records": [],
                    "historical_bars": [],
                    "item_updates": [],
                    "request_audits": [],
                },
            )
            if normalization_error is None:
                payload["raw_records"].extend(result["records"])
                payload["historical_bars"].extend(normalized_bars)
            payload["item_updates"].append(update)
            payload["request_audits"].extend(result["audits"])

        raw_inserted = 0
        bars_inserted = 0
        shard_write_failed = False
        for shard_id, payload in shard_payloads.items():
            try:
                inserted_raw, inserted_bars, _ = (
                    self.repository.persist_batch(
                        raw_records=payload["raw_records"],
                        historical_bars=payload["historical_bars"],
                        item_updates=payload["item_updates"],
                        request_audits=payload["request_audits"],
                    )
                )
                raw_inserted += inserted_raw
                bars_inserted += inserted_bars
            except Exception as exc:
                shard_write_failed = True
                symbols = [
                    item["symbol"] for item in payload["item_updates"]
                ]
                errors.append(
                    {
                        "shard_id": shard_id,
                        "symbols": symbols,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:500],
                    }
                )
                self.repository.mark_items_failed(
                    run_id=run_id,
                    symbols=symbols,
                    completed_at=self.clock(),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        remaining = self.repository.pending_items(run_id, limit=1)
        if saw_rate_limit:
            status = BackfillStatus.PAUSED_RATE_LIMIT
        elif remaining:
            status = BackfillStatus.PAUSED_BUDGET
        elif shard_write_failed or any(
            item["status"] == "FAILED" for item in item_updates
        ):
            status = BackfillStatus.PARTIAL
        else:
            status = BackfillStatus.SUCCESS
        self.repository.finalize_run(
            run_id,
            status=status.value,
            completed_at=self.clock(),
            database_bytes_after=self.repository.database_bytes(),
            persisted_raw_count=raw_inserted,
            persisted_canonical_count=bars_inserted,
            error_summary=errors,
            extra_request_count=calendar_request_count,
        )
        return self._response(run_id)

    def run(self, request: HistoryBackfillRequest) -> HistoryBackfillResponse:
        if request.shard_type == ShardType.TRADE_DATE_SHARD:
            return self._trade_date_service().run(request)
        free_bytes = 0
        try:
            free_bytes = shutil.disk_usage(
                self.repository.database_path.parent
            ).free
        except OSError:
            free_bytes = 0
        if free_bytes < request.minimum_free_bytes:
            raise RuntimeError(
                "insufficient disk budget for historical backfill"
            )
        (
            planned_items,
            shards,
            stocks,
            days,
            calendar_requests,
            warnings,
        ) = self._plan(
            request,
            persist_calendar=not request.dry_run,
        )
        started_at = self.clock()
        run_id = shards[0]["shard_id"].rsplit("_shard_", 1)[0] if shards else (
            "history_"
            + stable_hash(
                {
                    "request": request.model_dump(mode="json"),
                    "started_at": started_at,
                }
            )[:24]
        )
        eligible = sum(
            item["eligibility_status"] == EligibilityStatus.ELIGIBLE.value
            for item in planned_items
        )
        skipped = len(planned_items) - eligible
        if request.dry_run:
            return HistoryBackfillResponse(
                run_id=run_id,
                mode="DRY_RUN",
                provider=request.provider,
                status=BackfillStatus.PENDING,
                requested_symbol_count=len(stocks),
                eligible_symbol_count=eligible,
                completed_symbol_count=0,
                successful_symbol_count=0,
                skipped_symbol_count=skipped,
                failed_symbol_count=0,
                request_count=calendar_requests,
                request_budget=request.request_budget,
                retry_count=0,
                resume_cursor=0,
                database_bytes_before=self.repository.database_bytes(),
                database_bytes_after=None,
                persisted_raw_count=0,
                persisted_canonical_count=0,
                started_at=started_at,
                completed_at=None,
                warnings=warnings,
                items=[
                    HistoryBackfillItem(
                        symbol=item["symbol"],
                        eligibility_status=item["eligibility_status"],
                        list_date=item["list_date"],
                        expected_trading_days=item["expected_trading_days"],
                        observed_trading_days=0,
                        provider_used=None,
                        status=item["status"],
                        attempt_count=0,
                        request_count=0,
                        raw_record_count=0,
                        canonical_record_count=0,
                        fetch_elapsed_seconds=0,
                        write_elapsed_seconds=0,
                        error_message=item.get("error_message"),
                    )
                    for item in planned_items
                ],
            )
        self.repository.create_run(
            run={
                "run_id": run_id,
                "provider": request.provider,
                "fallback_providers": request.fallback_providers,
                "selection_strategy": request.selection_strategy.value,
                "requested_symbols": [
                    item["symbol"] for item in planned_items
                ],
                "requested_date_range": {
                    "start_date": days[0],
                    "end_date": days[-1],
                },
                "target_trading_days": request.target_trading_days,
                "adjustment_type": request.adjustment_type.value,
                "eligible_symbol_count": eligible,
                "initial_skipped_count": skipped,
                "request_budget": request.request_budget,
                "concurrency": request.concurrency,
                "max_retries": request.max_retries,
                "started_at": started_at,
                "database_bytes_before": self.repository.database_bytes(),
                "report_path": request.report_path,
                "request": request.model_dump(mode="json"),
            },
            shards=shards,
            items=planned_items,
        )
        return self._execute_existing(
            run_id=run_id,
            request=request,
            calendar_days=days,
            calendar_request_count=calendar_requests,
        )

    def resume(
        self,
        run_id: str,
        action: HistoryRunActionRequest,
    ) -> HistoryBackfillResponse:
        detail = self.repository.run_detail(run_id)
        if detail is None:
            raise KeyError(run_id)
        if detail["run"]["selection_strategy"] == "TRADE_DATE":
            return self._trade_date_service().resume(run_id, action)
        if not action.apply:
            return self._response(run_id)
        if detail["run"]["status"] == BackfillStatus.CANCELLED.value:
            raise ValueError("cancelled history run cannot be resumed")
        request_payload = dict(detail["run"]["request_json"])
        request_payload.update(
            {
                "dry_run": False,
                "resume": True,
                "data_cutoff": action.data_cutoff,
            }
        )
        if action.request_budget is not None:
            request_payload["request_budget"] = action.request_budget
        request = HistoryBackfillRequest.model_validate(request_payload)
        days, calendar_requests = self._calendar_days(
            request,
            persist=True,
        )
        return self._execute_existing(
            run_id=run_id,
            request=request,
            calendar_days=days,
            calendar_request_count=calendar_requests,
        )

    def cancel(
        self,
        run_id: str,
        action: HistoryRunActionRequest,
    ) -> HistoryRunDetail:
        if action.apply:
            if not self.repository.cancel(run_id, self.clock()):
                detail = self.repository.run_detail(run_id)
                if detail is None:
                    raise KeyError(run_id)
        return self.get(run_id)

    def get(self, run_id: str) -> HistoryRunDetail:
        detail = self.repository.run_detail(run_id)
        if detail is None:
            raise KeyError(run_id)
        return HistoryRunDetail.model_validate(detail)

    def _trade_date_service(self) -> Any:
        from data_hub.services.trade_date_history_service import (
            TradeDateHistoryBackfillService,
        )

        return TradeDateHistoryBackfillService(
            repository=self.repository,
            provider=self.providers.get("TUSHARE"),
            app_settings=self.settings,
            clock=self.clock,
        )

    def _response(self, run_id: str) -> HistoryBackfillResponse:
        detail = self.repository.run_detail(run_id)
        if detail is None:
            raise KeyError(run_id)
        run = detail["run"]
        items = detail["items"]
        return HistoryBackfillResponse(
            run_id=run_id,
            mode=run["mode"],
            provider=run["provider"],
            status=run["status"],
            requested_symbol_count=len(run["requested_symbols_json"]),
            eligible_symbol_count=int(run["eligible_symbol_count"]),
            completed_symbol_count=int(run["completed_symbol_count"]),
            successful_symbol_count=int(run["successful_symbol_count"]),
            skipped_symbol_count=int(run["skipped_symbol_count"]),
            failed_symbol_count=int(run["failed_symbol_count"]),
            request_count=int(run["request_count"]),
            request_budget=int(run["request_budget"]),
            retry_count=int(run["retry_count"]),
            resume_cursor=int(run["resume_cursor"]),
            database_bytes_before=int(run["database_bytes_before"]),
            database_bytes_after=run["database_bytes_after"],
            persisted_raw_count=int(run["persisted_raw_count"]),
            persisted_canonical_count=int(
                run["persisted_canonical_count"]
            ),
            started_at=run["started_at"],
            completed_at=run["completed_at"],
            warnings=[],
            items=[
                HistoryBackfillItem(
                    symbol=item["symbol"],
                    eligibility_status=item["eligibility_status"],
                    list_date=item["list_date"],
                    expected_trading_days=int(
                        item["expected_trading_days"]
                    ),
                    observed_trading_days=int(
                        item["observed_trading_days"]
                    ),
                    provider_used=item["provider_used"],
                    status=item["status"],
                    attempt_count=int(item["attempt_count"]),
                    request_count=int(item["request_count"]),
                    raw_record_count=int(item["raw_record_count"]),
                    canonical_record_count=int(
                        item["canonical_record_count"]
                    ),
                    fetch_elapsed_seconds=float(
                        item["fetch_elapsed_seconds"] or 0
                    ),
                    write_elapsed_seconds=float(
                        item["write_elapsed_seconds"] or 0
                    ),
                    error_type=item["error_type"],
                    error_message=item["error_message"],
                )
                for item in items
            ],
        )


__all__ = ["HISTORY_ALGORITHM_VERSION", "HistoryBackfillService"]
