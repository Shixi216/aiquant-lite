from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from statistics import median
from time import perf_counter
from typing import Iterable, Protocol
from zoneinfo import ZoneInfo

from trading.research.sentiment.v3.calculators import SnapshotBuildContext, _meta
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    CapitalMethodologyClass,
    DRAGON_TIGER_ALGORITHM_VERSION,
    DragonTigerSnapshot,
    FieldMethodology,
    Finality,
    MARGIN_ALGORITHM_VERSION,
    MARKET_CAPITAL_ALGORITHM_VERSION,
    MarginFinancingSnapshot,
    MarketCapitalSnapshot,
    MarketUniverse,
    MethodologyTransparency,
    QualityFlag,
    RecordSource,
    SECTOR_CAPITAL_ALGORITHM_VERSION,
    STOCK_CAPITAL_ALGORITHM_VERSION,
    SectorCapitalSnapshot,
    SectorMembership,
    SectorType,
    SnapshotType,
    StockCapitalSnapshot,
)

CNY = "CNY"
SHARES = "SHARES"
DECIMAL = "DECIMAL"


@dataclass(frozen=True)
class CapitalStockRow:
    symbol: str
    turnover: float | None
    previous_turnover: float | None
    turnover_rate: float | None
    vendor_small_net_flow: float | None
    vendor_medium_net_flow: float | None
    vendor_large_net_flow: float | None
    vendor_super_large_net_flow: float | None
    vendor_net_flow: float | None

    @property
    def vendor_main_net_flow(self) -> float | None:
        if self.vendor_large_net_flow is None or self.vendor_super_large_net_flow is None:
            return None
        return self.vendor_large_net_flow + self.vendor_super_large_net_flow


@dataclass(frozen=True)
class CapitalRawData:
    source: str
    source_timestamp: datetime
    available_time: datetime
    rows: list[CapitalStockRow] | None
    previous_rows: list[CapitalStockRow] | None = None
    vendor_total_buy_amount: float | None = None
    vendor_total_sell_amount: float | None = None
    quality_flags: tuple[QualityFlag, ...] = ()
    field_provenance: dict[str, str] = field(default_factory=dict)
    request_seconds: dict[str, float] = field(default_factory=dict)
    methodology_compatible: bool = True


@dataclass(frozen=True)
class MarginRow:
    symbol: str
    margin_balance: float | None
    margin_buy: float | None
    margin_repay: float | None
    short_balance: float | None
    short_sell: float | None
    short_repay: float | None
    total_margin_balance: float | None


@dataclass(frozen=True)
class MarginRawData:
    source: str
    source_timestamp: datetime
    available_time: datetime
    rows: list[MarginRow] | None
    previous_rows: list[MarginRow] | None
    market_margin_total: float | None
    quality_flags: tuple[QualityFlag, ...] = ()
    request_seconds: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class DragonTigerEvent:
    symbol: str
    reasons: tuple[str, ...]
    buy_amount: float | None
    sell_amount: float | None
    net_amount: float | None
    institution_buy_amount: float | None
    institution_sell_amount: float | None
    institution_net_amount: float | None
    seat_count: int
    institution_seat_count: int
    top_buyers: tuple[str, ...]
    top_sellers: tuple[str, ...]


@dataclass(frozen=True)
class DragonTigerRawData:
    source: str
    source_timestamp: datetime
    available_time: datetime
    provider_succeeded: bool
    events: list[DragonTigerEvent]
    quality_flags: tuple[QualityFlag, ...] = ()
    request_seconds: dict[str, float] = field(default_factory=dict)


class CapitalDataSource(Protocol):
    def fetch(self, trade_date: date) -> CapitalRawData: ...


class MarginDataSource(Protocol):
    def fetch(self, trade_date: date) -> MarginRawData: ...


class DragonTigerDataSource(Protocol):
    def fetch(self, trade_date: date) -> DragonTigerRawData: ...


