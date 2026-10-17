from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from statistics import mean, median

from trading.research.sentiment.v3.hashing import snapshot_id, stable_content_hash
from trading.research.sentiment.v3.schemas import (
    Finality,
    MARKET_ALGORITHM_VERSION,
    MarketSentimentSnapshot,
    ProviderHealth,
    QualityFlag,
    RecordSource,
    SCHEMA_VERSION,
    SPECULATION_ALGORITHM_VERSION,
    SentimentSnapshotMeta,
    SnapshotType,
    SpeculationSentimentSnapshot,
)


BROKEN_RATE_DEFINITION = "broken / (sealed_limit_up + broken)"


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    change_pct: float | None
    amount: float | None


@dataclass(frozen=True)
class LimitUpStock:
    symbol: str
    board_height: int | None


@dataclass(frozen=True)
class SnapshotBuildContext:
    trade_date: date
    source: str
    source_timestamp: datetime
    available_time: datetime
    data_cutoff: datetime
    snapshot_time: datetime
    finality: Finality
    record_source: RecordSource
    data_version: str
    symbol_scope: str = "ALL_A"
    created_at: datetime | None = None
    revision: int = 1
    corrects_snapshot_id: str | None = None
    quality_flags: tuple[QualityFlag, ...] = ()


@dataclass(frozen=True)
class SnapshotInputs:
    quotes: list[MarketQuote] | None
    limit_up: list[LimitUpStock] | None
    limit_down_symbols: list[str] | None
    broken_board_symbols: list[str] | None
    strong_stock_symbols: list[str] | None
    expected_universe_size: int | None = None
    previous_market: MarketSentimentSnapshot | None = None
    previous_speculation: SpeculationSentimentSnapshot | None = None
    missing_fields: tuple[str, ...] = ()
    quality_flags: tuple[QualityFlag, ...] = ()
    field_provenance: dict[str, str] = field(default_factory=dict)
    provider_health: tuple[ProviderHealth, ...] = ()


def _unique(values: list[str] | None) -> tuple[list[str] | None, bool]:
    if values is None:
        return None, False
    normalized = sorted({value for value in values if value})
    return normalized, len(normalized) != len(values)


def _limit_map(
    values: list[LimitUpStock] | None,
) -> tuple[dict[str, int] | None, bool, bool]:
    if values is None:
        return None, False, False
    result: dict[str, int] = {}
    duplicated = False
    missing_height = False
    for item in values:
        if item.symbol in result:
            duplicated = True
        if item.board_height is None or item.board_height < 1:
            missing_height = True
            continue
        result[item.symbol] = item.board_height
    return dict(sorted(result.items())), duplicated, missing_height


def _meta(
    *,
    context: SnapshotBuildContext,
    snapshot_type: SnapshotType,
    algorithm_version: str,
    normalized_payload: dict,
    coverage_ratio: float,
    quality_flags: set[QualityFlag],
    missing_fields: set[str],
) -> SentimentSnapshotMeta:
    if context.finality in {Finality.INTRADAY, Finality.PRELIMINARY}:
        quality_flags.add(QualityFlag.NOT_FINAL)
    if missing_fields:
        quality_flags.add(QualityFlag.PARTIAL_DATA)
    digest = stable_content_hash(
        snapshot_type=snapshot_type,
        source=context.source,
        trade_date=context.trade_date,
        symbol_scope=context.symbol_scope,
        schema_version=SCHEMA_VERSION,
        algorithm_version=algorithm_version,
        normalized_payload=normalized_payload,
    )
    return SentimentSnapshotMeta(
        snapshot_id=snapshot_id(snapshot_type, digest, context.record_source.value),
        snapshot_type=snapshot_type,
        trade_date=context.trade_date,
        symbol_scope=context.symbol_scope,
        source=context.source,
        source_timestamp=context.source_timestamp,
        available_time=context.available_time,
        data_cutoff=context.data_cutoff,
        snapshot_time=context.snapshot_time,
        finality=context.finality,
        data_version=context.data_version,
        schema_version=SCHEMA_VERSION,
        algorithm_version=algorithm_version,
        content_hash=digest,
        coverage_ratio=max(0.0, min(1.0, coverage_ratio)),
        quality_flags=sorted(quality_flags, key=str),
        missing_fields=sorted(missing_fields),
        record_source=context.record_source,
        revision=context.revision,
        corrects_snapshot_id=context.corrects_snapshot_id,
        created_at=context.created_at or context.snapshot_time,
    )


