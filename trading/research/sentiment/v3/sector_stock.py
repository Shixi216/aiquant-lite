from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from statistics import mean, median
from time import perf_counter
from typing import Callable, Iterable, Protocol
from zoneinfo import ZoneInfo

from config.settings import settings
from trading.research.sentiment.v3.calculators import SnapshotBuildContext, _meta
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    Finality,
    MarketSentimentSnapshot,
    MarketUniverse,
    QualityFlag,
    RecordSource,
    SECTOR_ALGORITHM_VERSION,
    STOCK_ALGORITHM_VERSION,
    SectorMembership,
    SectorSentimentSnapshot,
    SectorType,
    SnapshotType,
    StockTradingSentimentSnapshot,
    UniverseScope,
    UniverseSecurity,
)
from trading.research.sentiment.v3.universe import build_market_universe


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LEADER_DEFINITION = "RETURN_RANK_V1"
CLV_VERSION = "CLV_V1"
GAP_PATH_VERSION = "GAP_PATH_V1"
TOP_N = 3


@dataclass(frozen=True)
class StockTradingBar:
    symbol: str
    close: float | None
    previous_close: float | None
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    amount: float | None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    return_pct: float | None = None


@dataclass(frozen=True)
class SectorStockRawData:
    source: str
    source_timestamp: datetime
    available_time: datetime
    securities: list[UniverseSecurity]
    memberships: list[SectorMembership]
    bars: list[StockTradingBar]
    previous_bars: list[StockTradingBar]
    source_universe_version: str | None
    request_seconds_by_source: dict[str, float] = field(default_factory=dict)
    field_provenance: dict[str, str] = field(default_factory=dict)
    eod_only: bool = True


class SectorStockDataSource(Protocol):
    name: str

    def fetch(self, trade_date: date) -> SectorStockRawData: ...


def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return None
    return datetime.strptime(text[:8], "%Y%m%d").date()


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


def _suspended_symbols(frame) -> set[str]:
    if frame.empty:
        return set()
    return set(frame.loc[frame["suspend_type"] == "S", "ts_code"].astype(str))


def _sector_id(name: str, sector_type: SectorType) -> str:
    import hashlib

    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"{sector_type.value}:{digest}"


def _board(symbol: str, market: str) -> str:
    code = symbol.split(".", 1)[0]
    if symbol.endswith(".BJ"):
        return "BEIJING"
    if code.startswith("300"):
        return "CHINEXT"
    if code.startswith("688"):
        return "STAR"
    return "SH_MAIN" if symbol.endswith(".SH") else "SZ_MAIN"