def _methodology() -> dict[str, FieldMethodology]:
    fact = FieldMethodology(
        methodology_class=CapitalMethodologyClass.OBSERVABLE_FACT,
        transparency=MethodologyTransparency.FULL,
        unit=CNY,
    )
    vendor = FieldMethodology(
        methodology_class=CapitalMethodologyClass.VENDOR_DERIVED,
        transparency=MethodologyTransparency.PARTIAL,
        unit=CNY,
        source_field="Tushare moneyflow order-size buckets",
    )
    derived = FieldMethodology(
        methodology_class=CapitalMethodologyClass.DERIVED_TRANSPARENT,
        transparency=MethodologyTransparency.FULL,
        unit=DECIMAL,
        formula_version="CAPITAL_RELATIVE_RATIO_V1",
    )
    return {"fact": fact, "vendor": vendor, "derived": derived}


def _turnover_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return current / previous - 1.0


def _flow_ratio(flow: float | None, turnover: float | None) -> float | None:
    if flow is None or turnover is None or turnover == 0:
        return None
    return flow / turnover


def _coverage(observed: int, expected: int) -> float:
    return observed / expected if expected else 0.0


def calculate_capital_snapshots(
    *,
    context: SnapshotBuildContext,
    raw: CapitalRawData,
    universe: MarketUniverse,
    memberships: Iterable[SectorMembership] = (),
    market_snapshot_id: str | None = None,
    sector_snapshot_ids: dict[str, str] | None = None,
    stock_snapshot_ids: dict[str, str] | None = None,
) -> tuple[MarketCapitalSnapshot, list[SectorCapitalSnapshot], list[StockCapitalSnapshot]]:
    flags = set(context.quality_flags) | set(raw.quality_flags) | {QualityFlag.EOD_ONLY}
    rows = raw.rows
    missing: set[str] = set()
    if rows is None:
        rows = []
        flags.update({QualityFlag.SOURCE_UNAVAILABLE, QualityFlag.MISSING_CAPITAL_DATA})
        missing.update({"market_turnover", "vendor_net_flow", "stock_capital"})
    universe_symbols = set(universe.symbols)
    row_map = {row.symbol: row for row in rows if row.symbol in universe_symbols}
    expected = universe.eligible_symbol_count
    observed = len(row_map)
    if observed < expected:
        flags.add(QualityFlag.PARTIAL_DATA)
    market_turnover = sum(row.turnover for row in row_map.values() if row.turnover is not None)
    if not row_map or all(row.turnover is None for row in row_map.values()):
        market_turnover = None
        missing.add("market_turnover")
    previous_turnover = sum(
        row.previous_turnover for row in row_map.values() if row.previous_turnover is not None
    )
    if not row_map or all(row.previous_turnover is None for row in row_map.values()):
        previous_turnover = None
    vendor_net = sum(
        row.vendor_net_flow for row in row_map.values() if row.vendor_net_flow is not None
    )
    main_net = sum(
        value for row in row_map.values() if (value := row.vendor_main_net_flow) is not None
    )
    if not row_map or all(row.vendor_net_flow is None for row in row_map.values()):
        vendor_net = None
    if not row_map or all(row.vendor_main_net_flow is None for row in row_map.values()):
        main_net = None
        missing.add("vendor_main_net_flow")
    market_main_ratio = _flow_ratio(main_net, market_turnover)
    methods = _methodology()
    market_payload = {
        "universe_id": universe.universe_id,
        "market_snapshot_id": market_snapshot_id,
        "expected": expected,
        "observed": observed,
        "market_turnover": market_turnover,
        "previous_market_turnover": previous_turnover,
        "vendor_total_buy_amount": raw.vendor_total_buy_amount,
        "vendor_total_sell_amount": raw.vendor_total_sell_amount,
        "vendor_net_flow": vendor_net,
        "vendor_main_net_flow": main_net,
    }
    market_meta = _meta(
        context=context,
        snapshot_type=SnapshotType.MARKET_CAPITAL,
        algorithm_version=MARKET_CAPITAL_ALGORITHM_VERSION,
        normalized_payload=market_payload,
        coverage_ratio=_coverage(observed, expected),
        quality_flags=set(flags),
        missing_fields=set(missing),
    )
    market = MarketCapitalSnapshot(
        meta=market_meta,
        universe_id=universe.universe_id,
        universe_version=universe.universe_version,
        market_snapshot_id=market_snapshot_id,
        expected_symbol_count=expected,
        observed_symbol_count=observed,
        market_turnover=market_turnover,
        previous_market_turnover=previous_turnover,
        market_turnover_change=_turnover_change(market_turnover, previous_turnover),
        vendor_total_buy_amount=raw.vendor_total_buy_amount,
        vendor_total_sell_amount=raw.vendor_total_sell_amount,
        vendor_net_flow=vendor_net,
        vendor_main_net_flow=main_net,
        vendor_main_flow_ratio=market_main_ratio,
        field_provenance=dict(raw.field_provenance),
        field_methodology={
            "market_turnover": methods["fact"],
            "market_turnover_change": methods["derived"].model_copy(
                update={"formula_version": "TURNOVER_CHANGE_V1"}
            ),
            "vendor_total_buy_amount": methods["vendor"],
            "vendor_total_sell_amount": methods["vendor"],
            "vendor_net_flow": methods["vendor"],
            "vendor_main_net_flow": methods["vendor"],
            "vendor_main_flow_ratio": methods["vendor"].model_copy(
                update={"unit": DECIMAL, "formula_version": "VENDOR_MAIN_FLOW_RATIO_V1"}
            ),
        },
    )

    membership_by_symbol: dict[str, SectorMembership] = {}
    members_by_sector: dict[str, list[str]] = defaultdict(list)
    sector_info: dict[str, SectorMembership] = {}
    for membership in memberships:
        if membership.sector_type == SectorType.INDUSTRY and membership.symbol in universe_symbols:
            if membership.symbol in row_map:
                membership_by_symbol[membership.symbol] = membership
            members_by_sector[membership.sector_id].append(membership.symbol)
            sector_info[membership.sector_id] = membership

    sectors: list[SectorCapitalSnapshot] = []
    sector_ratios: dict[str, float | None] = {}
    sector_ids = sector_snapshot_ids or {}
    for sector_id, symbols in sorted(members_by_sector.items()):
        info = sector_info[sector_id]
        sector_rows = [row_map[symbol] for symbol in symbols if symbol in row_map]
        turnover = sum(row.turnover for row in sector_rows if row.turnover is not None)
        previous = sum(
            row.previous_turnover for row in sector_rows if row.previous_turnover is not None
        )
        main_values = [
            row.vendor_main_net_flow for row in sector_rows if row.vendor_main_net_flow is not None
        ]
        large_values = [
            row.vendor_large_net_flow
            for row in sector_rows
            if row.vendor_large_net_flow is not None
        ]
        small_values = [
            row.vendor_small_net_flow
            for row in sector_rows
            if row.vendor_small_net_flow is not None
        ]
        if all(row.turnover is None for row in sector_rows):
            turnover = None
        if all(row.previous_turnover is None for row in sector_rows):
            previous = None
        main_sum = sum(main_values) if main_values else None
        ratio = _flow_ratio(main_sum, turnover)
        sector_ratios[sector_id] = ratio
        sector_flags = set(flags)
        if not info.pit_safe_for_history:
            sector_flags.update(
                {QualityFlag.CURRENT_MEMBERSHIP_ONLY, QualityFlag.PIT_UNSAFE_FOR_HISTORY}
            )
        if len(sector_rows) < len(symbols):
            sector_flags.add(QualityFlag.PARTIAL_DATA)
        relative_flow = (
            ratio - market_main_ratio
            if raw.methodology_compatible and ratio is not None and market_main_ratio is not None
            else None
        )
        if relative_flow is None:
            sector_flags.add(
                QualityFlag.INCOMPATIBLE_METHODOLOGY
                if main_sum is not None
                else QualityFlag.MISSING_CAPITAL_DATA
            )
        payload = {
            "sector_id": sector_id,
            "symbols": sorted(symbols),
            "turnover": turnover,
            "main_sum": main_sum,
        }
        sector_context = replace(context, symbol_scope=f"SECTOR:{sector_id}")
        sector_meta = _meta(
            context=sector_context,
            snapshot_type=SnapshotType.SECTOR_CAPITAL,
            algorithm_version=SECTOR_CAPITAL_ALGORITHM_VERSION,
            normalized_payload=payload,
            coverage_ratio=_coverage(len(sector_rows), len(symbols)),
            quality_flags=sector_flags,
            missing_fields=set(),
        )
        sectors.append(
            SectorCapitalSnapshot(
                meta=sector_meta,
                sector_id=sector_id,
                sector_name=info.sector_name,
                sector_type=info.sector_type,
                universe_id=universe.universe_id,
                universe_version=universe.universe_version,
                membership_version=info.membership_version,
                market_capital_snapshot_id=market.meta.snapshot_id,
                sector_snapshot_id=sector_ids.get(sector_id),
                member_count=len(symbols),
                observed_member_count=len(sector_rows),
                sector_turnover=turnover,
                previous_sector_turnover=previous,
                turnover_change=_turnover_change(turnover, previous),
                vendor_main_flow_sum=main_sum,
                vendor_main_flow_median=median(main_values) if main_values else None,
                vendor_large_flow_sum=sum(large_values) if large_values else None,
                vendor_small_flow_sum=sum(small_values) if small_values else None,
                vendor_main_flow_ratio=ratio,
                relative_market_turnover=(
                    turnover / market_turnover if turnover is not None and market_turnover else None
                ),
                relative_market_flow=relative_flow,
                field_provenance=dict(raw.field_provenance),
                field_methodology={
                    "sector_turnover": methods["fact"],
                    "vendor_main_flow_sum": methods["vendor"],
                    "relative_market_flow": methods["vendor"].model_copy(
                        update={"unit": DECIMAL, "formula_version": "CAPITAL_RELATIVE_RATIO_V1"}
                    ),
                },
            )
        )

    stocks: list[StockCapitalSnapshot] = []
    stock_ids = stock_snapshot_ids or {}
    sector_capital_ids = {item.sector_id: item.meta.snapshot_id for item in sectors}
    for symbol, row in sorted(row_map.items()):
        membership = membership_by_symbol.get(symbol)
        sector_id = membership.sector_id if membership else None
        ratio = _flow_ratio(row.vendor_main_net_flow, row.turnover)
        sector_ratio = sector_ratios.get(sector_id) if sector_id else None
        stock_flags = set(flags)
        relative_market = (
            ratio - market_main_ratio
            if raw.methodology_compatible and ratio is not None and market_main_ratio is not None
            else None
        )
        relative_sector = (
            ratio - sector_ratio
            if raw.methodology_compatible and ratio is not None and sector_ratio is not None
            else None
        )
        if not raw.methodology_compatible:
            stock_flags.add(QualityFlag.INCOMPATIBLE_METHODOLOGY)
        if membership is None:
            stock_flags.add(QualityFlag.MISSING_SECTOR_MEMBERSHIP)
        if row.vendor_main_net_flow is None:
            stock_flags.add(QualityFlag.MISSING_CAPITAL_DATA)
        stock_context = replace(context, symbol_scope=symbol)
        payload = {
            "symbol": symbol,
            "turnover": row.turnover,
            "main": row.vendor_main_net_flow,
            "sector": sector_id,
        }
        stock_meta = _meta(
            context=stock_context,
            snapshot_type=SnapshotType.STOCK_CAPITAL,
            algorithm_version=STOCK_CAPITAL_ALGORITHM_VERSION,
            normalized_payload=payload,
            coverage_ratio=_coverage(observed, expected),
            quality_flags=stock_flags,
            missing_fields={"vendor_main_net_flow"} if row.vendor_main_net_flow is None else set(),
        )
        stocks.append(
            StockCapitalSnapshot(
                meta=stock_meta,
                symbol=symbol,
                universe_id=universe.universe_id,
                universe_version=universe.universe_version,
                industry_sector_id=sector_id,
                turnover=row.turnover,
                previous_turnover=row.previous_turnover,
                turnover_change=_turnover_change(row.turnover, row.previous_turnover),
                turnover_rate=row.turnover_rate,
                vendor_main_net_flow=row.vendor_main_net_flow,
                vendor_super_large_net_flow=row.vendor_super_large_net_flow,
                vendor_large_net_flow=row.vendor_large_net_flow,
                vendor_medium_net_flow=row.vendor_medium_net_flow,
                vendor_small_net_flow=row.vendor_small_net_flow,
                vendor_main_flow_ratio=ratio,
                market_vendor_main_flow_ratio=market_main_ratio,
                sector_vendor_main_flow_ratio=sector_ratio,
                relative_market_flow=relative_market,
                relative_sector_flow=relative_sector,
                market_capital_snapshot_id=market.meta.snapshot_id,
                sector_capital_snapshot_id=sector_capital_ids.get(sector_id),
                stock_snapshot_id=stock_ids.get(symbol),
                field_provenance=dict(raw.field_provenance),
                field_methodology={
                    "turnover": methods["fact"],
                    "vendor_main_net_flow": methods["vendor"],
                    "relative_market_flow": methods["vendor"].model_copy(
                        update={"unit": DECIMAL, "formula_version": "CAPITAL_RELATIVE_RATIO_V1"}
                    ),
                    "relative_sector_flow": methods["vendor"].model_copy(
                        update={"unit": DECIMAL, "formula_version": "CAPITAL_RELATIVE_RATIO_V1"}
                    ),
                },
            )
        )
    return market, sectors, stocks