def calculate_market_snapshot(
    context: SnapshotBuildContext,
    inputs: SnapshotInputs,
) -> MarketSentimentSnapshot:
    flags = set(context.quality_flags) | set(inputs.quality_flags)
    missing = set(inputs.missing_fields)

    quotes = inputs.quotes
    duplicated = False
    quote_map: dict[str, MarketQuote] | None = None
    if quotes is None:
        missing.update(
            {
                "advancers",
                "decliners",
                "unchanged",
                "market_turnover",
                "breadth_strength",
            }
        )
        flags.add(QualityFlag.MISSING_UNIVERSE)
    else:
        quote_map = {}
        for quote in quotes:
            duplicated |= quote.symbol in quote_map
            quote_map[quote.symbol] = quote

    valid_changes = (
        [q.change_pct for q in quote_map.values() if q.change_pct is not None]
        if quote_map is not None
        else []
    )
    valid_quote_count = len(valid_changes) if quote_map is not None else None
    universe_size = (
        inputs.expected_universe_size
        if inputs.expected_universe_size is not None
        else (len(quote_map) if quote_map is not None else None)
    )
    advancers = sum(value > 0 for value in valid_changes) if quote_map is not None else None
    decliners = sum(value < 0 for value in valid_changes) if quote_map is not None else None
    unchanged = sum(value == 0 for value in valid_changes) if quote_map is not None else None
    breadth_denominator = len(valid_changes) if quote_map is not None else None
    advance_ratio = advancers / breadth_denominator if breadth_denominator else None
    decline_ratio = decliners / breadth_denominator if breadth_denominator else None
    advance_decline_ratio = (
        advancers / decliners
        if advancers is not None and decliners is not None and decliners > 0
        else None
    )
    breadth_strength = (
        (advancers - decliners) / breadth_denominator
        if advancers is not None and decliners is not None and breadth_denominator
        else None
    )
    market_turnover = (
        sum(q.amount for q in quote_map.values() if q.amount is not None)
        if quote_map is not None
        else None
    )
    previous_turnover = (
        inputs.previous_market.market_turnover if inputs.previous_market is not None else None
    )
    market_turnover_change = (
        (market_turnover - previous_turnover) / previous_turnover
        if market_turnover is not None and previous_turnover not in {None, 0}
        else None
    )
    if inputs.previous_market is None:
        missing.add("market_turnover_change")
        flags.add(QualityFlag.MISSING_PREVIOUS_MARKET_SNAPSHOT)

    limit_map, dup_limit, _ = _limit_map(inputs.limit_up)
    limit_up_symbols = None if limit_map is None else sorted(limit_map)
    limit_down_symbols, dup_down = _unique(inputs.limit_down_symbols)
    broken_symbols, dup_broken = _unique(inputs.broken_board_symbols)
    strong_symbols, dup_strong = _unique(inputs.strong_stock_symbols)
    duplicated |= dup_limit or dup_down or dup_broken or dup_strong
    if duplicated:
        flags.add(QualityFlag.DUPLICATED_SOURCE_DATA)

    limit_up_count = None if limit_up_symbols is None else len(limit_up_symbols)
    limit_down_count = None if limit_down_symbols is None else len(limit_down_symbols)
    broken_count = None if broken_symbols is None else len(broken_symbols)
    strong_count = None if strong_symbols is None else len(strong_symbols)
    if limit_up_symbols is None:
        missing.add("limit_up_count")
        flags.add(QualityFlag.MISSING_LIMIT_UP_POOL)
    if limit_down_symbols is None:
        missing.add("limit_down_count")
        flags.add(QualityFlag.MISSING_LIMIT_DOWN_POOL)
    if broken_symbols is None:
        missing.update({"broken_board_count", "broken_board_rate"})
        flags.add(QualityFlag.MISSING_BROKEN_BOARD_POOL)
    if strong_symbols is None:
        missing.add("strong_stock_count")
        flags.add(QualityFlag.MISSING_STRONG_STOCK_POOL)

    limit_up_down_ratio = (
        limit_up_count / limit_down_count
        if limit_up_count is not None and limit_down_count is not None and limit_down_count > 0
        else None
    )
    broken_denominator = (
        limit_up_count + broken_count
        if limit_up_count is not None and broken_count is not None
        else None
    )
    broken_rate = broken_count / broken_denominator if broken_denominator else None

    quote_coverage = (
        valid_quote_count / universe_size
        if valid_quote_count is not None and universe_size
        else 0.0
    )
    component_coverage = mean(
        [
            quote_coverage,
            float(limit_up_symbols is not None),
            float(limit_down_symbols is not None),
            float(broken_symbols is not None),
            float(strong_symbols is not None),
        ]
    )
    payload = {
        "universe_size": universe_size,
        "valid_quote_count": valid_quote_count,
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "advance_ratio": advance_ratio,
        "decline_ratio": decline_ratio,
        "advance_decline_ratio": advance_decline_ratio,
        "breadth_denominator": breadth_denominator,
        "breadth_strength": breadth_strength,
        "limit_up_count": limit_up_count,
        "limit_down_count": limit_down_count,
        "limit_up_down_ratio": limit_up_down_ratio,
        "broken_board_count": broken_count,
        "sealed_limit_up_count": limit_up_count,
        "broken_board_denominator": broken_denominator,
        "broken_board_denominator_definition": BROKEN_RATE_DEFINITION,
        "broken_board_rate": broken_rate,
        "strong_stock_count": strong_count,
        "market_turnover": market_turnover,
        "previous_market_turnover": previous_turnover,
        "market_turnover_change": market_turnover_change,
        "limit_up_symbols": limit_up_symbols,
        "limit_down_symbols": limit_down_symbols,
        "broken_board_symbols": broken_symbols,
        "strong_stock_symbols": strong_symbols,
        "field_provenance": dict(sorted(inputs.field_provenance.items())),
        "provider_health": list(inputs.provider_health),
    }
    hash_payload = {key: value for key, value in payload.items() if key != "provider_health"}
    meta = _meta(
        context=context,
        snapshot_type=SnapshotType.MARKET,
        algorithm_version=MARKET_ALGORITHM_VERSION,
        normalized_payload=hash_payload,
        coverage_ratio=component_coverage,
        quality_flags=flags,
        missing_fields=missing,
    )
    return MarketSentimentSnapshot(meta=meta, **payload)