class TushareSectorStockSource:
    """Six bounded batch calls; no request is made per stock or per sector."""

    name = "Tushare daily/basic/current industry EOD"

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    @staticmethod
    def _client():
        if not settings.tushare_token or not settings.tushare_token.strip():
            raise RuntimeError("Tushare token is not configured")
        import tushare as ts

        ts.set_token(settings.tushare_token.strip())
        return ts.pro_api()

    @staticmethod
    def _timed(function: Callable, *args, **kwargs):
        started = perf_counter()
        result = function(*args, **kwargs)
        return result, perf_counter() - started

    def fetch(self, trade_date: date) -> SectorStockRawData:
        pro = self._client()
        key = trade_date.strftime("%Y%m%d")
        start = (trade_date - timedelta(days=14)).strftime("%Y%m%d")
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                "daily": pool.submit(self._timed, pro.daily, trade_date=key),
                "daily_basic": pool.submit(
                    self._timed,
                    pro.daily_basic,
                    trade_date=key,
                    fields="ts_code,trade_date,turnover_rate,volume_ratio",
                ),
                "stock_basic": pool.submit(
                    self._timed,
                    pro.stock_basic,
                    exchange="",
                    list_status="L",
                    fields=(
                        "ts_code,name,industry,market,exchange,list_status,list_date,delist_date"
                    ),
                ),
                "suspend": pool.submit(self._timed, pro.suspend_d, trade_date=key),
                "calendar": pool.submit(
                    self._timed,
                    pro.trade_cal,
                    exchange="",
                    start_date=start,
                    end_date=key,
                    is_open="1",
                ),
            }
            results = {name: future.result() for name, future in futures.items()}
        calendar = results["calendar"][0]
        dates = sorted(str(value) for value in calendar["cal_date"].tolist() if str(value) < key)
        if not dates:
            raise RuntimeError("previous open trade date is unavailable")
        previous_key = dates[-1]
        previous, previous_seconds = self._timed(pro.daily, trade_date=previous_key)

        daily = results["daily"][0]
        basic = results["daily_basic"][0]
        stocks = results["stock_basic"][0]
        suspensions = results["suspend"][0]
        if daily.empty or stocks.empty:
            raise RuntimeError("Tushare returned empty daily or stock_basic data")
        suspended = _suspended_symbols(suspensions)
        basic_by_symbol = {str(row.ts_code): row for row in basic.itertuples()}
        securities: list[UniverseSecurity] = []
        memberships: list[SectorMembership] = []
        now = self.clock()
        membership_rows: list[tuple[str, str]] = []
        for row in stocks.itertuples():
            symbol = str(row.ts_code)
            exchange = symbol.split(".", 1)[1]
            name = str(row.name or "")
            securities.append(
                UniverseSecurity(
                    symbol=symbol,
                    exchange=exchange,
                    board=_board(symbol, str(row.market or "")),
                    short_name=name,
                    list_date=_date(row.list_date),
                    delist_date=_date(row.delist_date),
                    listing_status=str(row.list_status),
                    is_st=name.upper().startswith(("ST", "*ST", "SST", "S*ST")),
                    is_suspended=symbol in suspended,
                )
            )
            industry = str(row.industry or "").strip()
            if industry and industry.lower() != "nan":
                membership_rows.append((symbol, industry))
        membership_digest = (
            __import__("hashlib")
            .sha256(canonical_json(sorted(membership_rows)).encode("utf-8"))
            .hexdigest()[:16]
        )
        membership_version = f"TUSHARE_CURRENT_INDUSTRY_V1_{membership_digest}"
        for symbol, industry in membership_rows:
            memberships.append(
                SectorMembership(
                    symbol=symbol,
                    sector_id=_sector_id(industry, SectorType.INDUSTRY),
                    sector_name=industry,
                    sector_type=SectorType.INDUSTRY,
                    available_time=now,
                    source="Tushare Pro / stock_basic current industry",
                    membership_version=membership_version,
                    pit_safe_for_history=False,
                )
            )

        def bars_from(frame, *, enrich: bool) -> list[StockTradingBar]:
            values: list[StockTradingBar] = []
            for row in frame.itertuples():
                symbol = str(row.ts_code)
                detail = basic_by_symbol.get(symbol) if enrich else None
                values.append(
                    StockTradingBar(
                        symbol=symbol,
                        close=_number(row.close),
                        previous_close=_number(row.pre_close),
                        open=_number(row.open),
                        high=_number(row.high),
                        low=_number(row.low),
                        volume=_number(row.vol),
                        amount=(
                            None if _number(row.amount) is None else _number(row.amount) * 1000
                        ),
                        turnover_rate=(
                            None
                            if detail is None or _number(detail.turnover_rate) is None
                            else _number(detail.turnover_rate) / 100
                        ),
                        volume_ratio=(None if detail is None else _number(detail.volume_ratio)),
                        return_pct=(
                            None if _number(row.pct_chg) is None else _number(row.pct_chg) / 100
                        ),
                    )
                )
            return values

        timestamp = datetime.combine(trade_date, time(15, 0), tzinfo=SHANGHAI_TZ)
        timings = {name: value[1] for name, value in results.items()}
        timings["previous_daily"] = previous_seconds
        source_version_hash = (
            __import__("hashlib")
            .sha256(canonical_json(sorted(item.symbol for item in securities)).encode("utf-8"))
            .hexdigest()[:16]
        )
        return SectorStockRawData(
            source=self.name,
            source_timestamp=timestamp,
            available_time=now,
            securities=securities,
            memberships=memberships,
            bars=bars_from(daily, enrich=True),
            previous_bars=bars_from(previous, enrich=False),
            source_universe_version=f"TUSHARE_LISTED_V1_{source_version_hash}",
            request_seconds_by_source=timings,
            field_provenance={
                "prices": "Tushare Pro / daily",
                "volume_amount": "Tushare Pro / daily",
                "turnover_rate": "Tushare Pro / daily_basic",
                "volume_ratio": "Tushare Pro / daily_basic",
                "membership": "Tushare Pro / stock_basic current industry",
                "suspension": "Tushare Pro / suspend_d",
            },
            eod_only=True,
        )