def calculate_margin_snapshots(
    *, context: SnapshotBuildContext, raw: MarginRawData, universe: MarketUniverse
) -> list[MarginFinancingSnapshot]:
    flags = (
        set(context.quality_flags)
        | set(raw.quality_flags)
        | {
            QualityFlag.EOD_ONLY,
            QualityFlag.NEXT_SESSION_AVAILABLE,
        }
    )
    provider_succeeded = raw.rows is not None
    rows = {row.symbol: row for row in (raw.rows or [])}
    previous = {row.symbol: row for row in (raw.previous_rows or [])}
    universe_symbols = set(universe.symbols)
    observed = len(universe_symbols & set(rows))
    coverage = _coverage(observed, universe.eligible_symbol_count)
    if not provider_succeeded:
        flags.update({QualityFlag.SOURCE_UNAVAILABLE, QualityFlag.MISSING_MARGIN_DATA})
    elif observed < universe.eligible_symbol_count:
        flags.add(QualityFlag.PARTIAL_DATA)
    fact = FieldMethodology(
        methodology_class=CapitalMethodologyClass.OBSERVABLE_FACT,
        transparency=MethodologyTransparency.FULL,
        unit=CNY,
    )
    derived = FieldMethodology(
        methodology_class=CapitalMethodologyClass.DERIVED_TRANSPARENT,
        transparency=MethodologyTransparency.FULL,
        unit=CNY,
        formula_version="MARGIN_NET_BUY_V1",
    )
    result = []
    for symbol in universe.symbols:
        row = rows.get(symbol)
        has_data = row is not None if provider_succeeded else None
        if row is None:
            row = MarginRow(
                symbol=symbol,
                margin_balance=None,
                margin_buy=None,
                margin_repay=None,
                short_balance=None,
                short_sell=None,
                short_repay=None,
                total_margin_balance=None,
            )
        prior = previous.get(symbol)
        change = (
            row.margin_balance - prior.margin_balance
            if row.margin_balance is not None and prior and prior.margin_balance is not None
            else None
        )
        change_pct = (
            change / prior.margin_balance
            if change is not None and prior and prior.margin_balance
            else None
        )
        net_buy = (
            row.margin_buy - row.margin_repay
            if row.margin_buy is not None and row.margin_repay is not None
            else None
        )
        item_flags = set(flags)
        missing = set()
        if not has_data:
            item_flags.add(QualityFlag.MISSING_MARGIN_DATA)
            missing.add("margin_balance")
        item_context = replace(context, symbol_scope=symbol)
        payload = {
            "symbol": symbol,
            "has_margin_data": has_data,
            "balance": row.margin_balance,
            "buy": row.margin_buy,
            "repay": row.margin_repay,
            "market": raw.market_margin_total,
        }
        meta = _meta(
            context=item_context,
            snapshot_type=SnapshotType.MARGIN_FINANCING,
            algorithm_version=MARGIN_ALGORITHM_VERSION,
            normalized_payload=payload,
            coverage_ratio=coverage,
            quality_flags=item_flags,
            missing_fields=missing,
        )
        result.append(
            MarginFinancingSnapshot(
                meta=meta,
                symbol=symbol,
                universe_id=universe.universe_id,
                universe_version=universe.universe_version,
                has_margin_data=has_data,
                margin_balance=row.margin_balance,
                margin_buy=row.margin_buy,
                margin_repay=row.margin_repay,
                margin_net_buy=net_buy,
                short_balance=row.short_balance,
                short_sell=row.short_sell,
                short_repay=row.short_repay,
                total_margin_balance=row.total_margin_balance,
                margin_balance_change=change,
                margin_balance_change_pct=change_pct,
                market_margin_total=raw.market_margin_total,
                field_provenance={
                    name: "Tushare margin_detail"
                    for name in (
                        "margin_balance",
                        "margin_buy",
                        "margin_repay",
                        "short_balance",
                        "short_sell",
                        "short_repay",
                    )
                },
                field_methodology={
                    "margin_balance": fact,
                    "margin_buy": fact,
                    "margin_repay": fact,
                    "margin_net_buy": derived,
                    "short_balance": fact,
                    "short_sell": fact.model_copy(update={"unit": SHARES}),
                    "short_repay": fact.model_copy(update={"unit": SHARES}),
                    "margin_balance_change": derived.model_copy(
                        update={"formula_version": "MARGIN_BALANCE_CHANGE_V1"}
                    ),
                    "total_margin_balance": fact,
                    "market_margin_total": fact,
                    "margin_balance_change_pct": derived.model_copy(
                        update={"unit": DECIMAL, "formula_version": "MARGIN_BALANCE_CHANGE_PCT_V1"}
                    ),
                },
            )
        )
    return result


