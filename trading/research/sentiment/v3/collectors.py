from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time
from time import perf_counter
from typing import Callable, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from trading.research.sentiment.v3.calculators import (
    LimitUpStock,
    MarketQuote,
    SnapshotBuildContext,
    SnapshotInputs,
    calculate_market_snapshot,
    calculate_speculation_snapshot,
)
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.market_providers import (
    MarketBreadthResolution,
    default_market_breadth_chain,
)
from trading.research.sentiment.v3.schemas import (
    Finality,
    MarketSentimentSnapshot,
    QualityFlag,
    RecordSource,
    SnapshotType,
    SpeculationSentimentSnapshot,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class SourceSchemaChangedError(RuntimeError):
    pass


@dataclass(frozen=True)
class FetchedFrame:
    frame: pd.DataFrame
    source_timestamp: datetime
    available_time: datetime
    request_seconds: float
    eod_only: bool = False


class SentimentV3DataSource(Protocol):
    name: str

    def fetch_market_quotes(self, trade_date: date) -> FetchedFrame: ...

    def fetch_limit_up_pool(self, trade_date: date) -> FetchedFrame: ...

    def fetch_limit_down_pool(self, trade_date: date) -> FetchedFrame: ...

    def fetch_broken_board_pool(self, trade_date: date) -> FetchedFrame: ...

    def fetch_strong_stock_pool(self, trade_date: date) -> FetchedFrame: ...


def _normalize_symbol(value: object) -> str | None:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    code = text.zfill(6)
    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("4", "8", "920")):
        exchange = "BJ"
    elif code.startswith(("5", "6", "9")):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{code}.{exchange}"


def _column(frame: pd.DataFrame, names: tuple[str, ...]) -> str:
    for name in names:
        if name in frame.columns:
            return name
    raise SourceSchemaChangedError(f"missing source columns: {names}")


