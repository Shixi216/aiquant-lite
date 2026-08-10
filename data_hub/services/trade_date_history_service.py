from __future__ import annotations

import shutil
from datetime import date, datetime, time
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import Settings, settings
from data_hub.providers.tushare_provider import (
    TushareProvider,
    classify_tushare_error,
)
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.history import (
    BackfillStatus,
    HistoryBackfillRequest,
    HistoryBackfillResponse,
    HistoryRunActionRequest,
    HistoryTradeDateShard,
    ShardType,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.full_market_common import (
    as_float,
    is_a_share_symbol,
    normalize_symbol,
    parse_date,
    stable_hash,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
TUSHARE_DAILY_NORMALIZATION_VERSION = "tushare-daily-raw-units-v1"
TUSHARE_DATE_SHARD_ALGORITHM_VERSION = "trade-date-history-shard-v1"
MINIMUM_COVERAGE_RATIO = 0.98
MAXIMUM_INVALID_RATIO = 0.01
MAXIMUM_CONFLICT_RATIO = 0.005


def _valid_ohlc(
    open_value: float,
    high: float,
    low: float,
    close: float,
) -> bool:
    return (
        min(open_value, high, low, close) > 0
        and high >= max(open_value, low, close)
        and low <= min(open_value, high, close)
    )


class TradeDateHistoryBackfillService:
    def __init__(
        self,
        *,
        repository: HistoryRepository | None = None,
        provider: Any | None = None,
        app_settings: Settings = settings,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or HistoryRepository()
        self.settings = app_settings
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.provider = provider
        if self.provider is None:
            if not (
                app_settings.tushare_token
                and app_settings.tushare_token.strip()
            ):
                raise RuntimeError("Tushare provider is not configured")
            self.provider = TushareProvider()

    def _resolve_dates(
        self,
        request: HistoryBackfillRequest,
    ) -> list[date]:
        latest = self.repository.latest_completed_trade_date(
            request.data_cutoff
        )
        if latest is None:
            raise RuntimeError("no audited completed trading date is available")
        if request.trade_dates:
            requested = sorted(set(request.trade_dates))
        elif request.start_trade_date or request.end_trade_date:
            start = request.start_trade_date or request.end_trade_date
            end = request.end_trade_date or request.start_trade_date
            if start is None or end is None:
                raise ValueError("trade-date range is incomplete")
            requested = self.repository.trading_days(
                data_cutoff=request.data_cutoff,
                start_date=start,
                end_date=end,
            )
        else:
            requested = self.repository.trading_days(
                data_cutoff=request.data_cutoff,
                end_date=latest,
                limit=request.target_trading_days,
            )
        if not requested:
            raise RuntimeError("no audited trading dates match the request")
        if any(item > latest for item in requested):
            raise ValueError("future or incomplete trading dates are forbidden")
        audited = set(
            self.repository.trading_days(
                data_cutoff=request.data_cutoff,
                start_date=requested[0],
                end_date=requested[-1],
            )
        )
        missing = [item for item in requested if item not in audited]
        if missing:
            raise ValueError("all requested dates must be audited trading days")
        return requested

    @staticmethod
    def _expected_symbols(
        universe: dict[str, dict[str, Any]],
        trade_date: date,
    ) -> set[str]:
        return {
            symbol
            for symbol, context in universe.items()
            if (
                context["list_date"] is None
                or context["list_date"] <= trade_date
            )
            and (
                context.get("delist_date") is None
                or context["delist_date"] >= trade_date
            )
        }

    def _raw_record(
        self,
        *,
        raw: dict[str, Any],
        requested_date: date,
        fetched_at: datetime,
    ) -> MarketRecord:
        normalized_symbol = normalize_symbol(raw.get("ts_code"))
        symbol = normalized_symbol or str(raw.get("ts_code") or "UNKNOWN")
        identity = {
            "provider": "TUSHARE",
            "capability": "BATCH_DAILY_BY_TRADE_DATE",
            "payload": raw,
        }
        digest = stable_hash(identity)
        return MarketRecord(
            record_id=f"raw_tushare_daily_{digest[:32]}",
            symbol=symbol,
            data_type=DataType.DAILY_BAR,
            event_time=datetime.combine(
                requested_date,
                time(hour=15),
                tzinfo=SHANGHAI_TZ,
            ),
            fetched_at=fetched_at,
            source_name="Tushare Pro",
            source_level=SourceLevel.STRUCTURED,
            verified=False,
            content_hash=digest,
            data=raw,
        )

    def _canonical_bar(
        self,
        *,
        record: MarketRecord,
        raw: dict[str, Any],
        requested_date: date,
        generated_at: datetime,
    ) -> tuple[dict[str, Any] | None, str | None]:
        symbol = normalize_symbol(raw.get("ts_code"))
        if symbol is None or not is_a_share_symbol(symbol):
            return None, "INVALID_SYMBOL"
        observed_date = parse_date(raw.get("trade_date"))
        if observed_date != requested_date:
            return None, "TRADE_DATE_MISMATCH"
        values = {
            field: as_float(raw.get(field))
            for field in (
                "open",
                "high",
                "low",
                "close",
                "vol",
                "amount",
            )
        }
        if any(value is None for value in values.values()):
            return None, "INVALID_NUMBER"
        open_value = float(values["open"])
        high = float(values["high"])
        low = float(values["low"])
        close = float(values["close"])
        volume_lots = float(values["vol"])
        amount_thousand_cny = float(values["amount"])
        if not _valid_ohlc(open_value, high, low, close):
            return None, "INVALID_OHLC"
        if volume_lots < 0 or amount_thousand_cny < 0:
            return None, "NEGATIVE_VOLUME_OR_AMOUNT"
        volume = volume_lots * 100
        amount = amount_thousand_cny * 1000
        normalized_payload = {
            "symbol": symbol,
            "trade_date": requested_date,
            "adjustment_type": "RAW",
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "volume_unit": "SHARES",
            "amount_unit": "CNY",
        }
        value_hash = stable_hash(normalized_payload)
        data_available_time = datetime.combine(
            requested_date,
            time(hour=16),
            tzinfo=SHANGHAI_TZ,
        )
        return (
            {
                "bar_id": f"hbar_{value_hash[:32]}",
                "symbol": symbol,
                "trade_date": requested_date,
                "event_time": record.event_time,
                "data_available_time": data_available_time,
                "data_cutoff": data_available_time,
                "generated_at": generated_at,
                "adjustment_type": "RAW",
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
                "volume_unit": "SHARES",
                "amount_unit": "CNY",
                "primary_source": "Tushare Pro",
                "source_record_ids": [record.record_id],
                "verification_source_ids": [],
                "verification_status": "SINGLE_SOURCE",
                "confidence": 0.5,
                "content_hash": value_hash,
                "algorithm_version": (
                    TUSHARE_DAILY_NORMALIZATION_VERSION
                ),
                "raw_payload": raw,
            },
            None,
        )

    def _normalize(
        self,
        *,
        rows: list[dict[str, Any]],
        requested_date: date,
        fetched_at: datetime,
    ) -> tuple[
        list[MarketRecord],
        list[dict[str, Any]],
        dict[str, int],
        dict[str, int],
    ]:
        raw_records: list[MarketRecord] = []
        bars: list[dict[str, Any]] = []
        errors: dict[str, int] = {}
        markets = {"BJ": 0, "SH": 0, "SZ": 0}
        seen: set[str] = set()
        for raw in rows:
            record = self._raw_record(
                raw=raw,
                requested_date=requested_date,
                fetched_at=fetched_at,
            )
            raw_records.append(record)
            bar, error = self._canonical_bar(
                record=record,
                raw=raw,
                requested_date=requested_date,
                generated_at=fetched_at,
            )
            if error is not None:
                errors[error] = errors.get(error, 0) + 1
                continue
            if bar is None:
                errors["INVALID_RECORD"] = (
                    errors.get("INVALID_RECORD", 0) + 1
                )
                continue
            if bar["symbol"] in seen:
                errors["DUPLICATE_SYMBOL"] = (
                    errors.get("DUPLICATE_SYMBOL", 0) + 1
                )
                continue
            seen.add(bar["symbol"])
            markets[bar["symbol"].rsplit(".", 1)[1]] += 1
            bars.append(bar)
        return raw_records, bars, errors, markets

    def _audit(
        self,
        *,
        run_id: str,
        shard_id: str,
        trade_date: date,
        attempt: int,
        started_at: datetime,
        completed_at: datetime,
        latency_ms: int,
        status: str,
        record_count: int,
        error_code: str | None = None,
        exception_type: str | None = None,
        sanitized_error: str | None = None,
    ) -> dict[str, Any]:
        identity = {
            "run_id": run_id,
            "trade_date": trade_date,
            "attempt": attempt,
            "started_at": started_at,
        }
        return {
            "audit_id": f"provider_{stable_hash(identity)[:32]}",
            "provider": "TUSHARE",
            "capability": "BATCH_DAILY_BY_TRADE_DATE",
            "attempt": attempt,
            "request_started_at": started_at,
            "request_completed_at": completed_at,
            "status": status,
            "record_count": record_count,
            "latency_ms": latency_ms,
            "rate_limited": status == "RATE_LIMITED",
            "error_type": exception_type,
            "error_message": sanitized_error,
            "failure_code": error_code,
            "request_hash": stable_hash(identity),
            "request_parameters": {
                "provider_parameters": {
                    "trade_date": trade_date.strftime("%Y%m%d"),
                },
                "internal_context": {
                    "adjustment_type": "RAW",
                    "shard_id": shard_id,
                },
            },
        }

    def _fetch_date(
        self,
        *,
        run_id: str,
        shard: dict[str, Any],
        request: HistoryBackfillRequest,
        remaining_budget: int,
    ) -> dict[str, Any]:
        audits: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] | None = None
        last_error: Exception | None = None
        attempts_allowed = min(
            request.max_retries + 1,
            remaining_budget,
        )
        for attempt in range(1, attempts_allowed + 1):
            request_started = self.clock()
            timer = perf_counter()
            try:
                rows = self.provider.get_daily_by_trade_date(
                    shard["trade_date"]
                )
                completed = self.clock()
                latency_ms = round((perf_counter() - timer) * 1000)
                audits.append(
                    self._audit(
                        run_id=run_id,
                        shard_id=shard["shard_id"],
                        trade_date=shard["trade_date"],
                        attempt=attempt,
                        started_at=request_started,
                        completed_at=completed,
                        latency_ms=latency_ms,
                        status="SUCCESS" if rows else "SUCCESS_EMPTY",
                        record_count=len(rows),
                    )
                )
                break
            except Exception as exc:
                last_error = exc
                completed = self.clock()
                code, reason = classify_tushare_error(exc)
                latency_ms = round((perf_counter() - timer) * 1000)
                audits.append(
                    self._audit(
                        run_id=run_id,
                        shard_id=shard["shard_id"],
                        trade_date=shard["trade_date"],
                        attempt=attempt,
                        started_at=request_started,
                        completed_at=completed,
                        latency_ms=latency_ms,
                        status=(
                            "RATE_LIMITED"
                            if code == "RATE_LIMITED"
                            else "FAILED"
                        ),
                        record_count=0,
                        error_code=code,
                        exception_type=type(exc).__name__,
                        sanitized_error=reason,
                    )
                )
                if code == "RATE_LIMITED":
                    break
        return {
            "rows": rows,
            "audits": audits,
            "error": last_error,
            "request_count": len(audits),
            "retry_count": max(0, len(audits) - 1),
            "latency_ms": sum(item["latency_ms"] for item in audits),
        }

    def _date_shard_model(
        self,
        row: dict[str, Any],
    ) -> HistoryTradeDateShard:
        return HistoryTradeDateShard(
            run_id=row["run_id"],
            shard_id=row["shard_id"],
            provider=row["provider"],
            trade_date=row["trade_date"],
            expected_universe_count=int(row["expected_universe_count"]),
            returned_record_count=int(row["returned_record_count"]),
            valid_record_count=int(row["valid_record_count"]),
            invalid_record_count=int(row["invalid_record_count"]),
            missing_symbol_count=int(row["missing_symbol_count"]),
            extra_symbol_count=int(row["extra_symbol_count"]),
            beijing_record_count=int(row["beijing_record_count"]),
            shanghai_record_count=int(row["shanghai_record_count"]),
            shenzhen_record_count=int(row["shenzhen_record_count"]),
            raw_inserted_count=int(row["raw_inserted_count"]),
            canonical_inserted_count=int(row["canonical_inserted_count"]),
            duplicate_count=int(row["duplicate_count"]),
            conflict_count=int(row["conflict_count"]),
            request_count=int(row["request_count"]),
            retry_count=int(row["retry_count"]),
            latency_ms=int(row["latency_ms"]),
            write_elapsed_seconds=float(
                row["write_elapsed_seconds"] or 0
            ),
            database_growth_bytes=int(row["database_growth_bytes"] or 0),
            coverage_ratio=float(row["coverage_ratio"]),
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error_code=row["error_code"],
            sanitized_error=row["sanitized_error"],
            payload=row.get("payload_json") or {},
        )

    def _response(self, run_id: str) -> HistoryBackfillResponse:
        detail = self.repository.run_detail(run_id)
        if detail is None:
            raise KeyError(run_id)
        run = detail["run"]
        shards = detail["date_shards"]
        completed_statuses = {
            "SUCCESS",
            "SUCCESS_PARTIAL",
            "SUCCESS_EMPTY",
            "FAILED",
            "PAUSED_RATE_LIMIT",
        }
        return HistoryBackfillResponse(
            run_id=run_id,
            mode=run["mode"],
            provider=run["provider"],
            status=run["status"],
            requested_symbol_count=0,
            eligible_symbol_count=0,
            completed_symbol_count=0,
            successful_symbol_count=0,
            skipped_symbol_count=0,
            failed_symbol_count=0,
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
            shard_type=ShardType.TRADE_DATE_SHARD,
            requested_trade_date_count=len(shards),
            completed_trade_date_count=sum(
                row["status"] in completed_statuses for row in shards
            ),
            date_shards=[
                self._date_shard_model(row) for row in shards
            ],
        )

    def _execute(
        self,
        *,
        run_id: str,
        request: HistoryBackfillRequest,
    ) -> HistoryBackfillResponse:
        self.repository.mark_trade_date_run_running(
            run_id,
            started_at=self.clock(),
        )
        budget_used = 0
        errors: list[dict[str, Any]] = []
        stop_status: BackfillStatus | None = None
        pending = self.repository.trade_date_shards(
            run_id,
            statuses=[
                "PENDING",
                "FAILED",
                "PAUSED_BUDGET",
                "PAUSED_RATE_LIMIT",
            ],
        )
        checkpoint = pending[: request.batch_size]
        universe = self.repository.active_universe_context(
            data_cutoff=request.data_cutoff
        )
        for shard in checkpoint:
            if budget_used >= request.request_budget:
                stop_status = BackfillStatus.PAUSED_BUDGET
                break
            started_at = self.clock()
            fetched = self._fetch_date(
                run_id=run_id,
                shard=shard,
                request=request,
                remaining_budget=request.request_budget - budget_used,
            )
            budget_used += fetched["request_count"]
            if fetched["rows"] is None:
                exc = fetched["error"] or RuntimeError(
                    "Tushare request did not complete"
                )
                code, reason = classify_tushare_error(exc)
                date_status = (
                    "PAUSED_RATE_LIMIT"
                    if code == "RATE_LIMITED"
                    else "FAILED"
                )
                self.repository.record_trade_date_failure(
                    run_id=run_id,
                    shard_id=shard["shard_id"],
                    status=date_status,
                    request_audits=fetched["audits"],
                    request_count=fetched["request_count"],
                    retry_count=fetched["retry_count"],
                    latency_ms=fetched["latency_ms"],
                    started_at=started_at,
                    completed_at=self.clock(),
                    error_code=code,
                    sanitized_error=reason,
                )
                errors.append(
                    {
                        "trade_date": shard["trade_date"],
                        "error_code": code,
                        "sanitized_error": reason,
                    }
                )
                stop_status = (
                    BackfillStatus.PAUSED_RATE_LIMIT
                    if code == "RATE_LIMITED"
                    else BackfillStatus.FAILED
                )
                break
            fetched_at = self.clock()
            raw_records, bars, validation_errors, markets = self._normalize(
                rows=fetched["rows"],
                requested_date=shard["trade_date"],
                fetched_at=fetched_at,
            )
            expected_symbols = self._expected_symbols(
                universe,
                shard["trade_date"],
            )
            valid_symbols = {bar["symbol"] for bar in bars}
            missing = expected_symbols - valid_symbols
            extra = valid_symbols - expected_symbols
            valid_in_universe = valid_symbols & expected_symbols
            current_suspended_missing = {
                symbol
                for symbol in missing
                if universe[symbol].get("is_suspended") is True
            }
            unknown_trading_status = missing - current_suspended_missing
            universe_versions = sorted(
                {
                    str(context["universe_version"])
                    for context in universe.values()
                    if context.get("universe_version")
                }
            )
            coverage_ratio = (
                len(valid_in_universe) / len(expected_symbols)
                if expected_symbols
                else 0.0
            )
            invalid_count = sum(validation_errors.values())
            invalid_ratio = (
                invalid_count / len(fetched["rows"])
                if fetched["rows"]
                else 0.0
            )
            if not fetched["rows"]:
                shard_status = "SUCCESS_EMPTY"
            elif (
                coverage_ratio >= MINIMUM_COVERAGE_RATIO
                and invalid_ratio <= MAXIMUM_INVALID_RATIO
            ):
                shard_status = "SUCCESS"
            else:
                shard_status = "SUCCESS_PARTIAL"
            completed_at = self.clock()
            try:
                persisted = self.repository.persist_trade_date_batch(
                    run_id=run_id,
                    shard_id=shard["shard_id"],
                    raw_records=raw_records,
                    historical_bars=bars,
                    request_audits=fetched["audits"],
                    shard_metrics={
                        "trade_date": shard["trade_date"],
                        "adjustment_type": "RAW",
                        "expected_universe_count": len(expected_symbols),
                        "returned_record_count": len(fetched["rows"]),
                        "valid_record_count": len(bars),
                        "invalid_record_count": invalid_count,
                        "missing_symbol_count": len(missing),
                        "extra_symbol_count": len(extra),
                        "beijing_record_count": markets["BJ"],
                        "shanghai_record_count": markets["SH"],
                        "shenzhen_record_count": markets["SZ"],
                        "request_count": fetched["request_count"],
                        "retry_count": fetched["retry_count"],
                        "latency_ms": fetched["latency_ms"],
                        "coverage_ratio": coverage_ratio,
                        "status": shard_status,
                        "started_at": started_at,
                        "completed_at": completed_at,
                        "payload": {
                            "normalization_version": (
                                TUSHARE_DAILY_NORMALIZATION_VERSION
                            ),
                            "algorithm_version": (
                                TUSHARE_DATE_SHARD_ALGORITHM_VERSION
                            ),
                            "volume_conversion": "LOTS_TO_SHARES_X100",
                            "amount_conversion": (
                                "THOUSAND_CNY_TO_CNY_X1000"
                            ),
                            "pct_chg_unit": "PERCENT",
                            "validation_errors": validation_errors,
                            "active_universe_count": sum(
                                context.get("listing_status") == "ACTIVE"
                                for context in universe.values()
                            ),
                            "historical_universe_context_count": len(
                                universe
                            ),
                            "historical_universe_rule": (
                                "LIST_DATE_LE_TRADE_DATE_AND_"
                                "DELIST_DATE_GE_TRADE_DATE"
                            ),
                            "universe_versions": universe_versions,
                            "missing_status_counts": {
                                "current_snapshot_suspended": len(
                                    current_suspended_missing
                                ),
                                "unknown_trading_status": len(
                                    unknown_trading_status
                                ),
                            },
                            "valid_in_universe_count": (
                                len(valid_in_universe)
                            ),
                        },
                    },
                )
            except Exception as exc:
                code = "DATE_TRANSACTION_FAILED"
                reason = "Date-shard transaction failed and was rolled back"
                self.repository.record_trade_date_failure(
                    run_id=run_id,
                    shard_id=shard["shard_id"],
                    status="FAILED",
                    request_audits=fetched["audits"],
                    request_count=fetched["request_count"],
                    retry_count=fetched["retry_count"],
                    latency_ms=fetched["latency_ms"],
                    started_at=started_at,
                    completed_at=self.clock(),
                    error_code=code,
                    sanitized_error=reason,
                )
                errors.append(
                    {
                        "trade_date": shard["trade_date"],
                        "error_code": code,
                        "sanitized_error": reason,
                    }
                )
                stop_status = BackfillStatus.FAILED
                break
            conflict_ratio = (
                persisted["conflict_count"] / len(bars) if bars else 0.0
            )
            if (
                persisted["status"] != "SUCCESS"
                or coverage_ratio < MINIMUM_COVERAGE_RATIO
                or invalid_ratio > MAXIMUM_INVALID_RATIO
                or conflict_ratio > MAXIMUM_CONFLICT_RATIO
            ):
                errors.append(
                    {
                        "trade_date": shard["trade_date"],
                        "error_code": "DATE_ACCEPTANCE_THRESHOLD_FAILED",
                        "sanitized_error": (
                            "Date-shard acceptance threshold was not met"
                        ),
                    }
                )
                stop_status = BackfillStatus.PARTIAL
                break
        remaining = self.repository.trade_date_shards(
            run_id,
            statuses=[
                "PENDING",
                "FAILED",
                "PAUSED_BUDGET",
                "PAUSED_RATE_LIMIT",
            ],
        )
        if stop_status is None:
            stop_status = (
                BackfillStatus.PAUSED_BUDGET
                if remaining
                else BackfillStatus.SUCCESS
            )
        self.repository.finalize_trade_date_run(
            run_id,
            completed_at=self.clock(),
            database_bytes_after=self.repository.database_bytes(),
            status=stop_status.value,
            errors=errors,
        )
        return self._response(run_id)

    def run(
        self,
        request: HistoryBackfillRequest,
    ) -> HistoryBackfillResponse:
        if request.shard_type != ShardType.TRADE_DATE_SHARD:
            raise ValueError("trade-date service requires TRADE_DATE_SHARD")
        free_bytes = shutil.disk_usage(
            self.repository.database_path.parent
        ).free
        if free_bytes < request.minimum_free_bytes:
            raise RuntimeError(
                "insufficient disk budget for trade-date backfill"
            )
        trade_dates = self._resolve_dates(request)
        universe = self.repository.active_universe_context(
            data_cutoff=request.data_cutoff
        )
        started_at = self.clock()
        run_id = (
            "history_date_"
            + stable_hash(
                {
                    "request": request.model_dump(mode="json"),
                    "started_at": started_at,
                }
            )[:24]
        )
        if request.dry_run:
            successful = self.repository.successful_trade_date_shards(
                provider="TUSHARE",
                adjustment_type="RAW",
                trade_dates=trade_dates,
            )
            date_shards = [
                HistoryTradeDateShard(
                    run_id=run_id,
                    shard_id=f"{run_id}_date_{item:%Y%m%d}",
                    provider="TUSHARE",
                    trade_date=item,
                    expected_universe_count=(
                        int(successful[item]["expected_universe_count"])
                        if item in successful
                        else len(self._expected_symbols(universe, item))
                    ),
                    returned_record_count=(
                        int(successful[item]["returned_record_count"])
                        if item in successful
                        else 0
                    ),
                    valid_record_count=(
                        int(successful[item]["valid_record_count"])
                        if item in successful
                        else 0
                    ),
                    invalid_record_count=(
                        int(successful[item]["invalid_record_count"])
                        if item in successful
                        else 0
                    ),
                    missing_symbol_count=(
                        int(successful[item]["missing_symbol_count"])
                        if item in successful
                        else len(self._expected_symbols(universe, item))
                    ),
                    extra_symbol_count=(
                        int(successful[item]["extra_symbol_count"])
                        if item in successful
                        else 0
                    ),
                    beijing_record_count=(
                        int(successful[item]["beijing_record_count"])
                        if item in successful
                        else 0
                    ),
                    shanghai_record_count=(
                        int(successful[item]["shanghai_record_count"])
                        if item in successful
                        else 0
                    ),
                    shenzhen_record_count=(
                        int(successful[item]["shenzhen_record_count"])
                        if item in successful
                        else 0
                    ),
                    raw_inserted_count=0,
                    canonical_inserted_count=0,
                    duplicate_count=0,
                    conflict_count=0,
                    request_count=0,
                    retry_count=0,
                    latency_ms=0,
                    write_elapsed_seconds=0,
                    database_growth_bytes=0,
                    coverage_ratio=(
                        float(successful[item]["coverage_ratio"])
                        if item in successful
                        else 0
                    ),
                    status=(
                        "SUCCESS"
                        if item in successful
                        else "PENDING"
                    ),
                    payload=(
                        {
                            **successful[item]["payload_json"],
                            "skipped_existing_success": True,
                        }
                        if item in successful
                        else {}
                    ),
                )
                for item in trade_dates
            ]
            return HistoryBackfillResponse(
                run_id=run_id,
                mode="DRY_RUN",
                provider="TUSHARE",
                status=BackfillStatus.PENDING,
                requested_symbol_count=0,
                eligible_symbol_count=0,
                completed_symbol_count=0,
                successful_symbol_count=0,
                skipped_symbol_count=0,
                failed_symbol_count=0,
                request_count=0,
                request_budget=request.request_budget,
                retry_count=0,
                resume_cursor=0,
                database_bytes_before=self.repository.database_bytes(),
                database_bytes_after=None,
                persisted_raw_count=0,
                persisted_canonical_count=0,
                started_at=started_at,
                completed_at=None,
                shard_type=ShardType.TRADE_DATE_SHARD,
                requested_trade_date_count=len(trade_dates),
                completed_trade_date_count=len(successful),
                date_shards=date_shards,
            )
        self.repository.create_trade_date_run(
            run={
                "run_id": run_id,
                "provider": "TUSHARE",
                "requested_date_range": {
                    "start_trade_date": trade_dates[0],
                    "end_trade_date": trade_dates[-1],
                    "trade_dates": trade_dates,
                },
                "adjustment_type": "RAW",
                "request_budget": request.request_budget,
                "max_retries": request.max_retries,
                "started_at": started_at,
                "database_bytes_before": self.repository.database_bytes(),
                "report_path": request.report_path,
                "request": request.model_dump(mode="json"),
            },
            trade_dates=trade_dates,
            expected_universe_count=len(universe),
            force_fetch=request.verify_idempotency,
        )
        return self._execute(run_id=run_id, request=request)

    def resume(
        self,
        run_id: str,
        action: HistoryRunActionRequest,
    ) -> HistoryBackfillResponse:
        if not action.apply:
            return self._response(run_id)
        detail = self.repository.run_detail(run_id)
        if detail is None:
            raise KeyError(run_id)
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
        if action.verify_idempotency:
            requested_dates = [
                row["trade_date"] for row in detail["date_shards"]
            ]
            request = request.model_copy(
                update={
                    "trade_dates": requested_dates,
                    "start_trade_date": None,
                    "end_trade_date": None,
                    "verify_idempotency": True,
                    "resume": False,
                }
            )
            return self.run(request)
        return self._execute(run_id=run_id, request=request)


__all__ = [
    "MAXIMUM_CONFLICT_RATIO",
    "MAXIMUM_INVALID_RATIO",
    "MINIMUM_COVERAGE_RATIO",
    "TUSHARE_DAILY_NORMALIZATION_VERSION",
    "TradeDateHistoryBackfillService",
]