def calculate_dragon_tiger_snapshots(
    *, context: SnapshotBuildContext, raw: DragonTigerRawData, universe: MarketUniverse
) -> list[DragonTigerSnapshot]:
    flags = set(context.quality_flags) | set(raw.quality_flags) | {QualityFlag.EOD_ONLY}
    event_map = {event.symbol: event for event in raw.events}
    coverage = 1.0 if raw.provider_succeeded else 0.0
    if not raw.provider_succeeded:
        flags.update({QualityFlag.SOURCE_UNAVAILABLE, QualityFlag.MISSING_DRAGON_TIGER_DATA})
    event_method = FieldMethodology(
        methodology_class=CapitalMethodologyClass.EVENT_CAPITAL,
        transparency=MethodologyTransparency.FULL,
        unit=CNY,
        source_field="Tushare top_list/top_inst",
    )
    result = []
    for symbol in universe.symbols:
        event = event_map.get(symbol)
        has_event = event is not None if raw.provider_succeeded else None
        missing = {"has_dragon_tiger"} if has_event is None else set()
        payload = {
            "symbol": symbol,
            "has_dragon_tiger": has_event,
            "event": event.__dict__ if event else None,
        }
        item_context = replace(context, symbol_scope=symbol)
        meta = _meta(
            context=item_context,
            snapshot_type=SnapshotType.DRAGON_TIGER,
            algorithm_version=DRAGON_TIGER_ALGORITHM_VERSION,
            normalized_payload=payload,
            coverage_ratio=coverage,
            quality_flags=set(flags),
            missing_fields=missing,
        )
        result.append(
            DragonTigerSnapshot(
                meta=meta,
                symbol=symbol,
                universe_id=universe.universe_id,
                universe_version=universe.universe_version,
                has_dragon_tiger=has_event,
                reasons=list(event.reasons) if event else [],
                buy_amount=event.buy_amount if event else None,
                sell_amount=event.sell_amount if event else None,
                net_amount=event.net_amount if event else None,
                institution_buy_amount=event.institution_buy_amount if event else None,
                institution_sell_amount=event.institution_sell_amount if event else None,
                institution_net_amount=event.institution_net_amount if event else None,
                seat_count=event.seat_count if event else None,
                institution_seat_count=event.institution_seat_count if event else None,
                top_buyers=list(event.top_buyers) if event else [],
                top_sellers=list(event.top_sellers) if event else [],
                field_provenance={
                    name: "Tushare top_list/top_inst"
                    for name in (
                        "buy_amount",
                        "sell_amount",
                        "net_amount",
                        "institution_buy_amount",
                        "institution_sell_amount",
                    )
                },
                field_methodology={
                    name: event_method
                    for name in (
                        "buy_amount",
                        "sell_amount",
                        "net_amount",
                        "institution_buy_amount",
                        "institution_sell_amount",
                        "institution_net_amount",
                    )
                },
            )
        )
    return result