def _number(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AKShareSentimentV3Source:
    """Batch-only AKShare adapter. No method performs per-symbol requests."""

    name = "AKShare / Eastmoney"

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    def _fetch(self, function: Callable, *, eod_only: bool, **kwargs) -> FetchedFrame:
        started = perf_counter()
        frame = function(**kwargs)
        completed = self.clock()
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("AKShare source did not return a DataFrame")
        return FetchedFrame(
            frame=frame,
            source_timestamp=completed,
            available_time=completed,
            request_seconds=perf_counter() - started,
            eod_only=eod_only,
        )

    def fetch_market_quotes(self, trade_date: date) -> FetchedFrame:
        if trade_date != self.clock().date():
            raise ValueError("AKShare spot data cannot reconstruct a historical intraday quote set")
        import akshare as ak

        return self._fetch(ak.stock_zh_a_spot_em, eod_only=False)

    def fetch_limit_up_pool(self, trade_date: date) -> FetchedFrame:
        import akshare as ak

        return self._fetch(
            ak.stock_zt_pool_em,
            date=trade_date.strftime("%Y%m%d"),
            eod_only=trade_date < self.clock().date(),
        )

    def fetch_limit_down_pool(self, trade_date: date) -> FetchedFrame:
        import akshare as ak

        return self._fetch(
            ak.stock_zt_pool_dtgc_em,
            date=trade_date.strftime("%Y%m%d"),
            eod_only=trade_date < self.clock().date(),
        )

    def fetch_broken_board_pool(self, trade_date: date) -> FetchedFrame:
        import akshare as ak

        return self._fetch(
            ak.stock_zt_pool_zbgc_em,
            date=trade_date.strftime("%Y%m%d"),
            eod_only=trade_date < self.clock().date(),
        )

    def fetch_strong_stock_pool(self, trade_date: date) -> FetchedFrame:
        import akshare as ak

        return self._fetch(
            ak.stock_zt_pool_strong_em,
            date=trade_date.strftime("%Y%m%d"),
            eod_only=trade_date < self.clock().date(),
        )


@dataclass(frozen=True)
class CollectorPerformance:
    external_request_seconds: float
    request_seconds_by_source: dict[str, float]
    normalization_seconds: float
    snapshot_calculation_seconds: float
    db_write_seconds: float
    total_seconds: float
    request_count: int
    failed_sources: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionResult:
    market: MarketSentimentSnapshot
    speculation: SpeculationSentimentSnapshot
    performance: CollectorPerformance


class SentimentV3Collector:
    """One shared market collection feeding both S2A snapshot modules."""

    _FETCHERS = {
        "quotes": "fetch_market_quotes",
        "limit_up": "fetch_limit_up_pool",
        "limit_down": "fetch_limit_down_pool",
        "broken": "fetch_broken_board_pool",
        "strong": "fetch_strong_stock_pool",
    }

    def __init__(
        self,
        *,
        repository: SentimentV3Repository | None = None,
        source: SentimentV3DataSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or SentimentV3Repository()
        self._use_market_breadth_chain = source is None
        self.source = source or AKShareSentimentV3Source()
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    @staticmethod
    def _normalize_quotes(frame: pd.DataFrame) -> list[MarketQuote]:
        symbol_column = _column(frame, ("代码", "symbol", "code", "ts_code"))
        change_column = _column(frame, ("涨跌幅", "pct_chg", "change_pct"))
        amount_column = _column(frame, ("成交额", "amount", "turnover"))
        values: list[MarketQuote] = []
        for row in frame.to_dict(orient="records"):
            symbol = _normalize_symbol(row.get(symbol_column))
            if symbol is None:
                continue
            change = _number(row.get(change_column))
            values.append(
                MarketQuote(
                    symbol=symbol,
                    change_pct=None if change is None else change / 100,
                    amount=_number(row.get(amount_column)),
                )
            )
        return values

    @staticmethod
    def _normalize_symbols(frame: pd.DataFrame) -> list[str]:
        symbol_column = _column(frame, ("代码", "symbol", "code", "ts_code"))
        return [
            symbol
            for value in frame[symbol_column].tolist()
            if (symbol := _normalize_symbol(value)) is not None
        ]

    @staticmethod
    def _normalize_limit_up(frame: pd.DataFrame) -> list[LimitUpStock]:
        symbol_column = _column(frame, ("代码", "symbol", "code", "ts_code"))
        height_column = next(
            (name for name in ("连板数", "board_height") if name in frame.columns),
            None,
        )
        values: list[LimitUpStock] = []
        for row in frame.to_dict(orient="records"):
            symbol = _normalize_symbol(row.get(symbol_column))
            if symbol is None:
                continue
            height_number = _number(row.get(height_column)) if height_column else None
            values.append(
                LimitUpStock(
                    symbol=symbol,
                    board_height=(
                        int(height_number)
                        if height_number is not None and height_number >= 1
                        else None
                    ),
                )
            )
        return values

    def _validate_finality(
        self,
        *,
        trade_date: date,
        finality: Finality,
        confirmed_final: bool,
        batches: dict[str, FetchedFrame],
    ) -> None:
        now = self.clock()
        if finality == Finality.FINAL:
            if not confirmed_final:
                raise ValueError("FINAL collection requires explicit confirmed_final=True")
            if trade_date == now.date() and now.timetz().replace(tzinfo=None) < time(15, 0):
                raise ValueError("current trade date cannot be FINAL before market close")
        if finality == Finality.INTRADAY and any(batch.eod_only for batch in batches.values()):
            raise ValueError("EOD-only source data cannot be labelled INTRADAY")

    def collect(
        self,
        *,
        trade_date: date,
        finality: Finality = Finality.INTRADAY,
        record_source: RecordSource = RecordSource.REAL,
        persist: bool = True,
        confirmed_final: bool = False,
        corrects_market_snapshot_id: str | None = None,
        corrects_speculation_snapshot_id: str | None = None,
    ) -> CollectionResult:
        total_started = perf_counter()
        request_cutoff = self.clock()
        external_started = perf_counter()
        batches: dict[str, FetchedFrame | MarketBreadthResolution] = {}
        failures: dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=len(self._FETCHERS)) as pool:
            futures = {}
            for key, method in self._FETCHERS.items():
                if key == "quotes" and self._use_market_breadth_chain:
                    chain = default_market_breadth_chain(finality=finality)
                    future = pool.submit(
                        chain.resolve,
                        trade_date=trade_date,
                        data_cutoff=request_cutoff,
                        finality=finality,
                    )
                else:
                    future = pool.submit(getattr(self.source, method), trade_date)
                futures[future] = key
            for future in as_completed(futures):
                key = futures[future]
                try:
                    batches[key] = future.result()
                except Exception as exc:
                    failures[key] = f"{type(exc).__name__}: {exc}"
        external_seconds = perf_counter() - external_started
        breadth_resolution = (
            batches.get("quotes")
            if isinstance(batches.get("quotes"), MarketBreadthResolution)
            else None
        )
        if breadth_resolution is not None and breadth_resolution.data is None:
            failures["quotes"] = "MarketBreadthUnavailable: all providers failed"
        self._validate_finality(
            trade_date=trade_date,
            finality=finality,
            confirmed_final=confirmed_final,
            batches=batches,
        )

        normalization_started = perf_counter()
        quality_flags: set[QualityFlag] = set()
        missing_fields: set[str] = set()
        if breadth_resolution is not None:
            quality_flags.update(breadth_resolution.quality_flags)
        if failures:
            quality_flags.add(QualityFlag.SOURCE_UNAVAILABLE)
        mapping = {
            "quotes": (QualityFlag.MISSING_UNIVERSE, "market_quotes"),
            "limit_up": (QualityFlag.MISSING_LIMIT_UP_POOL, "limit_up_pool"),
            "limit_down": (QualityFlag.MISSING_LIMIT_DOWN_POOL, "limit_down_pool"),
            "broken": (
                QualityFlag.MISSING_BROKEN_BOARD_POOL,
                "broken_board_pool",
            ),
            "strong": (
                QualityFlag.MISSING_STRONG_STOCK_POOL,
                "strong_stock_pool",
            ),
        }
        for key in failures:
            flag, missing = mapping[key]
            quality_flags.add(flag)
            missing_fields.add(missing)
            if failures[key].startswith("SourceSchemaChangedError"):
                quality_flags.add(QualityFlag.SOURCE_SCHEMA_CHANGED)
        if any(batch.eod_only for batch in batches.values()):
            quality_flags.add(QualityFlag.EOD_ONLY)

        def normalize(key: str, function: Callable[[pd.DataFrame], list]):
            batch = batches.get(key)
            if batch is None:
                return None
            try:
                return function(batch.frame)
            except Exception as exc:
                failures[key] = f"{type(exc).__name__}: {exc}"
                quality_flags.add(QualityFlag.SOURCE_SCHEMA_CHANGED)
                flag, missing = mapping[key]
                quality_flags.add(flag)
                missing_fields.add(missing)
                return None

        if breadth_resolution is not None:
            quotes = None if breadth_resolution.data is None else breadth_resolution.data.symbols
        else:
            quotes = normalize("quotes", self._normalize_quotes)
        limit_up = normalize("limit_up", self._normalize_limit_up)
        limit_down = normalize("limit_down", self._normalize_symbols)
        broken = normalize("broken", self._normalize_symbols)
        strong = normalize("strong", self._normalize_symbols)
        normalization_seconds = perf_counter() - normalization_started

        timing_values = list(batches.values())
        source_timestamp = max(
            (
                batch.source_timestamp
                for batch in timing_values
                if batch.source_timestamp is not None
            ),
            default=self.clock(),
        )
        available_time = max(
            (batch.available_time for batch in timing_values if batch.available_time is not None),
            default=self.clock(),
        )
        data_cutoff = max(available_time, self.clock())

        previous_market = self.repository.previous_final_snapshot(
            snapshot_type=SnapshotType.MARKET,
            before_trade_date=trade_date,
            cutoff=data_cutoff,
            record_source=record_source,
        )
        previous_speculation = self.repository.previous_final_snapshot(
            snapshot_type=SnapshotType.SPECULATION,
            before_trade_date=trade_date,
            cutoff=data_cutoff,
            record_source=record_source,
        )
        market_previous = (
            previous_market if isinstance(previous_market, MarketSentimentSnapshot) else None
        )
        speculation_previous = (
            previous_speculation
            if isinstance(previous_speculation, SpeculationSentimentSnapshot)
            else None
        )

        market_revision = speculation_revision = 1
        if finality == Finality.CORRECTED:
            market_target = self.repository.get_snapshot(corrects_market_snapshot_id or "")
            speculation_target = self.repository.get_snapshot(
                corrects_speculation_snapshot_id or ""
            )
            if not isinstance(market_target, MarketSentimentSnapshot):
                raise ValueError("CORRECTED collection requires a market correction target")
            if not isinstance(speculation_target, SpeculationSentimentSnapshot):
                raise ValueError("CORRECTED collection requires a speculation correction target")
            market_revision = market_target.meta.revision + 1
            speculation_revision = speculation_target.meta.revision + 1

        calculation_started = perf_counter()
        snapshot_time = max(data_cutoff, self.clock())
        selected_sources = {self.source.name}
        if breadth_resolution is not None and breadth_resolution.data is not None:
            selected_sources.add(breadth_resolution.data.source)
        if len(selected_sources) > 1:
            quality_flags.add(QualityFlag.MULTI_SOURCE)
        snapshot_source = (
            next(iter(selected_sources)) if len(selected_sources) == 1 else "MULTI_SOURCE"
        )
        base_context = dict(
            trade_date=trade_date,
            source=snapshot_source,
            source_timestamp=min(source_timestamp, available_time),
            available_time=available_time,
            data_cutoff=data_cutoff,
            snapshot_time=snapshot_time,
            finality=finality,
            record_source=record_source,
            data_version=f"{trade_date.isoformat()}:{source_timestamp.isoformat()}",
            created_at=snapshot_time,
            quality_flags=tuple(quality_flags),
        )
        field_provenance = (
            dict(breadth_resolution.data.field_provenance)
            if breadth_resolution is not None and breadth_resolution.data is not None
            else {}
        )
        pool_source = self.source.name
        field_provenance.update(
            {
                "limit_up_count": pool_source,
                "limit_down_count": pool_source,
                "limit_up_down_ratio": pool_source,
                "broken_board_count": pool_source,
                "broken_board_rate": pool_source,
                "strong_stock_count": pool_source,
            }
        )
        inputs = SnapshotInputs(
            quotes=quotes,
            limit_up=limit_up,
            limit_down_symbols=limit_down,
            broken_board_symbols=broken,
            strong_stock_symbols=strong,
            expected_universe_size=(
                breadth_resolution.data.expected_universe_size
                if breadth_resolution is not None and breadth_resolution.data is not None
                else (len(quotes) if quotes is not None else None)
            ),
            previous_market=market_previous,
            previous_speculation=speculation_previous,
            missing_fields=tuple(missing_fields),
            quality_flags=tuple(quality_flags),
            field_provenance=field_provenance,
            provider_health=(
                tuple(breadth_resolution.provider_health) if breadth_resolution is not None else ()
            ),
        )
        market = calculate_market_snapshot(
            SnapshotBuildContext(
                **base_context,
                revision=market_revision,
                corrects_snapshot_id=corrects_market_snapshot_id,
            ),
            inputs,
        )
        speculation = calculate_speculation_snapshot(
            SnapshotBuildContext(
                **base_context,
                revision=speculation_revision,
                corrects_snapshot_id=corrects_speculation_snapshot_id,
            ),
            inputs,
        )
        calculation_seconds = perf_counter() - calculation_started

        write_started = perf_counter()
        if persist:
            market = self.repository.save_snapshot(market)
            speculation = self.repository.save_snapshot(speculation)
        db_write_seconds = perf_counter() - write_started
        performance = CollectorPerformance(
            external_request_seconds=external_seconds,
            request_seconds_by_source={
                key: batch.request_seconds for key, batch in sorted(batches.items())
            },
            normalization_seconds=normalization_seconds,
            snapshot_calculation_seconds=calculation_seconds,
            db_write_seconds=db_write_seconds,
            total_seconds=perf_counter() - total_started,
            request_count=len(self._FETCHERS),
            failed_sources=dict(sorted(failures.items())),
        )
        return CollectionResult(
            market=market,
            speculation=speculation,
            performance=performance,
        )


__all__ = [
    "AKShareSentimentV3Source",
    "CollectionResult",
    "CollectorPerformance",
    "FetchedFrame",
    "SentimentV3Collector",
    "SentimentV3DataSource",
    "SourceSchemaChangedError",
]
