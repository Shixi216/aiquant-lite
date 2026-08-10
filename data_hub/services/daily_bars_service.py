from __future__ import annotations

from time import perf_counter
from typing import Any

from config.network import configure_network_policy
from database.db import (
    get_connection,
    initialize_database,
    insert_market_record,
)
from data_hub.providers import (
    AKShareProvider,
    BaoStockProvider,
    TushareProvider,
)
from data_hub.schemas.market import MarketRecord
from data_hub.schemas.service import DailyBarsResponse, ProviderRun
from data_hub.services.canonicalization_service import CanonicalizationService
from data_hub.verification import verify_daily_bars


class DailyBarsService:
    """Fetch, normalize, verify, and persist A-share daily bars."""

    def __init__(self) -> None:
        configure_network_policy()

        self._providers: list[tuple[str, Any]] = [
            ("Tushare Pro", TushareProvider()),
            ("AKShare / Eastmoney", AKShareProvider()),
            ("BaoStock", BaoStockProvider()),
        ]

    @staticmethod
    def _trade_date(record: MarketRecord) -> str:
        value = record.data.get("trade_date")

        if value is None:
            raise RuntimeError(
                f"{record.source_name} 记录缺少 trade_date"
            )

        return str(value)

    def _run_provider(
        self,
        provider_name: str,
        provider: Any,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> tuple[list[MarketRecord], ProviderRun]:
        started = perf_counter()

        try:
            records = provider.get_daily_bars(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )

            latency_ms = round((perf_counter() - started) * 1000)

            return records, ProviderRun(
                provider=provider_name,
                success=True,
                record_count=len(records),
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = round((perf_counter() - started) * 1000)

            return [], ProviderRun(
                provider=provider_name,
                success=False,
                record_count=0,
                latency_ms=latency_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

    @staticmethod
    def _insert_if_absent(
        connection: Any,
        record: MarketRecord,
    ) -> None:
        existing = connection.execute(
            """
            SELECT record_id
            FROM data_records
            WHERE content_hash = ?
            LIMIT 1
            """,
            [record.content_hash],
        ).fetchone()

        if existing is None:
            insert_market_record(connection, record)

    def _persist_records(
        self,
        records: list[MarketRecord],
    ) -> None:
        initialize_database()

        with get_connection() as connection:
            for record in records:
                self._insert_if_absent(connection, record)

            verified_hashes = [
                record.content_hash
                for record in records
                if record.verified and record.content_hash
            ]

            if verified_hashes:
                placeholders = ",".join(
                    "?" for _ in verified_hashes
                )

                connection.execute(
                    f"""
                    UPDATE data_records
                    SET verified = TRUE
                    WHERE content_hash IN ({placeholders})
                    """,
                    verified_hashes,
                )

        groups: dict[
            tuple[str, str, object],
            list[MarketRecord],
        ] = {}
        for record in records:
            key = (
                record.symbol,
                record.data_type.value,
                record.event_time,
            )
            groups.setdefault(key, []).append(record)
        canonicalization = CanonicalizationService()
        for group in groups.values():
            canonicalization.canonicalize_market(group)

    def get_daily_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        persist: bool = True,
    ) -> DailyBarsResponse:
        records_by_source: dict[str, list[MarketRecord]] = {}
        provider_runs: list[ProviderRun] = []

        for provider_name, provider in self._providers:
            records, run = self._run_provider(
                provider_name=provider_name,
                provider=provider,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
            )

            records_by_source[provider_name] = records
            provider_runs.append(run)

        successful_sources = [
            source
            for source, records in records_by_source.items()
            if records
        ]

        if not successful_sources:
            errors = "; ".join(
                f"{run.provider}: {run.error_type}: "
                f"{run.error_message}"
                for run in provider_runs
                if not run.success
            )
            raise RuntimeError(
                f"所有日线数据源均失败：{errors}"
            )

        indexed: dict[str, dict[str, MarketRecord]] = {}

        for source, records in records_by_source.items():
            indexed[source] = {
                self._trade_date(record): record
                for record in records
            }

        all_dates = sorted(
            {
                trade_date
                for source_records in indexed.values()
                for trade_date in source_records
            }
        )

        verified_dates: list[str] = []
        canonical_records: list[MarketRecord] = []

        for trade_date in all_dates:
            candidates = [
                indexed[source][trade_date]
                for source, _ in self._providers
                if trade_date in indexed[source]
            ]

            if len(candidates) >= 2:
                verification = verify_daily_bars(candidates)

                if verification.verified:
                    verified_dates.append(trade_date)

                    for record in candidates:
                        record.verified = True

            canonical: MarketRecord | None = None

            for source, _ in self._providers:
                record = indexed[source].get(trade_date)

                if record is not None:
                    canonical = record
                    break

            if canonical is not None:
                canonical_records.append(canonical)

        all_records = [
            record
            for records in records_by_source.values()
            for record in records
        ]

        if persist:
            self._persist_records(all_records)

        verified_set = set(verified_dates)

        return DailyBarsResponse(
            symbol=canonical_records[0].symbol,
            start_date=start_date,
            end_date=end_date,
            primary_source=successful_sources[0],
            records=canonical_records,
            provider_runs=provider_runs,
            verified_dates=verified_dates,
            unverified_dates=[
                trade_date
                for trade_date in all_dates
                if trade_date not in verified_set
            ],
        )