@dataclass
class CollectionMetrics:
    external_seconds: dict[str, float] = field(default_factory=dict)
    calculate_ms: float = 0.0
    db_save_ms: float = 0.0
    db_read_ms: float = 0.0
    snapshot_count: int = 0


class CapitalCollector:
    def __init__(self, source: CapitalDataSource, repository: SentimentV3Repository | None = None):
        self.source = source
        self.repository = repository

    def collect(
        self,
        *,
        trade_date: date,
        universe: MarketUniverse,
        memberships: Iterable[SectorMembership] = (),
        market_snapshot_id: str | None = None,
        record_source: RecordSource = RecordSource.REAL,
    ):
        try:
            raw = self.source.fetch(trade_date)
        except Exception as exc:
            tz = ZoneInfo("Asia/Shanghai")
            raw = CapitalRawData(
                source=getattr(self.source, "name", type(self.source).__name__),
                source_timestamp=datetime.combine(trade_date, time(15, 0), tz),
                available_time=datetime.combine(trade_date, time(18, 0), tz),
                rows=None,
                quality_flags=(QualityFlag.SOURCE_UNAVAILABLE, QualityFlag.MISSING_CAPITAL_DATA),
                field_provenance={"provider_error": type(exc).__name__},
            )
        now = max(datetime.now(tz=raw.available_time.tzinfo), raw.available_time)
        context = SnapshotBuildContext(
            trade_date=trade_date,
            source=raw.source,
            source_timestamp=raw.source_timestamp,
            available_time=raw.available_time,
            data_cutoff=now,
            snapshot_time=now,
            finality=Finality.FINAL,
            record_source=record_source,
            data_version=trade_date.isoformat(),
            created_at=now,
            quality_flags=raw.quality_flags,
        )
        started = perf_counter()
        snapshots = calculate_capital_snapshots(
            context=context,
            raw=raw,
            universe=universe,
            memberships=memberships,
            market_snapshot_id=market_snapshot_id,
        )
        metrics = CollectionMetrics(
            external_seconds=dict(raw.request_seconds),
            calculate_ms=(perf_counter() - started) * 1000,
        )
        flat = [snapshots[0], *snapshots[1], *snapshots[2]]
        metrics.snapshot_count = len(flat)
        if self.repository:
            started = perf_counter()
            self.repository.save_snapshots(flat)
            metrics.db_save_ms = (perf_counter() - started) * 1000
        return snapshots, metrics


