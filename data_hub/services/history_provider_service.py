from __future__ import annotations

from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Any

import akshare as ak
import baostock as bs

from config.settings import Settings, settings
from data_hub.providers import AKShareProvider, BaoStockProvider
from data_hub.providers.tushare_provider import (
    TushareProvider,
    classify_tushare_error,
)
from data_hub.providers.full_market import _query_to_frame
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.history import (
    ProviderVerificationResponse,
    ProviderVerificationResult,
)
from data_hub.services.full_market_common import stable_hash


class TradingCalendarService:
    def __init__(
        self,
        *,
        repository: HistoryRepository | None = None,
        calendar_fetcher: Any | None = None,
        akshare_calendar_fetcher: Any | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or HistoryRepository()
        self.calendar_fetcher = calendar_fetcher
        self.akshare_calendar_fetcher = akshare_calendar_fetcher
        self.clock = clock or (lambda: datetime.now().astimezone())
        self.last_provider: str | None = None
        self.last_metadata: dict[str, Any] = {}

    @staticmethod
    def _fetch_baostock(start_date: date, end_date: date) -> list[dict[str, Any]]:
        login = bs.login()
        if login.error_code != "0":
            raise RuntimeError(
                f"BaoStock login failed: {login.error_code} {login.error_msg}"
            )
        try:
            frame = _query_to_frame(
                bs.query_trade_dates(
                    start_date=start_date.isoformat(),
                    end_date=end_date.isoformat(),
                )
            )
        finally:
            bs.logout()
        return frame.to_dict(orient="records")

    @staticmethod
    def _fetch_akshare(start_date: date, end_date: date) -> list[dict[str, Any]]:
        import akshare as ak
        frame = ak.tool_trade_date_hist_sina()
        column = "trade_date"
        if column not in frame.columns:
            raise RuntimeError("AKShare calendar response lacks trade_date")
        trading_dates = {
            value.date()
            for value in frame[column]
            if start_date <= value.date() <= end_date
        }
        rows: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            rows.append(
                {
                    "calendar_date": current.isoformat(),
                    "is_trading_day": "1" if current in trading_dates else "0",
                }
            )
            current += timedelta(days=1)
        return rows

    def fetch(
        self,
        *,
        start_date: date,
        end_date: date,
        data_cutoff: datetime,
        persist: bool,
    ) -> tuple[list[date], int, int]:
        local_cutoff = data_cutoff.astimezone()
        completed_date = local_cutoff.date()
        if (
            local_cutoff.hour
            < settings.history_market_close_available_hour
        ):
            completed_date -= timedelta(days=1)
        if end_date > completed_date:
            raise ValueError("calendar end_date is not completed at data_cutoff")
        if start_date > end_date:
            raise ValueError("calendar start_date exceeds end_date")
        fetched_at = self.clock()
        fetchers: list[tuple[str, Any, dict[str, Any]]]
        if self.calendar_fetcher is not None:
            fetchers = [
                (
                    "BaoStock",
                    self.calendar_fetcher,
                    {"endpoint": "custom_calendar_fetcher"},
                )
            ]
        else:
            fetchers = [
                (
                    "BaoStock",
                    self._fetch_baostock,
                    {
                        "endpoint": "query_trade_dates",
                        "provider_version": getattr(bs, "__version__", "unknown"),
                    },
                ),
                (
                    "AKShare",
                    self.akshare_calendar_fetcher or self._fetch_akshare,
                    {
                        "endpoint": "tool_trade_date_hist_sina",
                        "provider_version": getattr(ak, "__version__", "unknown"),
                        "non_trading_semantics": (
                            "derived as complement of provider trading dates"
                        ),
                    },
                ),
            ]
        raw_rows: list[dict[str, Any]] | None = None
        request_count = 0
        errors: list[str] = []
        provider_name = ""
        provider_metadata: dict[str, Any] = {}
        for name, fetcher, metadata in fetchers:
            request_count += 1
            try:
                candidate = fetcher(start_date, end_date)
                if not candidate:
                    raise RuntimeError("calendar provider returned no rows")
                raw_rows = candidate
                provider_name = name
                provider_metadata = metadata
                break
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}"[:500])
        if raw_rows is None:
            self.last_provider = None
            self.last_metadata = {
                "errors": errors,
                "request_count": request_count,
            }
            raise RuntimeError(
                "all trading-calendar providers failed: " + " | ".join(errors)
            )
        normalized: list[dict[str, Any]] = []
        for row in raw_rows:
            calendar_date = date.fromisoformat(str(row["calendar_date"]))
            if calendar_date < start_date or calendar_date > end_date:
                raise ValueError("calendar provider returned out-of-range date")
            is_trading_day = str(row["is_trading_day"]) == "1"
            content = {
                "provider": provider_name,
                "calendar_date": calendar_date,
                "is_trading_day": is_trading_day,
            }
            normalized.append(
                {
                    **content,
                    "content_hash": stable_hash(content),
                    "metadata": provider_metadata,
                }
            )
        if len({item["calendar_date"] for item in normalized}) != len(normalized):
            raise ValueError("calendar provider returned duplicate dates")
        inserted = (
            self.repository.save_calendar(
                provider=provider_name,
                rows=normalized,
                data_cutoff=data_cutoff,
                fetched_at=fetched_at,
            )
            if persist
            else 0
        )
        trading_days = [
            item["calendar_date"]
            for item in normalized
            if item["is_trading_day"]
        ]
        self.last_provider = provider_name
        self.last_metadata = {
            **provider_metadata,
            "attempt_errors": errors,
            "request_count": request_count,
        }
        return trading_days, request_count, inserted

    def ensure_recent(
        self,
        *,
        data_cutoff: datetime,
        target_trading_days: int,
        persist: bool,
    ) -> tuple[list[date], int, int]:
        end_date = data_cutoff.astimezone().date()
        if (
            data_cutoff.astimezone().hour
            < settings.history_market_close_available_hour
        ):
            end_date -= timedelta(days=1)
        existing = self.repository.trading_days(
            data_cutoff=data_cutoff,
            end_date=end_date,
            limit=target_trading_days,
        )
        if len(existing) >= target_trading_days:
            return existing[-target_trading_days:], 0, 0
        start_date = end_date - timedelta(
            days=max(120, int(target_trading_days * 2.5))
        )
        days, requests, inserted = self.fetch(
            start_date=start_date,
            end_date=end_date,
            data_cutoff=data_cutoff,
            persist=persist,
        )
        if len(days) < target_trading_days:
            raise RuntimeError(
                "trading calendar did not return enough completed trading days"
            )
        return days[-target_trading_days:], requests, inserted


