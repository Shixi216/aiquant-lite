from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from time import perf_counter
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

from config.settings import settings
from database.db import open_database
from trading.research.sentiment.v3.calculators import MarketQuote
from trading.research.sentiment.v3.schemas import Finality, ProviderHealth, QualityFlag


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LOCAL_MAX_AGE = timedelta(minutes=10)
TENCENT_TIME_BUDGET_SECONDS = 8.0
_SECRET = re.compile(r"(?i)(api[_-]?key|token|cookie|authorization|secret)\s*[:=]\s*[^\s,;]+")


def sanitize_error(value: BaseException) -> str:
    text = _SECRET.sub(r"\1=<redacted>", str(value)).replace("\r", " ").replace("\n", " ")
    return text[:300]


@dataclass(frozen=True)
class MarketBreadthRawData:
    source: str
    source_timestamp: datetime
    available_time: datetime
    symbols: list[MarketQuote]
    expected_universe_size: int | None
    coverage_ratio: float
    field_provenance: dict[str, str]
    quality_flags: tuple[QualityFlag, ...] = ()
    eod_only: bool = False


class MarketBreadthProvider(Protocol):
    name: str

    def fetch(
        self,
        *,
        trade_date: date,
        data_cutoff: datetime,
        finality: Finality,
    ) -> MarketBreadthRawData: ...


def _provenance(source: str) -> dict[str, str]:
    return {
        key: source
        for key in (
            "advancers",
            "decliners",
            "unchanged",
            "market_turnover",
            "advance_ratio",
            "decline_ratio",
            "advance_decline_ratio",
            "breadth_strength",
        )
    }


class AkshareMarketBreadthProvider:
    name = "AKShare / Eastmoney spot"

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    def fetch(
        self, *, trade_date: date, data_cutoff: datetime, finality: Finality
    ) -> MarketBreadthRawData:
        del finality
        if trade_date != data_cutoff.astimezone(SHANGHAI_TZ).date():
            raise ValueError("AKShare spot cannot reconstruct historical market breadth")
        from data_hub.providers.full_market import AKShareBatchProvider

        frame = AKShareBatchProvider().fetch_market_snapshot().frame
        from trading.research.sentiment.v3.collectors import SentimentV3Collector

        quotes = SentimentV3Collector._normalize_quotes(frame)
        now = self.clock()
        return MarketBreadthRawData(
            source=self.name,
            source_timestamp=now,
            available_time=now,
            symbols=quotes,
            expected_universe_size=len(quotes),
            coverage_ratio=1.0 if quotes else 0.0,
            field_provenance=_provenance(self.name),
        )


class TushareMarketBreadthProvider:
    name = "Tushare daily EOD"

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    def fetch(
        self, *, trade_date: date, data_cutoff: datetime, finality: Finality
    ) -> MarketBreadthRawData:
        if finality == Finality.INTRADAY:
            raise ValueError("Tushare daily is EOD-only")
        if not settings.tushare_token or not settings.tushare_token.strip():
            raise RuntimeError("Tushare token is not configured")
        import tushare as ts

        ts.set_token(settings.tushare_token.strip())
        frame = ts.pro_api().daily(trade_date=trade_date.strftime("%Y%m%d"))
        if frame.empty:
            raise RuntimeError("Tushare daily returned no rows")
        quotes = [
            MarketQuote(
                symbol=str(row.ts_code),
                change_pct=float(row.pct_chg) / 100,
                amount=float(row.amount) * 1000,
            )
            for row in frame.itertuples()
        ]
        now = self.clock()
        source_timestamp = datetime.combine(
            trade_date, datetime.min.time().replace(hour=15), tzinfo=SHANGHAI_TZ
        )
        return MarketBreadthRawData(
            source=self.name,
            source_timestamp=source_timestamp,
            available_time=now,
            symbols=quotes,
            expected_universe_size=len(quotes),
            coverage_ratio=1.0,
            field_provenance=_provenance(self.name),
            quality_flags=(QualityFlag.EOD_ONLY,),
            eod_only=True,
        )