class MarginCollector:
    def __init__(self, source: MarginDataSource, repository: SentimentV3Repository | None = None):
        self.source = source
        self.repository = repository

    def collect(
        self,
        *,
        trade_date: date,
        universe: MarketUniverse,
        record_source: RecordSource = RecordSource.REAL,
    ):
        try:
            raw = self.source.fetch(trade_date)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
            raw = MarginRawData(
                source=getattr(self.source, "name", type(self.source).__name__),
                source_timestamp=datetime.combine(trade_date, time(15, 0), tz),
                available_time=datetime.combine(trade_date + timedelta(days=1), time(9, 0), tz),
                rows=None,
                previous_rows=None,
                market_margin_total=None,
                quality_flags=(
                    QualityFlag.SOURCE_UNAVAILABLE,
                    QualityFlag.MISSING_MARGIN_DATA,
                    QualityFlag.NEXT_SESSION_AVAILABLE,
                ),
            )
        now = max(datetime.now(tz=raw.available_time.tzinfo), raw.available_time)
        context = SnapshotBuildContext(
            trade_date=trade_date,
            source=raw.source,
            source_timestamp=raw.source_timestamp,
            available_time=raw.available_time,
            data_cutoff=now,
            snapshot_time=now,
            finality=Finality.FINAL,
            record_source=record_source,
            data_version=trade_date.isoformat(),
            created_at=now,
            quality_flags=raw.quality_flags,
        )
        started = perf_counter()
        items = calculate_margin_snapshots(context=context, raw=raw, universe=universe)
        metrics = CollectionMetrics(
            external_seconds=dict(raw.request_seconds),
            calculate_ms=(perf_counter() - started) * 1000,
            snapshot_count=len(items),
        )
        if self.repository:
            started = perf_counter()
            self.repository.save_snapshots(items)
            metrics.db_save_ms = (perf_counter() - started) * 1000
        return items, metrics