class HistoryProviderVerificationService:
    def __init__(
        self,
        *,
        repository: HistoryRepository | None = None,
        calendar: TradingCalendarService | None = None,
        akshare: Any | None = None,
        baostock: Any | None = None,
        app_settings: Settings = settings,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or HistoryRepository()
        self.calendar = calendar or TradingCalendarService(
            repository=self.repository
        )
        self.akshare = akshare or AKShareProvider()
        self.baostock = baostock or BaoStockProvider()
        self.settings = app_settings
        self.clock = clock or (lambda: datetime.now().astimezone())

    def verify_tushare_trade_date(
        self,
        *,
        trade_date: date,
        provider: TushareProvider | None = None,
    ) -> ProviderVerificationResult:
        """Probe one batch daily date without persistence or fallback."""
        resolved = provider or TushareProvider(
            token=self.settings.tushare_token
        )
        started = perf_counter()
        try:
            rows = resolved.get_daily_by_trade_date(trade_date)
        except Exception as exc:
            code, reason = classify_tushare_error(exc)
            return ProviderVerificationResult(
                provider="Tushare",
                capability="BATCH_DAILY_BY_TRADE_DATE",
                status=code,
                request_count=1,
                record_count=0,
                latency_ms=round((perf_counter() - started) * 1000),
                failure_reason=reason,
                metadata={
                    "api_name": "daily",
                    "trade_date": trade_date.strftime("%Y%m%d"),
                    "exception_type": type(exc).__name__,
                    "provider_error_code": None,
                },
            )
        return ProviderVerificationResult(
            provider="Tushare",
            capability="BATCH_DAILY_BY_TRADE_DATE",
            status="AVAILABLE" if rows else "EMPTY_RESULT",
            request_count=1,
            record_count=len(rows),
            latency_ms=round((perf_counter() - started) * 1000),
            failure_reason=(
                None if rows else "Tushare daily returned no records"
            ),
            metadata={
                "api_name": "daily",
                "trade_date": trade_date.strftime("%Y%m%d"),
                "exception_type": None,
                "provider_error_code": 0,
            },
        )

    def verify(
        self,
        *,
        data_cutoff: datetime,
        symbol: str = "600000.SH",
        persist_calendar: bool = False,
    ) -> ProviderVerificationResponse:
        end_date = data_cutoff.astimezone().date()
        if (
            data_cutoff.astimezone().hour
            < self.settings.history_market_close_available_hour
        ):
            end_date -= timedelta(days=1)
        start_date = end_date - timedelta(days=14)
        results: list[ProviderVerificationResult] = []
        total_requests = 0

        started = perf_counter()
        try:
            days, requests, _ = self.calendar.fetch(
                start_date=start_date,
                end_date=end_date,
                data_cutoff=data_cutoff,
                persist=persist_calendar,
            )
            total_requests += requests
            results.append(
                ProviderVerificationResult(
                    provider=self.calendar.last_provider or "UNKNOWN",
                    capability="TRADING_CALENDAR",
                    status="AVAILABLE",
                    request_count=requests,
                    record_count=len(days),
                    latency_ms=round((perf_counter() - started) * 1000),
                    metadata={
                        "persisted": persist_calendar,
                        "start_date": start_date,
                        "end_date": end_date,
                        **self.calendar.last_metadata,
                    },
                )
            )
        except Exception as exc:
            failed_requests = int(
                self.calendar.last_metadata.get("request_count", 2)
            )
            total_requests += failed_requests
            results.append(
                ProviderVerificationResult(
                    provider="BaoStock/AKShare",
                    capability="TRADING_CALENDAR",
                    status="FAILED",
                    request_count=failed_requests,
                    record_count=0,
                    latency_ms=round((perf_counter() - started) * 1000),
                    failure_reason=f"{type(exc).__name__}: {exc}"[:500],
                )
            )

        for name, provider in (
            ("BaoStock", self.baostock),
            ("AKShare", self.akshare),
        ):
            started = perf_counter()
            try:
                rows = provider.get_daily_bars(
                    symbol,
                    start_date.strftime("%Y%m%d"),
                    end_date.strftime("%Y%m%d"),
                    adjustment_type="RAW",
                )
                total_requests += 1
                results.append(
                    ProviderVerificationResult(
                        provider=name,
                        capability="PER_SYMBOL_DAILY_RAW",
                        status="AVAILABLE",
                        request_count=1,
                        record_count=len(rows),
                        latency_ms=round((perf_counter() - started) * 1000),
                        metadata={
                            "adjustment_type": "RAW",
                            "volume_unit": (
                                rows[0].data.get("volume_unit")
                                if rows
                                else None
                            ),
                            "amount_unit": (
                                rows[0].data.get("amount_unit")
                                if rows
                                else None
                            ),
                            "fields": (
                                sorted(rows[0].data)
                                if rows
                                else []
                            ),
                            "requested_start_date": start_date,
                            "requested_end_date": end_date,
                            "returned_start_date": (
                                min(
                                    row.event_time.astimezone().date()
                                    for row in rows
                                )
                                if rows
                                else None
                            ),
                            "returned_end_date": (
                                max(
                                    row.event_time.astimezone().date()
                                    for row in rows
                                )
                                if rows
                                else None
                            ),
                        },
                    )
                )
            except Exception as exc:
                total_requests += 1
                results.append(
                    ProviderVerificationResult(
                        provider=name,
                        capability="PER_SYMBOL_DAILY_RAW",
                        status="FAILED",
                        request_count=1,
                        record_count=0,
                        latency_ms=round((perf_counter() - started) * 1000),
                        failure_reason=f"{type(exc).__name__}: {exc}"[:500],
                    )
                )
            results.append(
                ProviderVerificationResult(
                    provider=name,
                    capability="BATCH_DAILY_BY_TRADE_DATE",
                    status="UNSUPPORTED_INTERFACE",
                    request_count=0,
                    record_count=0,
                    latency_ms=0,
                )
            )

        token_present = bool(
            self.settings.tushare_token
            and self.settings.tushare_token.strip()
        )
        tushare_capabilities = (
            "TRADING_CALENDAR",
            "STOCK_LIST",
            "PER_SYMBOL_DAILY_RAW",
            "BATCH_DAILY_BY_TRADE_DATE",
            "ANNOUNCEMENT",
            "FINANCIAL",
        )
        if not token_present:
            results.extend(
                ProviderVerificationResult(
                    provider="Tushare",
                    capability=capability,
                    status="SKIPPED_NO_TOKEN",
                    request_count=0,
                    record_count=0,
                    latency_ms=0,
                )
                for capability in tushare_capabilities
            )
        else:
            results.extend(
                ProviderVerificationResult(
                    provider="Tushare",
                    capability=capability,
                    status="NOT_PROBED_BY_GENERIC_VERIFIER",
                    request_count=0,
                    record_count=0,
                    latency_ms=0,
                    failure_reason=(
                        "token exists; use the bounded Tushare permission "
                        "verifier before enabling this provider"
                    ),
                )
                for capability in tushare_capabilities
            )
        return ProviderVerificationResponse(
            data_cutoff=data_cutoff,
            tushare_token_present=token_present,
            total_request_count=total_requests,
            results=results,
        )


__all__ = [
    "HistoryProviderVerificationService",
    "TradingCalendarService",
]