def calculate_speculation_snapshot(
    context: SnapshotBuildContext,
    inputs: SnapshotInputs,
) -> SpeculationSentimentSnapshot:
    flags = set(context.quality_flags) | set(inputs.quality_flags)
    missing = set(inputs.missing_fields)
    limit_map, duplicated, missing_height = _limit_map(inputs.limit_up)
    broken_symbols, dup_broken = _unique(inputs.broken_board_symbols)
    strong_symbols, dup_strong = _unique(inputs.strong_stock_symbols)
    duplicated |= dup_broken or dup_strong
    if duplicated:
        flags.add(QualityFlag.DUPLICATED_SOURCE_DATA)
    if missing_height:
        missing.add("board_height_by_symbol")
        flags.add(QualityFlag.PARTIAL_DATA)

    if limit_map is None:
        first = second = third = high = max_height = None
        ladder = None
        limit_symbols = None
        missing.update(
            {
                "first_board_count",
                "second_board_count",
                "third_board_count",
                "high_board_count",
                "max_board_height",
                "board_ladder_distribution",
            }
        )
        flags.add(QualityFlag.MISSING_LIMIT_UP_POOL)
    else:
        heights = list(limit_map.values())
        ladder_counter = Counter(heights)
        ladder = {str(key): ladder_counter[key] for key in sorted(ladder_counter)}
        first = ladder_counter[1]
        second = ladder_counter[2]
        third = ladder_counter[3]
        high = sum(count for height, count in ladder_counter.items() if height >= 4)
        max_height = max(heights, default=0)
        limit_symbols = sorted(limit_map)

    broken_count = None if broken_symbols is None else len(broken_symbols)
    strong_count = None if strong_symbols is None else len(strong_symbols)
    sealed_count = None if limit_map is None else len(limit_map)
    broken_denominator = (
        sealed_count + broken_count
        if sealed_count is not None and broken_count is not None
        else None
    )
    broken_rate = broken_count / broken_denominator if broken_denominator else None
    if broken_symbols is None:
        missing.update({"broken_board_count", "broken_board_rate"})
        flags.add(QualityFlag.MISSING_BROKEN_BOARD_POOL)
    if strong_symbols is None:
        missing.add("strong_stock_count")
        flags.add(QualityFlag.MISSING_STRONG_STOCK_POOL)

    previous = inputs.previous_speculation
    quote_returns = (
        {q.symbol: q.change_pct for q in inputs.quotes if q.change_pct is not None}
        if inputs.quotes is not None
        else {}
    )
    promotion_count = promotion_denominator = promotion_rate = None
    return_count = avg_return = median_return = None
    success_count = success_denominator = success_rate = None
    repair_count = repair_denominator = repair_rate = None
    negative_count = negative_denominator = negative_rate = None
    previous_snapshot_id = None
    if previous is None:
        missing.update(
            {
                "promotion_rate",
                "yesterday_limit_up_avg_return",
                "yesterday_limit_up_median_return",
                "yesterday_board_success_rate",
                "broken_board_repair_rate",
                "high_position_negative_feedback",
            }
        )
        flags.add(QualityFlag.MISSING_PREVIOUS_SPECULATION_SNAPSHOT)
    else:
        previous_snapshot_id = previous.meta.snapshot_id
        previous_map = previous.board_height_by_symbol
        if previous_map is not None and limit_map is not None:
            promotion_denominator = len(previous_map)
            promotion_count = sum(
                limit_map.get(symbol, 0) >= height + 1 for symbol, height in previous_map.items()
            )
            promotion_rate = (
                promotion_count / promotion_denominator if promotion_denominator else None
            )
        else:
            missing.add("promotion_rate")

        previous_limit = previous.limit_up_symbols
        if previous_limit is not None:
            returns = [
                quote_returns[symbol] for symbol in previous_limit if symbol in quote_returns
            ]
            return_count = len(returns)
            if returns:
                avg_return = mean(returns)
                median_return = median(returns)
            else:
                missing.update(
                    {
                        "yesterday_limit_up_avg_return",
                        "yesterday_limit_up_median_return",
                    }
                )
            if limit_map is not None:
                success_denominator = len(previous_limit)
                success_count = len(set(previous_limit) & set(limit_map))
                success_rate = success_count / success_denominator if success_denominator else None
        else:
            missing.update(
                {
                    "yesterday_limit_up_avg_return",
                    "yesterday_limit_up_median_return",
                    "yesterday_board_success_rate",
                }
            )

        previous_broken = previous.broken_board_symbols
        if previous_broken is not None and limit_map is not None:
            repair_denominator = len(previous_broken)
            repair_count = len(set(previous_broken) & set(limit_map))
            repair_rate = repair_count / repair_denominator if repair_denominator else None
        else:
            missing.add("broken_board_repair_rate")

        if previous_map is not None:
            prior_high = [symbol for symbol, height in previous_map.items() if height >= 4]
            observed_high = [symbol for symbol in prior_high if symbol in quote_returns]
            negative_denominator = len(observed_high)
            negative_count = sum(quote_returns[symbol] < 0 for symbol in observed_high)
            negative_rate = negative_count / negative_denominator if negative_denominator else None
            if prior_high and not observed_high:
                missing.add("high_position_negative_feedback")
        else:
            missing.add("high_position_negative_feedback")

    component_coverage = mean(
        [
            float(limit_map is not None),
            float(broken_symbols is not None),
            float(strong_symbols is not None),
        ]
    )
    payload = {
        "first_board_count": first,
        "second_board_count": second,
        "third_board_count": third,
        "high_board_count": high,
        "max_board_height": max_height,
        "board_ladder_distribution": ladder,
        "limit_up_symbols": limit_symbols,
        "board_height_by_symbol": limit_map,
        "broken_board_count": broken_count,
        "broken_board_symbols": broken_symbols,
        "sealed_limit_up_count": sealed_count,
        "broken_board_denominator": broken_denominator,
        "broken_board_denominator_definition": BROKEN_RATE_DEFINITION,
        "broken_board_rate": broken_rate,
        "strong_stock_count": strong_count,
        "strong_stock_symbols": strong_symbols,
        "promotion_count": promotion_count,
        "promotion_denominator": promotion_denominator,
        "promotion_rate": promotion_rate,
        "yesterday_limit_up_return_count": return_count,
        "yesterday_limit_up_avg_return": avg_return,
        "yesterday_limit_up_median_return": median_return,
        "yesterday_board_success_count": success_count,
        "yesterday_board_success_denominator": success_denominator,
        "yesterday_board_success_rate": success_rate,
        "broken_board_repair_count": repair_count,
        "broken_board_repair_denominator": repair_denominator,
        "broken_board_repair_rate": repair_rate,
        "high_position_negative_feedback_count": negative_count,
        "high_position_negative_feedback_denominator": negative_denominator,
        "high_position_negative_feedback": negative_rate,
        "previous_snapshot_id": previous_snapshot_id,
    }
    meta = _meta(
        context=context,
        snapshot_type=SnapshotType.SPECULATION,
        algorithm_version=SPECULATION_ALGORITHM_VERSION,
        normalized_payload=payload,
        coverage_ratio=component_coverage,
        quality_flags=flags,
        missing_fields=missing,
    )
    return SpeculationSentimentSnapshot(meta=meta, **payload)


__all__ = [
    "BROKEN_RATE_DEFINITION",
    "LimitUpStock",
    "MarketQuote",
    "SnapshotBuildContext",
    "SnapshotInputs",
    "calculate_market_snapshot",
    "calculate_speculation_snapshot",
]