class DragonTigerCollector:
    def __init__(
        self, source: DragonTigerDataSource, repository: SentimentV3Repository | None = None
    ):
        self.source = source
        self.repository = repository

    def collect(
        self,
        *,
        trade_date: date,
        universe: MarketUniverse,
        record_source: RecordSource = RecordSource.REAL,
    ):
        try:
            raw = self.source.fetch(trade_date)
        except Exception:
            tz = ZoneInfo("Asia/Shanghai")
            raw = DragonTigerRawData(
                source=getattr(self.source, "name", type(self.source).__name__),
                source_timestamp=datetime.combine(trade_date, time(15, 0), tz),
                available_time=datetime.combine(trade_date, time(18, 0), tz),
                provider_succeeded=False,
                events=[],
                quality_flags=(
                    QualityFlag.SOURCE_UNAVAILABLE,
                    QualityFlag.MISSING_DRAGON_TIGER_DATA,
                ),
            )
        now = max(datetime.now(tz=raw.available_time.tzinfo), raw.available_time)
        context = SnapshotBuildContext(
            trade_date=trade_date,
            source=raw.source,
            source_timestamp=raw.source_timestamp,
            available_time=raw.available_time,
            data_cutoff=now,
            snapshot_time=now,
            finality=Finality.FINAL,
            record_source=record_source,
            data_version=trade_date.isoformat(),
            created_at=now,
            quality_flags=raw.quality_flags,
        )
        started = perf_counter()
        items = calculate_dragon_tiger_snapshots(context=context, raw=raw, universe=universe)
        metrics = CollectionMetrics(
            external_seconds=dict(raw.request_seconds),
            calculate_ms=(perf_counter() - started) * 1000,
            snapshot_count=len(items),
        )
        if self.repository:
            started = perf_counter()
            self.repository.save_snapshots(items)
            metrics.db_save_ms = (perf_counter() - started) * 1000
        return items, metrics