def _ratio(current: float | None, previous: float | None) -> float | None:
    return (
        (current - previous) / previous
        if current is not None and previous not in {None, 0}
        else None
    )


def _returns(bar: StockTradingBar) -> float | None:
    if bar.return_pct is not None:
        return bar.return_pct
    if bar.close is None or bar.previous_close in {None, 0}:
        return None
    return bar.close / bar.previous_close - 1


def calculate_sector_stock_snapshots(
    *,
    context: SnapshotBuildContext,
    universe: MarketUniverse,
    market: MarketSentimentSnapshot | None,
    memberships: Iterable[SectorMembership],
    bars: Iterable[StockTradingBar],
    previous_bars: Iterable[StockTradingBar] = (),
    field_provenance: dict[str, str] | None = None,
    timings: dict[str, float] | None = None,
) -> tuple[list[SectorSentimentSnapshot], list[StockTradingSentimentSnapshot]]:
    universe_symbols = set(universe.symbols)
    bar_map = {item.symbol: item for item in bars if item.symbol in universe_symbols}
    previous_map = {item.symbol: item for item in previous_bars}
    memberships = [
        item
        for item in memberships
        if item.symbol in universe_symbols and item.available_time <= context.data_cutoff
    ]
    by_sector: dict[str, list[SectorMembership]] = defaultdict(list)
    by_symbol: dict[str, list[SectorMembership]] = defaultdict(list)
    for item in memberships:
        by_sector[item.sector_id].append(item)
        by_symbol[item.symbol].append(item)

    market_complete = market is not None and market.breadth_denominator is not None
    observed_returns = [_returns(item) for item in bar_map.values()]
    valid_market_returns = [value for value in observed_returns if value is not None]
    market_return = mean(valid_market_returns) if market_complete and valid_market_returns else None
    market_advance_ratio = (
        sum(value > 0 for value in valid_market_returns) / len(valid_market_returns)
        if market_complete and valid_market_returns
        else None
    )
    current_market_volume = sum(item.volume for item in bar_map.values() if item.volume is not None)
    prior_market_volume_values = [
        previous_map[symbol].volume
        for symbol in bar_map
        if symbol in previous_map and previous_map[symbol].volume is not None
    ]
    market_volume_change = (
        _ratio(current_market_volume, sum(prior_market_volume_values))
        if len(prior_market_volume_values) == len(bar_map)
        else None
    )
    limit_up = (
        None if market is None or market.limit_up_symbols is None else set(market.limit_up_symbols)
    )
    limit_down = (
        None
        if market is None or market.limit_down_symbols is None
        else set(market.limit_down_symbols)
    )
    broken = (
        None
        if market is None or market.broken_board_symbols is None
        else set(market.broken_board_symbols)
    )
    provenance = dict(sorted((field_provenance or {}).items()))

    sector_started = perf_counter()
    sector_snapshots: list[SectorSentimentSnapshot] = []
    sector_returns: dict[str, float | None] = {}
    sector_volumes: dict[str, float | None] = {}
    for sector_id, sector_memberships in sorted(by_sector.items()):
        first = sector_memberships[0]
        members = sorted({item.symbol for item in sector_memberships})
        observed = [bar_map[symbol] for symbol in members if symbol in bar_map]
        returns = [(bar.symbol, _returns(bar)) for bar in observed]
        valid = [(symbol, value) for symbol, value in returns if value is not None]
        values = [value for _, value in valid]
        flags = set(context.quality_flags)
        missing: set[str] = set()
        if any(not item.pit_safe_for_history for item in sector_memberships):
            flags.update({QualityFlag.CURRENT_MEMBERSHIP_ONLY, QualityFlag.PIT_UNSAFE_FOR_HISTORY})
        if len(observed) < len(members):
            flags.add(QualityFlag.PARTIAL_DATA)
            missing.add("sector_members")
        sector_mean = mean(values) if values else None
        sector_median = median(values) if values else None
        sector_returns[sector_id] = sector_mean
        advance = sum(value > 0 for value in values) if values else None
        decline = sum(value < 0 for value in values) if values else None
        unchanged = sum(value == 0 for value in values) if values else None
        advance_ratio = advance / len(values) if advance is not None and values else None
        turnover_values = [bar.amount for bar in observed if bar.amount is not None]
        sector_turnover = sum(turnover_values) if len(turnover_values) == len(observed) else None
        prior_amounts = [
            previous_map[bar.symbol].amount
            for bar in observed
            if bar.symbol in previous_map and previous_map[bar.symbol].amount is not None
        ]
        previous_turnover = sum(prior_amounts) if len(prior_amounts) == len(observed) else None
        turnover_change = _ratio(sector_turnover, previous_turnover)
        current_volumes = [bar.volume for bar in observed if bar.volume is not None]
        prior_volumes = [
            previous_map[bar.symbol].volume
            for bar in observed
            if bar.symbol in previous_map and previous_map[bar.symbol].volume is not None
        ]
        volume_change = (
            _ratio(sum(current_volumes), sum(prior_volumes))
            if len(current_volumes) == len(observed) and len(prior_volumes) == len(observed)
            else None
        )
        sector_volumes[sector_id] = volume_change
        if turnover_change is None or volume_change is None:
            flags.add(QualityFlag.MISSING_PREVIOUS_QUOTE)
            missing.update({"turnover_change", "volume_change"})
        up_count = None if limit_up is None else len(set(members) & limit_up)
        down_count = None if limit_down is None else len(set(members) & limit_down)
        broken_count = None if broken is None else len(set(members) & broken)
        if limit_up is None:
            flags.add(QualityFlag.MISSING_LIMIT_UP_POOL)
            missing.update({"limit_up_count", "limit_up_ratio"})
        if limit_down is None:
            flags.add(QualityFlag.MISSING_LIMIT_DOWN_POOL)
            missing.add("limit_down_count")
        if broken is None:
            flags.add(QualityFlag.MISSING_BROKEN_BOARD_POOL)
            missing.update({"broken_board_count", "broken_board_rate"})
        limit_ratio = up_count / len(members) if up_count is not None and members else None
        broken_denominator = (
            up_count + broken_count if up_count is not None and broken_count is not None else None
        )
        broken_rate = broken_count / broken_denominator if broken_denominator else None
        if not market_complete:
            if market is None:
                flags.add(QualityFlag.MISSING_MARKET_SNAPSHOT)
            missing.update({"relative_market_return", "relative_market_breadth"})
        relative_return = (
            sector_mean - market_return
            if sector_mean is not None and market_return is not None
            else None
        )
        relative_breadth = (
            advance_ratio - market_advance_ratio
            if advance_ratio is not None and market_advance_ratio is not None
            else None
        )
        ranked = sorted(valid, key=lambda item: (-item[1], item[0]))
        leader_symbol, leader_return = ranked[0] if ranked else (None, None)
        top_values = [item[1] for item in ranked[:TOP_N]]
        payload = {
            "sector_id": sector_id,
            "sector_name": first.sector_name,
            "sector_type": first.sector_type,
            "universe_id": universe.universe_id,
            "universe_version": universe.universe_version,
            "market_snapshot_id": None if market is None else market.meta.snapshot_id,
            "membership_version": first.membership_version,
            "member_count": len(members),
            "observed_member_count": len(observed),
            "advancers": advance,
            "decliners": decline,
            "unchanged": unchanged,
            "advance_ratio": advance_ratio,
            "mean_return": sector_mean,
            "median_return": sector_median,
            "sector_turnover": sector_turnover,
            "previous_sector_turnover": previous_turnover,
            "turnover_change": turnover_change,
            "volume_change": volume_change,
            "limit_up_count": up_count,
            "limit_down_count": down_count,
            "broken_board_count": broken_count,
            "limit_up_ratio": limit_ratio,
            "broken_board_rate": broken_rate,
            "market_return": market_return,
            "market_advance_ratio": market_advance_ratio,
            "relative_market_return": relative_return,
            "relative_market_breadth": relative_breadth,
            "leader_symbol": leader_symbol,
            "leader_return": leader_return,
            "leader_definition": LEADER_DEFINITION,
            "top_n": TOP_N,
            "top_n_mean_return": mean(top_values) if top_values else None,
            "top_n_median_return": median(top_values) if top_values else None,
            "field_provenance": provenance,
        }
        sector_context = replace(context, symbol_scope=sector_id)
        meta = _meta(
            context=sector_context,
            snapshot_type=SnapshotType.SECTOR,
            algorithm_version=SECTOR_ALGORITHM_VERSION,
            normalized_payload=payload,
            coverage_ratio=(len(observed) / len(members) if members else 0),
            quality_flags=flags,
            missing_fields=missing,
        )
        sector_snapshots.append(SectorSentimentSnapshot(meta=meta, **payload))

    if timings is not None:
        timings["sector"] = perf_counter() - sector_started
    stock_started = perf_counter()
    sector_by_id = {item.sector_id: item for item in sector_snapshots}
    stock_snapshots: list[StockTradingSentimentSnapshot] = []
    for symbol, bar in sorted(bar_map.items()):
        flags = set(context.quality_flags)
        missing: set[str] = set()
        symbol_memberships = by_symbol.get(symbol, [])
        industry = next(
            (item for item in symbol_memberships if item.sector_type == SectorType.INDUSTRY),
            None,
        )
        concepts = sorted(
            item.sector_id for item in symbol_memberships if item.sector_type == SectorType.CONCEPT
        )
        if industry is None:
            flags.add(QualityFlag.MISSING_SECTOR_MEMBERSHIP)
            missing.update({"relative_sector_return", "relative_sector_volume"})
        elif not industry.pit_safe_for_history:
            flags.update({QualityFlag.CURRENT_MEMBERSHIP_ONLY, QualityFlag.PIT_UNSAFE_FOR_HISTORY})
        sector_snapshot = None if industry is None else sector_by_id.get(industry.sector_id)
        stock_return = _returns(bar)
        open_gap = (
            bar.open / bar.previous_close - 1
            if bar.open is not None and bar.previous_close not in {None, 0}
            else None
        )
        intraday = (
            bar.close / bar.open - 1
            if bar.close is not None and bar.open not in {None, 0}
            else None
        )
        amplitude = (
            (bar.high - bar.low) / bar.previous_close
            if bar.high is not None and bar.low is not None and bar.previous_close not in {None, 0}
            else None
        )
        clv = (
            (bar.close - bar.low) / (bar.high - bar.low)
            if bar.close is not None
            and bar.high is not None
            and bar.low is not None
            and bar.high != bar.low
            else None
        )
        if bar.high == bar.low and bar.high is not None:
            missing.add("close_location_value")
        high_drawdown = (
            (bar.high - bar.close) / bar.previous_close
            if bar.high is not None
            and bar.close is not None
            and bar.previous_close not in {None, 0}
            else None
        )
        low_recovery = (
            (bar.close - bar.low) / bar.previous_close
            if bar.low is not None and bar.close is not None and bar.previous_close not in {None, 0}
            else None
        )
        direction = 0 if open_gap in {None, 0} else (1 if open_gap > 0 else -1)
        path = None if intraday is None or open_gap is None else direction * intraday
        follow = None if path is None else max(0.0, path)
        fade = None if path is None else max(0.0, -path)
        previous = previous_map.get(symbol)
        volume_change = None if previous is None else _ratio(bar.volume, previous.volume)
        turnover_change = None if previous is None else _ratio(bar.amount, previous.amount)
        if previous is None:
            flags.add(QualityFlag.MISSING_PREVIOUS_QUOTE)
            missing.update({"volume_change", "turnover_change"})
        sector_return = None if sector_snapshot is None else sector_snapshot.mean_return
        sector_volume_change = None if sector_snapshot is None else sector_snapshot.volume_change
        if market_return is None:
            missing.add("relative_market_return")
        if sector_return is None:
            missing.add("relative_sector_return")
        if limit_up is None:
            flags.add(QualityFlag.MISSING_LIMIT_UP_POOL)
            missing.add("is_limit_up")
        if limit_down is None:
            flags.add(QualityFlag.MISSING_LIMIT_DOWN_POOL)
            missing.add("is_limit_down")
        if broken is None:
            flags.add(QualityFlag.MISSING_BROKEN_BOARD_POOL)
            missing.add("is_broken_board")
        payload = {
            "symbol": symbol,
            "universe_id": universe.universe_id,
            "universe_version": universe.universe_version,
            "industry_sector_id": None if industry is None else industry.sector_id,
            "concept_sector_ids": concepts,
            "return_pct": stock_return,
            "open_gap_pct": open_gap,
            "intraday_return_pct": intraday,
            "amplitude": amplitude,
            "turnover_rate": bar.turnover_rate,
            "volume_ratio": bar.volume_ratio,
            "volume_change": volume_change,
            "turnover_change": turnover_change,
            "close_location_value": clv,
            "close_location_version": CLV_VERSION,
            "high_to_close_drawdown": high_drawdown,
            "low_to_close_recovery": low_recovery,
            "gap_and_fade": fade,
            "gap_and_follow_through": follow,
            "gap_path_version": GAP_PATH_VERSION,
            "market_return": market_return,
            "sector_return": sector_return,
            "relative_market_return": None
            if stock_return is None or market_return is None
            else stock_return - market_return,
            "relative_sector_return": None
            if stock_return is None or sector_return is None
            else stock_return - sector_return,
            "market_volume_change": market_volume_change,
            "sector_volume_change": sector_volume_change,
            "relative_market_volume": None
            if volume_change is None or market_volume_change is None
            else volume_change - market_volume_change,
            "relative_sector_volume": None
            if volume_change is None or sector_volume_change is None
            else volume_change - sector_volume_change,
            "is_limit_up": None if limit_up is None else symbol in limit_up,
            "is_limit_down": None if limit_down is None else symbol in limit_down,
            "is_broken_board": None if broken is None else symbol in broken,
            "market_snapshot_id": None if market is None else market.meta.snapshot_id,
            "sector_snapshot_id": None
            if sector_snapshot is None
            else sector_snapshot.meta.snapshot_id,
            "field_provenance": provenance,
        }
        stock_context = replace(context, symbol_scope=symbol)
        meta = _meta(
            context=stock_context,
            snapshot_type=SnapshotType.STOCK,
            algorithm_version=STOCK_ALGORITHM_VERSION,
            normalized_payload=payload,
            coverage_ratio=(
                sum(
                    value is not None
                    for value in (
                        stock_return,
                        open_gap,
                        amplitude,
                        clv,
                        bar.turnover_rate,
                        bar.volume_ratio,
                    )
                )
                / 6
            ),
            quality_flags=flags,
            missing_fields=missing,
        )
        stock_snapshots.append(StockTradingSentimentSnapshot(meta=meta, **payload))
    if timings is not None:
        timings["stock"] = perf_counter() - stock_started
    return sector_snapshots, stock_snapshots


