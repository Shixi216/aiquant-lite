from __future__ import annotations

import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

from data_hub.providers.full_market import AKShareBatchProvider
from data_hub.repositories.full_market import FullMarketRepository
from data_hub.schemas.full_market import (
    MarketSnapshotItem,
    MarketSnapshotResponse,
    MarketSnapshotSyncRequest,
    SnapshotCompleteness,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.services.full_market_common import (
    as_float,
    clean_text,
    normalize_symbol,
    stable_hash,
)
from data_hub.services.provider_capability_registry import (
    ProviderCapabilityRegistry,
)


SNAPSHOT_ALGORITHM_VERSION = "market-snapshot-v1"


def _rate(value: Any) -> float | None:
    number = as_float(value)
    return None if number is None else number / 100


class MarketSnapshotService:
    def __init__(
        self,
        *,
        repository: FullMarketRepository | None = None,
        provider: AKShareBatchProvider | None = None,
        capability_registry: ProviderCapabilityRegistry | None = None,
        clock: Any | None = None,
    ) -> None:
        self.repository = repository or FullMarketRepository()
        self.provider = provider or AKShareBatchProvider()
        self.capabilities = capability_registry or ProviderCapabilityRegistry(
            self.repository
        )
        self.clock = clock or (lambda: datetime.now().astimezone())

    def sync(self, request: MarketSnapshotSyncRequest) -> MarketSnapshotResponse:
        if request.request_budget < 1:
            raise ValueError("market snapshot requires one batch request")
        started_at = self.clock()
        started = perf_counter()
        database_path = Path("database/hermes_opc.duckdb")
        size_before = database_path.stat().st_size if database_path.exists() else 0
        tracemalloc.start()
        try:
            result = self.provider.fetch_market_snapshot()
            rows = result.frame.to_dict(orient="records")
            self.capabilities.record(
                provider=result.provider,
                capability=result.capability,
                available=True,
                verified_at=started_at,
                batch_supported=True,
                maximum_batch_size=len(rows),
                metadata=result.metadata,
            )
        except Exception as exc:
            self.capabilities.record(
                provider="AKShare",
                capability="FULL_MARKET_REALTIME",
                available=False,
                verified_at=started_at,
                batch_supported=True,
                failure_reason=f"{type(exc).__name__}: {exc}",
            )
            tracemalloc.stop()
            raise RuntimeError(
                f"full-market snapshot provider failed: {type(exc).__name__}"
            ) from exc

        version, _, universe = self.repository.list_universe(
            active_only=True,
            limit=10_000,
        )
        del version
        universe_symbols = {item.symbol for item in universe}
        expected_count = len(universe_symbols)
        snapshot_time = started_at
        items_by_symbol: dict[str, MarketSnapshotItem] = {}
        raw_records: list[MarketRecord] = []
        for row in rows:
            symbol = normalize_symbol(row.get("代码"))
            if symbol is None:
                continue
            if universe_symbols and symbol not in universe_symbols:
                continue
            price = as_float(row.get("最新价"))
            previous_close = as_float(row.get("昨收"))
            volume_lots = as_float(row.get("成交量"))
            volume = None if volume_lots is None else volume_lots * 100
            amount = as_float(row.get("成交额"))
            is_suspended = price is None or price <= 0
            is_abnormal = any(
                value is not None and value < 0
                for value in (volume, amount)
            )
            item_status = (
                "SUSPENDED_OR_NO_PRICE"
                if is_suspended
                else ("ABNORMAL" if is_abnormal else "VALID")
            )
            raw_payload = {
                str(key): (
                    None if clean_text(value) == "" else value
                )
                for key, value in row.items()
            }
            normalized_payload = {
                "symbol": symbol,
                "price": price,
                "previous_close": previous_close,
                "open": as_float(row.get("今开")),
                "high": as_float(row.get("最高")),
                "low": as_float(row.get("最低")),
                "volume": volume,
                "volume_unit": "SHARES",
                "amount": amount,
                "amount_unit": "CNY",
                "change": as_float(row.get("涨跌额")),
                "change_pct": _rate(row.get("涨跌幅")),
                "change_pct_unit": "DECIMAL",
                "turnover_rate": _rate(row.get("换手率")),
                "turnover_rate_unit": "DECIMAL",
                "snapshot_time": snapshot_time.isoformat(),
                "source_payload": raw_payload,
                "algorithm_version": SNAPSHOT_ALGORITHM_VERSION,
            }
            digest = stable_hash(
                {
                    "source": "AKShare / Eastmoney batch realtime",
                    "symbol": symbol,
                    "event_time": snapshot_time,
                    "payload": normalized_payload,
                }
            )
            raw_id = f"raw_quote_{digest[:32]}"
            item = MarketSnapshotItem(
                symbol=symbol,
                price=price,
                previous_close=previous_close,
                open=normalized_payload["open"],
                high=normalized_payload["high"],
                low=normalized_payload["low"],
                volume=volume,
                amount=amount,
                change=normalized_payload["change"],
                change_pct=normalized_payload["change_pct"],
                turnover_rate=normalized_payload["turnover_rate"],
                snapshot_time=snapshot_time,
                source="AKShare / Eastmoney batch realtime",
                item_status=item_status,
                is_suspended=is_suspended,
                is_abnormal=is_abnormal,
                raw_record_id=raw_id,
                payload=normalized_payload,
            )
            items_by_symbol[symbol] = item
            raw_records.append(
                MarketRecord(
                    record_id=raw_id,
                    symbol=symbol,
                    data_type=DataType.REALTIME_QUOTE,
                    event_time=snapshot_time,
                    fetched_at=snapshot_time,
                    source_name="AKShare / Eastmoney batch realtime",
                    source_level=SourceLevel.PUBLIC_WEB,
                    verified=False,
                    content_hash=digest,
                    data=normalized_payload,
                )
            )

        items = sorted(items_by_symbol.values(), key=lambda item: item.symbol)
        if request.limit is not None:
            items = items[: request.limit]
            allowed = {item.symbol for item in items}
            raw_records = [
                item for item in raw_records if item.symbol in allowed
            ]
        received_count = len(items)
        valid_count = sum(item.item_status == "VALID" for item in items)
        denominator = expected_count or received_count
        coverage = valid_count / denominator if denominator else 0.0
        completeness = (
            SnapshotCompleteness.COMPLETE
            if denominator > 0 and valid_count == denominator
            else SnapshotCompleteness.PARTIAL_UNIVERSE
        )
        content_hash = stable_hash(
            {
                "algorithm": SNAPSHOT_ALGORITHM_VERSION,
                "snapshot_time": snapshot_time,
                "items": [
                    item.model_dump(mode="json", exclude={"payload"})
                    for item in items
                ],
            }
        )
        snapshot_id = f"mkt_{content_hash[:32]}"
        _, peak_memory = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        completed_at = self.clock()
        elapsed = max(0.0, perf_counter() - started)
        if not request.dry_run:
            self.repository.save_market_snapshot(
                snapshot_id=snapshot_id,
                provider=result.provider,
                data_cutoff=request.data_cutoff,
                expected_count=expected_count,
                received_count=received_count,
                valid_count=valid_count,
                coverage_ratio=coverage,
                completeness=completeness,
                started_at=started_at,
                completed_at=completed_at,
                snapshot_time=snapshot_time,
                content_hash=content_hash,
                request_count=result.request_count,
                elapsed_seconds=elapsed,
                peak_memory_bytes=peak_memory,
                database_growth_bytes=None,
                items=items,
                raw_records=raw_records,
            )
            self.capabilities.persist()
        size_after = database_path.stat().st_size if database_path.exists() else size_before
        del size_after
        age = max(
            0.0,
            (self.clock().astimezone(timezone.utc) - snapshot_time.astimezone(timezone.utc))
            .total_seconds(),
        )
        return MarketSnapshotResponse(
            snapshot_id=snapshot_id,
            mode="DRY_RUN" if request.dry_run else "APPLY",
            provider=result.provider,
            data_cutoff=request.data_cutoff,
            snapshot_time=snapshot_time,
            expected_universe_size=expected_count,
            received_symbol_count=received_count,
            valid_symbol_count=valid_count,
            missing_symbol_count=max(0, expected_count - valid_count),
            coverage_ratio=coverage,
            completeness_status=completeness,
            stale=age > request.maximum_age_seconds,
            original_snapshot_time=snapshot_time,
            data_age_seconds=age,
            request_count=result.request_count,
            items=items,
            elapsed_seconds=elapsed,
            peak_memory_bytes=peak_memory,
        )

    def latest(
        self,
        *,
        maximum_age_seconds: int,
        limit: int = 100,
    ) -> MarketSnapshotResponse | None:
        persisted = self.repository.latest_market_snapshot(limit=limit)
        if persisted is None:
            return None
        run, items = persisted
        now = self.clock()
        age = max(
            0.0,
            (
                now.astimezone(timezone.utc)
                - run[10].astimezone(timezone.utc)
            ).total_seconds(),
        )
        return MarketSnapshotResponse(
            snapshot_id=run[0],
            mode=run[1],
            provider=run[2],
            data_cutoff=run[3],
            snapshot_time=run[10],
            expected_universe_size=run[4],
            received_symbol_count=run[5],
            valid_symbol_count=run[6],
            missing_symbol_count=run[7],
            coverage_ratio=run[8],
            completeness_status=run[9],
            stale=age > maximum_age_seconds,
            original_snapshot_time=run[10],
            data_age_seconds=age,
            request_count=run[12],
            items=items,
            elapsed_seconds=run[13],
            peak_memory_bytes=run[14],
        )


__all__ = ["MarketSnapshotService", "SNAPSHOT_ALGORITHM_VERSION"]