class LocalMarketBreadthProvider:
    name = "Data Hub local market snapshot"

    def __init__(
        self,
        database_path=None,
        max_age: timedelta = LOCAL_MAX_AGE,
    ) -> None:
        self.database_path = database_path or settings.opc_database_path
        self._explicit_database_path = database_path is not None
        self.max_age = max_age

    def _from_router(self):
        import httpx

        response = httpx.get(
            f"http://{settings.opc_router_host}:{settings.opc_router_port}/v1/market-snapshots/latest",
            params={"maximum_age_seconds": int(self.max_age.total_seconds()), "limit": 5000},
            timeout=15,
            trust_env=False,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("stale"):
            raise RuntimeError("local market snapshot is stale")
        snapshot_time = datetime.fromisoformat(payload["snapshot_time"])
        run = (
            payload["snapshot_id"],
            payload["provider"],
            datetime.fromisoformat(payload["data_cutoff"]),
            payload["expected_universe_size"],
            payload["valid_symbol_count"],
            payload["coverage_ratio"],
            snapshot_time,
        )
        rows = [
            (item["symbol"], item.get("change_pct"), item.get("amount"))
            for item in payload["items"]
            if item.get("item_status") == "VALID"
        ]
        return run, rows

    def fetch(
        self, *, trade_date: date, data_cutoff: datetime, finality: Finality
    ) -> MarketBreadthRawData:
        del finality
        if (
            not self._explicit_database_path
            and os.environ.get("HERMES_DB_OWNER_PROCESS") != "router"
        ):
            run, rows = self._from_router()
            snapshot_time = run[6]
            if snapshot_time.astimezone(SHANGHAI_TZ).date() != trade_date:
                raise RuntimeError("local market snapshot trade_date is stale")
            age = data_cutoff - snapshot_time
            if age < timedelta(0) or age > self.max_age:
                raise RuntimeError(f"local market snapshot is stale by {age}")
            quotes = [MarketQuote(symbol=row[0], change_pct=row[1], amount=row[2]) for row in rows]
            source = f"{self.name} ({run[1]})"
            return MarketBreadthRawData(
                source=source,
                source_timestamp=snapshot_time,
                available_time=run[2],
                symbols=quotes,
                expected_universe_size=int(run[3]),
                coverage_ratio=float(run[5]),
                field_provenance=_provenance(source),
            )
        with open_database(self.database_path, read_only=True) as connection:
            run = connection.execute(
                """
                SELECT snapshot_id, provider, data_cutoff, expected_universe_size,
                       valid_symbol_count, coverage_ratio, snapshot_time
                FROM market_snapshot_runs
                WHERE mode = 'APPLY'
                  AND completeness_status != 'FAILED'
                  AND data_cutoff <= ?
                  AND snapshot_time <= ?
                ORDER BY snapshot_time DESC, completed_at DESC
                LIMIT 1
                """,
                [data_cutoff, data_cutoff],
            ).fetchone()
            if run is None:
                raise RuntimeError("no local market snapshot is available as of cutoff")
            snapshot_time = run[6]
            if snapshot_time.astimezone(SHANGHAI_TZ).date() != trade_date:
                raise RuntimeError("local market snapshot trade_date is stale")
            age = data_cutoff - snapshot_time
            if age < timedelta(0) or age > self.max_age:
                raise RuntimeError(f"local market snapshot is stale by {age}")
            rows = connection.execute(
                """
                SELECT symbol, change_pct, amount
                FROM market_snapshot_items
                WHERE snapshot_id = ? AND item_status = 'VALID'
                ORDER BY symbol
                """,
                [run[0]],
            ).fetchall()
        quotes = [MarketQuote(symbol=row[0], change_pct=row[1], amount=row[2]) for row in rows]
        source = f"{self.name} ({run[1]})"
        return MarketBreadthRawData(
            source=source,
            source_timestamp=snapshot_time,
            available_time=run[2],
            symbols=quotes,
            expected_universe_size=int(run[3]),
            coverage_ratio=float(run[5]),
            field_provenance=_provenance(source),
        )


class TencentMarketBreadthProvider:
    name = "Tencent batch quote"

    def __init__(
        self,
        database_path=None,
        *,
        time_budget_seconds: float = TENCENT_TIME_BUDGET_SECONDS,
    ) -> None:
        if time_budget_seconds <= 0:
            raise ValueError("Tencent time budget must be positive")
        self.database_path = database_path or settings.opc_database_path
        self._explicit_database_path = database_path is not None
        self.time_budget_seconds = time_budget_seconds

    def _active_symbols(self, deadline: float | None = None) -> list[str]:
        if self._explicit_database_path or os.environ.get("HERMES_DB_OWNER_PROCESS") == "router":
            with open_database(self.database_path, read_only=True) as connection:
                return [
                    row[0].split(".", 1)[0]
                    for row in connection.execute(
                        """
                        SELECT DISTINCT symbol FROM stock_universe
                        WHERE listing_status = 'ACTIVE'
                        ORDER BY symbol
                        """
                    ).fetchall()
                ]
        import httpx

        url = f"http://{settings.opc_router_host}:{settings.opc_router_port}/v1/universe"
        symbols: list[str] = []
        offset = 0
        while True:
            remaining = None if deadline is None else deadline - perf_counter()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("Tencent provider deadline exceeded while loading universe")
            response = httpx.get(
                url,
                params={"active_only": "true", "offset": offset, "limit": 5000},
                timeout=min(15.0, remaining) if remaining is not None else 15.0,
                trust_env=False,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items", [])
            symbols.extend(str(item["symbol"]).split(".", 1)[0] for item in items)
            offset += len(items)
            if not items or offset >= int(payload.get("total", offset)):
                break
        return symbols

    def fetch(
        self, *, trade_date: date, data_cutoff: datetime, finality: Finality
    ) -> MarketBreadthRawData:
        del finality
        if trade_date != data_cutoff.astimezone(SHANGHAI_TZ).date():
            raise ValueError("Tencent quote cannot reconstruct historical market breadth")
        deadline = perf_counter() + self.time_budget_seconds
        symbols = self._active_symbols(deadline)
        if not symbols:
            raise RuntimeError("local active stock universe is unavailable")
        remaining = deadline - perf_counter()
        if remaining <= 0:
            raise TimeoutError("Tencent provider deadline exceeded before quote fetch")
        from data_hub.providers.tencent_snapshot import TencentSnapshotProvider

        frame = TencentSnapshotProvider().fetch_market_snapshot(
            symbols, total_timeout_seconds=remaining
        )
        if frame.empty:
            raise RuntimeError("Tencent batch quote returned no rows")
        from trading.research.sentiment.v3.collectors import SentimentV3Collector

        quotes = SentimentV3Collector._normalize_quotes(frame)
        timestamp = max(frame["snapshot_time"].tolist())
        coverage = len(quotes) / len(symbols)
        return MarketBreadthRawData(
            source=self.name,
            source_timestamp=timestamp,
            available_time=timestamp,
            symbols=quotes,
            expected_universe_size=len(symbols),
            coverage_ratio=min(1.0, coverage),
            field_provenance=_provenance(self.name),
        )


@dataclass(frozen=True)
class MarketBreadthResolution:
    data: MarketBreadthRawData | None
    provider_health: list[ProviderHealth]
    quality_flags: tuple[QualityFlag, ...]
    latency_by_provider_ms: dict[str, float] = field(default_factory=dict)

    @property
    def request_seconds(self) -> float:
        return sum(self.latency_by_provider_ms.values()) / 1000

    @property
    def eod_only(self) -> bool:
        return bool(self.data and self.data.eod_only)

    @property
    def source_timestamp(self) -> datetime | None:
        return None if self.data is None else self.data.source_timestamp

    @property
    def available_time(self) -> datetime | None:
        return None if self.data is None else self.data.available_time


class MarketBreadthProviderChain:
    def __init__(
        self,
        providers: list[MarketBreadthProvider],
        *,
        expected_universe_size: int | None = None,
    ) -> None:
        if not providers:
            raise ValueError("market breadth provider chain must not be empty")
        self.providers = providers
        self.expected_universe_size = expected_universe_size

    def resolve(
        self,
        *,
        trade_date: date,
        data_cutoff: datetime,
        finality: Finality,
    ) -> MarketBreadthResolution:
        health: list[ProviderHealth] = []
        flags: set[QualityFlag] = set()
        latencies: dict[str, float] = {}
        for index, provider in enumerate(self.providers):
            started = perf_counter()
            try:
                data = provider.fetch(
                    trade_date=trade_date,
                    data_cutoff=data_cutoff,
                    finality=finality,
                )
                latency = (perf_counter() - started) * 1000
                latencies[provider.name] = latency
                fallback = index > 0
                if fallback:
                    flags.update({QualityFlag.PRIMARY_SOURCE_FAILED, QualityFlag.FALLBACK_USED})
                flags.update(data.quality_flags)
                health.append(
                    ProviderHealth(
                        provider=provider.name,
                        success=True,
                        latency_ms=latency,
                        record_count=len(data.symbols),
                        coverage_ratio=data.coverage_ratio,
                        source_timestamp=data.source_timestamp,
                        fallback_used=fallback,
                    )
                )
                return MarketBreadthResolution(
                    data=data,
                    provider_health=health,
                    quality_flags=tuple(sorted(flags, key=str)),
                    latency_by_provider_ms=latencies,
                )
            except Exception as exc:
                latency = (perf_counter() - started) * 1000
                latencies[provider.name] = latency
                message = sanitize_error(exc)
                lowered = message.casefold()
                if "stale" in lowered:
                    flags.add(QualityFlag.STALE_DATA)
                if isinstance(exc, TimeoutError) or "timeout" in lowered or "deadline" in lowered:
                    flags.add(QualityFlag.PROVIDER_TIMEOUT)
                health.append(
                    ProviderHealth(
                        provider=provider.name,
                        success=False,
                        latency_ms=latency,
                        error_type=type(exc).__name__,
                        error_message_sanitized=message,
                        record_count=0,
                        coverage_ratio=0,
                        source_timestamp=None,
                    )
                )
        flags.update(
            {
                QualityFlag.SOURCE_UNAVAILABLE,
                QualityFlag.PARTIAL_DATA,
                QualityFlag.MARKET_BREADTH_UNAVAILABLE,
            }
        )
        if len(self.providers) > 1:
            flags.add(QualityFlag.PRIMARY_SOURCE_FAILED)
        return MarketBreadthResolution(
            data=None,
            provider_health=health,
            quality_flags=tuple(sorted(flags, key=str)),
            latency_by_provider_ms=latencies,
        )


def default_market_breadth_chain(
    *,
    finality: Finality,
    database_path=None,
) -> MarketBreadthProviderChain:
    if finality == Finality.INTRADAY:
        providers: list[MarketBreadthProvider] = [
            AkshareMarketBreadthProvider(),
            LocalMarketBreadthProvider(database_path),
            TencentMarketBreadthProvider(database_path),
        ]
    elif finality in {Finality.FINAL, Finality.CORRECTED}:
        providers = [
            TushareMarketBreadthProvider(),
            AkshareMarketBreadthProvider(),
            TencentMarketBreadthProvider(database_path),
        ]
    else:
        providers = [
            TushareMarketBreadthProvider(),
            LocalMarketBreadthProvider(database_path),
            AkshareMarketBreadthProvider(),
            TencentMarketBreadthProvider(database_path),
        ]
    return MarketBreadthProviderChain(providers)


__all__ = [
    "AkshareMarketBreadthProvider",
    "LOCAL_MAX_AGE",
    "LocalMarketBreadthProvider",
    "MarketBreadthProvider",
    "MarketBreadthProviderChain",
    "MarketBreadthRawData",
    "MarketBreadthResolution",
    "TencentMarketBreadthProvider",
    "TENCENT_TIME_BUDGET_SECONDS",
    "TushareMarketBreadthProvider",
    "default_market_breadth_chain",
    "sanitize_error",
]