@dataclass(frozen=True)
class SectorStockPerformance:
    universe_build_seconds: float
    external_request_seconds: float
    request_seconds_by_source: dict[str, float]
    sector_calculation_seconds: float
    stock_calculation_seconds: float
    db_write_seconds: float
    total_seconds: float


@dataclass(frozen=True)
class SectorStockCollectionResult:
    universe: MarketUniverse
    sectors: list[SectorSentimentSnapshot]
    stocks: list[StockTradingSentimentSnapshot]
    performance: SectorStockPerformance


class SectorStockSentimentCollector:
    def __init__(
        self,
        *,
        repository: SentimentV3Repository | None = None,
        source: SectorStockDataSource | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository or SentimentV3Repository()
        self.source = source or TushareSectorStockSource()
        self.clock = clock or (lambda: datetime.now(tz=SHANGHAI_TZ))

    def collect(
        self,
        *,
        trade_date: date,
        finality: Finality = Finality.FINAL,
        record_source: RecordSource = RecordSource.REAL,
        market_snapshot: MarketSentimentSnapshot | None = None,
        persist: bool = True,
    ) -> SectorStockCollectionResult:
        total_started = perf_counter()
        request_started = perf_counter()
        raw = self.source.fetch(trade_date)
        external_seconds = perf_counter() - request_started
        if finality == Finality.INTRADAY and raw.eod_only:
            raise ValueError("EOD-only sector/stock source cannot be labelled INTRADAY")
        now = self.clock()
        data_cutoff = max(raw.available_time, now)
        if market_snapshot is None:
            found = self.repository.get_snapshot_as_of(
                snapshot_type=SnapshotType.MARKET,
                cutoff=data_cutoff,
                record_source=record_source,
            )
            market_snapshot = found if isinstance(found, MarketSentimentSnapshot) else None
        universe_started = perf_counter()
        universe = build_market_universe(
            trade_date=trade_date,
            as_of=data_cutoff,
            securities=raw.securities,
            observed_symbols=(item.symbol for item in raw.bars),
            source=raw.source,
            source_universe_version=raw.source_universe_version,
            scope=UniverseScope.BREADTH_ELIGIBLE,
            memberships=raw.memberships,
            created_at=data_cutoff,
        )
        universe_seconds = perf_counter() - universe_started
        flags = {QualityFlag.EOD_ONLY} if raw.eod_only else set()
        if market_snapshot is None:
            flags.add(QualityFlag.MISSING_MARKET_SNAPSHOT)
        context = SnapshotBuildContext(
            trade_date=trade_date,
            source=raw.source,
            source_timestamp=min(raw.source_timestamp, raw.available_time),
            available_time=raw.available_time,
            data_cutoff=data_cutoff,
            snapshot_time=data_cutoff,
            finality=finality,
            record_source=record_source,
            data_version=f"{trade_date.isoformat()}:{raw.source_universe_version}",
            created_at=data_cutoff,
            quality_flags=tuple(flags),
        )
        calculation_timings: dict[str, float] = {}
        sectors, stocks = calculate_sector_stock_snapshots(
            context=context,
            universe=universe,
            market=market_snapshot,
            memberships=raw.memberships,
            bars=raw.bars,
            previous_bars=raw.previous_bars,
            field_provenance=raw.field_provenance,
            timings=calculation_timings,
        )
        sector_seconds = calculation_timings["sector"]
        stock_seconds = calculation_timings["stock"]
        write_started = perf_counter()
        if persist:
            universe = self.repository.save_universe(universe)
            persisted = self.repository.save_snapshots([*sectors, *stocks])
            sectors = [item for item in persisted if isinstance(item, SectorSentimentSnapshot)]
            stocks = [item for item in persisted if isinstance(item, StockTradingSentimentSnapshot)]
        db_seconds = perf_counter() - write_started
        return SectorStockCollectionResult(
            universe=universe,
            sectors=sectors,
            stocks=stocks,
            performance=SectorStockPerformance(
                universe_build_seconds=universe_seconds,
                external_request_seconds=external_seconds,
                request_seconds_by_source=dict(sorted(raw.request_seconds_by_source.items())),
                sector_calculation_seconds=sector_seconds,
                stock_calculation_seconds=stock_seconds,
                db_write_seconds=db_seconds,
                total_seconds=perf_counter() - total_started,
            ),
        )


__all__ = [
    "CLV_VERSION",
    "GAP_PATH_VERSION",
    "LEADER_DEFINITION",
    "SectorStockCollectionResult",
    "SectorStockDataSource",
    "SectorStockPerformance",
    "SectorStockRawData",
    "SectorStockSentimentCollector",
    "StockTradingBar",
    "TushareSectorStockSource",
    "calculate_sector_stock_snapshots",
]
