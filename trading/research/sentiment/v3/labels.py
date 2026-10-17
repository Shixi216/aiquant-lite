from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from enum import StrEnum
from time import perf_counter
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator

from trading.experiments.a_share_validation import _at_limit, _one_price
from trading.research.sentiment.v3.feature_assembly import ResearchFeaturePacket
from trading.research.sentiment.v3.hashing import canonical_json
from trading.research.sentiment.v3.schemas import RecordSource, SnapshotModel


SHANGHAI = ZoneInfo("Asia/Shanghai")
LABEL_SCHEMA_VERSION = "sentiment-v3-forward-label-1.0.0"
LABEL_ALGORITHM_VERSION = "sentiment-v3-forward-label-s3a-1.0.0"
RETURN_ALGORITHM_VERSION = "RETURN_ALGO_V1"
MAE_MFE_ALGORITHM_VERSION = "MAE_MFE_ALGO_V1"
PRICE_ADJUSTMENT_VERSION = "ADJ_FACTOR_RETURN_V1"
TRADABILITY_POLICY_VERSION = "A_SHARE_DAILY_TRADABILITY_V1"
SUPPORTED_HORIZONS = (1, 3, 5, 10, 20)


class LabelKind(StrEnum):
    STATISTICAL = "STATISTICAL"
    EXECUTABLE = "EXECUTABLE"


class EntryRule(StrEnum):
    SIGNAL_CLOSE_REFERENCE = "SIGNAL_CLOSE_REFERENCE"
    NEXT_TRADING_DAY_OPEN = "NEXT_TRADING_DAY_OPEN"


class ExitRule(StrEnum):
    HORIZON_TRADING_DAY_CLOSE = "HORIZON_TRADING_DAY_CLOSE"


class LabelStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INVALID = "INVALID"
    INSUFFICIENT_FORWARD_WINDOW = "INSUFFICIENT_FORWARD_WINDOW"


class ExecutionStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    EXECUTABLE = "EXECUTABLE"
    UNEXECUTABLE = "UNEXECUTABLE"
    SUSPENDED = "SUSPENDED"
    LIMIT_UP_LOCKED = "LIMIT_UP_LOCKED"
    LIMIT_DOWN_LOCKED = "LIMIT_DOWN_LOCKED"
    NO_VALID_QUOTE = "NO_VALID_QUOTE"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class TradabilityStatus(StrEnum):
    RESEARCH_RETURN_ONLY = "RESEARCH_RETURN_ONLY"
    NORMAL = "NORMAL"
    SUSPENDED = "SUSPENDED"
    LIMIT_UP_LOCKED = "LIMIT_UP_LOCKED"
    LIMIT_DOWN_LOCKED = "LIMIT_DOWN_LOCKED"
    NO_VALID_QUOTE = "NO_VALID_QUOTE"
    DELISTED = "DELISTED"
    UNKNOWN = "UNKNOWN"


class LabelQualityFlag(StrEnum):
    RESEARCH_RETURN_ONLY = "RESEARCH_RETURN_ONLY"
    COST_MODEL_NOT_CONFIGURED = "COST_MODEL_NOT_CONFIGURED"
    TRADABILITY_INFERRED = "TRADABILITY_INFERRED"
    ADJUSTMENT_UNAVAILABLE = "ADJUSTMENT_UNAVAILABLE"
    MISSING_ENTRY_PRICE = "MISSING_ENTRY_PRICE"
    MISSING_EXIT_PRICE = "MISSING_EXIT_PRICE"
    MISSING_HOLDING_BAR = "MISSING_HOLDING_BAR"
    SUSPENDED_ENTRY = "SUSPENDED_ENTRY"
    SUSPENDED_EXIT = "SUSPENDED_EXIT"
    SUSPENDED_DURING_HOLDING = "SUSPENDED_DURING_HOLDING"
    LIMIT_UP_LOCKED_ENTRY = "LIMIT_UP_LOCKED_ENTRY"
    LIMIT_DOWN_LOCKED_EXIT = "LIMIT_DOWN_LOCKED_EXIT"
    DELISTING_RELATED = "DELISTING_RELATED"
    INSUFFICIENT_FORWARD_WINDOW = "INSUFFICIENT_FORWARD_WINDOW"
    POINT_IN_TIME_INVALID = "POINT_IN_TIME_INVALID"
    PRICE_DATA_AFTER_BUILD_AS_OF = "PRICE_DATA_AFTER_BUILD_AS_OF"
    NEW_LISTING_INSUFFICIENT_WINDOW = "NEW_LISTING_INSUFFICIENT_WINDOW"


class MissingReason(StrEnum):
    MISSING_PRICE = "MISSING_PRICE"
    SUSPENDED_ENTRY = "SUSPENDED_ENTRY"
    SUSPENDED_EXIT = "SUSPENDED_EXIT"
    LIMIT_UP_LOCKED_ENTRY = "LIMIT_UP_LOCKED_ENTRY"
    LIMIT_DOWN_LOCKED_EXIT = "LIMIT_DOWN_LOCKED_EXIT"
    DELISTED = "DELISTED"
    INSUFFICIENT_FORWARD_WINDOW = "INSUFFICIENT_FORWARD_WINDOW"
    ADJUSTMENT_UNAVAILABLE = "ADJUSTMENT_UNAVAILABLE"
    INVALID_FEATURE_PACKET = "INVALID_FEATURE_PACKET"
    OTHER = "OTHER"


class LabelSpec(SnapshotModel):
    label_name: str
    label_version: str = "LABEL_SPEC_V1"
    label_kind: LabelKind
    entry_rule: EntryRule
    exit_rule: ExitRule = ExitRule.HORIZON_TRADING_DAY_CLOSE
    horizon: int
    horizon_unit: Literal["TRADING_DAYS"] = "TRADING_DAYS"
    execution_realistic: bool
    price_adjustment_method: Literal["ADJ_FACTOR_RETURN_V1"] = PRICE_ADJUSTMENT_VERSION
    tradability_policy: str = TRADABILITY_POLICY_VERSION
    cost_model_version: None = None
    slippage_model_version: None = None

    @field_validator("horizon")
    @classmethod
    def validate_horizon(cls, value: int) -> int:
        if value not in SUPPORTED_HORIZONS:
            raise ValueError(f"horizon must be one of {SUPPORTED_HORIZONS}")
        return value

    @model_validator(mode="after")
    def validate_kind(self) -> "LabelSpec":
        expected = self.label_kind == LabelKind.EXECUTABLE
        if self.execution_realistic != expected:
            raise ValueError("execution_realistic must match label_kind")
        if expected and self.entry_rule != EntryRule.NEXT_TRADING_DAY_OPEN:
            raise ValueError("executable labels must enter at next trading day open")
        if not expected and self.entry_rule != EntryRule.SIGNAL_CLOSE_REFERENCE:
            raise ValueError("statistical labels must use signal close reference")
        return self

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.model_dump(mode="json")).encode("utf-8")
        ).hexdigest()


def label_specs_v1() -> tuple[LabelSpec, ...]:
    specs: list[LabelSpec] = []
    for horizon in SUPPORTED_HORIZONS:
        specs.append(
            LabelSpec(
                label_name=f"CLOSE_TO_CLOSE_{horizon}D",
                label_kind=LabelKind.STATISTICAL,
                entry_rule=EntryRule.SIGNAL_CLOSE_REFERENCE,
                horizon=horizon,
                execution_realistic=False,
            )
        )
        specs.append(
            LabelSpec(
                label_name=f"NEXT_OPEN_TO_CLOSE_{horizon}D",
                label_kind=LabelKind.EXECUTABLE,
                entry_rule=EntryRule.NEXT_TRADING_DAY_OPEN,
                horizon=horizon,
                execution_realistic=True,
            )
        )
    return tuple(specs)


class PriceBar(SnapshotModel):
    symbol: str
    trade_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float | None = None
    amount: float | None = None
    previous_close: float | None = None
    adjustment_factor: float | None = None
    suspended: bool = False
    delisted: bool = False
    is_st: bool = False
    source: str = "LOCAL_CANONICAL_HISTORICAL_BARS"
    source_version: str = "UNKNOWN"
    data_available_time: datetime | None = None


class PriceBatch(SnapshotModel):
    calendar: list[date]
    bars: dict[str, dict[date, PriceBar]]
    price_source: str
    price_data_version: str
    loaded_at: datetime
    calendar_load_ms: float = 0.0
    price_load_ms: float = 0.0


class LocalPriceSource(Protocol):
    def load_batch(
        self,
        *,
        symbols: Sequence[str],
        start_date: date,
        end_date: date,
        build_as_of: datetime,
    ) -> PriceBatch: ...


class DuckDBLocalPriceSource:
    """Batch-only local loader. It never invokes a financial provider."""

    def __init__(self, *, connection_factory: Any | None = None) -> None:
        if connection_factory is None:
            from database.db import get_connection

            connection_factory = get_connection
        self._connection_factory = connection_factory

    def load_batch(
        self,
        *,
        symbols: Sequence[str],
        start_date: date,
        end_date: date,
        build_as_of: datetime,
    ) -> PriceBatch:
        if build_as_of.tzinfo is None or build_as_of.utcoffset() is None:
            raise ValueError("build_as_of must include timezone")
        calendar_started = perf_counter()
        with self._connection_factory() as connection:
            calendar = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT calendar_date FROM trading_calendar_days
                    WHERE is_trading_day AND calendar_date BETWEEN ? AND ?
                    ORDER BY calendar_date
                    """,
                    [start_date, end_date],
                ).fetchall()
            ]
        calendar_ms = (perf_counter() - calendar_started) * 1000
        if not symbols:
            return PriceBatch(
                calendar=calendar,
                bars={},
                price_source="LOCAL_CANONICAL_HISTORICAL_BARS",
                price_data_version="EMPTY",
                loaded_at=datetime.now(tz=SHANGHAI),
                calendar_load_ms=calendar_ms,
            )
        price_started = perf_counter()
        with self._connection_factory() as connection:
            rows = connection.execute(
                """
                WITH bars AS (
                    SELECT symbol, trade_date, open, high, low, close, volume, amount,
                           primary_source, content_hash, data_available_time
                    FROM canonical_historical_bars
                    WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                      AND trade_date BETWEEN ? AND ?
                      AND adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                      AND data_available_time <= ? AND data_cutoff <= ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY generated_at DESC, bar_id
                    ) = 1
                ), factors AS (
                    SELECT symbol, trade_date, adj_factor, content_hash
                    FROM historical_adjustment_factors
                    WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                      AND trade_date BETWEEN ? AND ?
                      AND data_available_time <= ? AND data_cutoff <= ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY fetched_at DESC, factor_id
                    ) = 1
                )
                SELECT b.*, f.adj_factor, f.content_hash AS factor_hash
                FROM bars b LEFT JOIN factors f USING (symbol, trade_date)
                ORDER BY b.symbol, b.trade_date
                """,
                [
                    list(symbols), start_date, end_date, build_as_of, build_as_of,
                    list(symbols), start_date, end_date, build_as_of, build_as_of,
                ],
            ).fetchall()
            statuses = connection.execute(
                """
                SELECT symbol, status_type, status_value, effective_start, effective_end
                FROM historical_security_statuses
                WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                  AND effective_start <= ?
                  AND (effective_end IS NULL OR effective_end >= ?)
                  AND data_available_time <= ? AND data_cutoff <= ?
                """,
                [list(symbols), end_date, start_date, build_as_of, build_as_of],
            ).fetchall()
        active_statuses: dict[tuple[str, date], set[str]] = defaultdict(set)
        for symbol, status_type, value, effective_start, effective_end in statuses:
            if not value:
                continue
            for day in calendar:
                if day >= effective_start and (effective_end is None or day <= effective_end):
                    active_statuses[(symbol, day)].add(status_type)
        bars: dict[str, dict[date, PriceBar]] = defaultdict(dict)
        previous: dict[str, float] = {}
        hashes: list[str] = []
        for row in rows:
            (
                symbol, day, open_, high, low, close, volume, amount, source,
                bar_hash, available_time, factor, factor_hash,
            ) = row
            status = active_statuses[(symbol, day)]
            bars[symbol][day] = PriceBar(
                symbol=symbol,
                trade_date=day,
                open=None if open_ is None else float(open_),
                high=None if high is None else float(high),
                low=None if low is None else float(low),
                close=None if close is None else float(close),
                volume=None if volume is None else float(volume),
                amount=None if amount is None else float(amount),
                previous_close=previous.get(symbol),
                adjustment_factor=None if factor is None else float(factor),
                suspended="SUSPENDED" in status,
                delisted="DELISTED" in status,
                is_st="ST" in status,
                source=source,
                source_version=bar_hash,
                data_available_time=available_time,
            )
            if close is not None and float(close) > 0:
                previous[symbol] = float(close)
            hashes.extend([bar_hash, factor_hash or "NO_FACTOR"])
        version = hashlib.sha256(canonical_json(hashes).encode("utf-8")).hexdigest()
        return PriceBatch(
            calendar=calendar,
            bars=dict(bars),
            price_source="LOCAL_CANONICAL_HISTORICAL_BARS+ADJ_FACTOR",
            price_data_version=version,
            loaded_at=datetime.now(tz=SHANGHAI),
            calendar_load_ms=calendar_ms,
            price_load_ms=(perf_counter() - price_started) * 1000,
        )


class ForwardReturnLabel(SnapshotModel):
    label_id: str
    symbol: str
    feature_packet_id: str
    feature_cutoff: datetime
    signal_trade_date: date
    label_name: str
    label_kind: LabelKind
    label_horizon: int
    entry_trade_date: date | None = None
    entry_time: datetime | None = None
    entry_price: float | None = None
    entry_price_type: EntryRule
    exit_trade_date: date | None = None
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_price_type: ExitRule
    raw_return: float | None = None
    adjusted_return: float | None = None
    net_return: float | None = None
    max_adverse_excursion: float | None = None
    max_favorable_excursion: float | None = None
    holding_trading_days: int
    entry_status: ExecutionStatus
    exit_status: ExecutionStatus
    label_status: LabelStatus
    tradability_status: TradabilityStatus
    execution_realistic: bool
    overlapping_label: bool
    price_adjustment_method: str
    price_source: str
    price_data_version: str
    cost_model_version: str | None = None
    universe_id: str | None = None
    universe_version: str | None = None
    record_source: RecordSource
    quality_flags: list[LabelQualityFlag] = Field(default_factory=list)
    missing_reason: MissingReason | None = None
    label_schema_version: str = LABEL_SCHEMA_VERSION
    label_algorithm_version: str = LABEL_ALGORITHM_VERSION
    return_algorithm_version: str = RETURN_ALGORITHM_VERSION
    mae_mfe_algorithm_version: str = MAE_MFE_ALGORITHM_VERSION
    label_spec_hash: str
    created_at: datetime
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    formal_weight: float = 0.0
    research_only: bool = True

    @field_validator("feature_cutoff", "entry_time", "exit_time", "created_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("label timestamps must include timezone")
        return value

    @model_validator(mode="after")
    def validate_time_separation(self) -> "ForwardReturnLabel":
        if self.execution_realistic and self.entry_time is not None:
            if self.entry_time <= self.feature_cutoff:
                raise ValueError("executable entry must be strictly after feature cutoff")
        if self.exit_time is not None and self.entry_time is not None:
            if self.exit_time < self.entry_time:
                raise ValueError("exit cannot precede entry")
        return self


class LabelBuildMetrics(SnapshotModel):
    packet_count: int
    label_count: int
    calendar_load_ms: float
    price_batch_load_ms: float
    adjustment_ms: float
    calculation_ms: float
    total_ms: float
    database_query_count: int = 3


class LabelBuildResult(SnapshotModel):
    labels: list[ForwardReturnLabel]
    metrics: LabelBuildMetrics


def _limit_rate(symbol: str, day: date, *, is_st: bool) -> float:
    if is_st:
        return 0.05
    code = symbol.split(".", 1)[0]
    if symbol.endswith(".BJ"):
        return 0.30
    if code.startswith(("688", "689")):
        return 0.20
    if code.startswith(("300", "301")):
        return 0.20 if day >= date(2020, 8, 24) else 0.10
    return 0.10


def _adjusted(price: float | None, factor: float | None) -> float | None:
    if price is None or price <= 0 or factor is None or factor <= 0:
        return None
    return float(price) * float(factor)


class ForwardLabelBuilder:
    """PIT-separated, batch forward-label builder over local data only."""

    def __init__(self, *, price_source: LocalPriceSource) -> None:
        self.price_source = price_source

    def build_for_packet(
        self,
        packet: ResearchFeaturePacket,
        specs: Sequence[LabelSpec],
        *,
        build_as_of: datetime,
    ) -> list[ForwardReturnLabel]:
        return self.build_batch([packet], specs, build_as_of=build_as_of).labels

    def build_batch(
        self,
        packets: Sequence[ResearchFeaturePacket],
        specs: Sequence[LabelSpec],
        *,
        build_as_of: datetime,
    ) -> LabelBuildResult:
        started = perf_counter()
        if build_as_of.tzinfo is None or build_as_of.utcoffset() is None:
            raise ValueError("build_as_of must include timezone")
        packet_values = list({item.packet_id: item for item in packets}.values())
        if not packet_values or not specs:
            return LabelBuildResult(
                labels=[],
                metrics=LabelBuildMetrics(
                    packet_count=len(packet_values), label_count=0,
                    calendar_load_ms=0, price_batch_load_ms=0,
                    adjustment_ms=0, calculation_ms=0,
                    total_ms=(perf_counter() - started) * 1000,
                    database_query_count=0,
                ),
            )
        start_date = min(item.trade_date for item in packet_values)
        end_date = build_as_of.astimezone(SHANGHAI).date()
        batch = self.price_source.load_batch(
            symbols=sorted({item.symbol for item in packet_values}),
            start_date=start_date,
            end_date=end_date,
            build_as_of=build_as_of,
        )
        calculation_started = perf_counter()
        calendar_index = {day: index for index, day in enumerate(batch.calendar)}
        labels = [
            self._calculate_one(
                packet=packet,
                spec=spec,
                calendar=batch.calendar,
                calendar_index=calendar_index,
                bars=batch.bars.get(packet.symbol, {}),
                price_source=batch.price_source,
                price_data_version=batch.price_data_version,
                created_at=build_as_of,
            )
            for packet in packet_values
            for spec in specs
        ]
        total_ms = (perf_counter() - started) * 1000
        return LabelBuildResult(
            labels=labels,
            metrics=LabelBuildMetrics(
                packet_count=len(packet_values),
                label_count=len(labels),
                calendar_load_ms=batch.calendar_load_ms,
                price_batch_load_ms=batch.price_load_ms,
                adjustment_ms=0.0,
                calculation_ms=(perf_counter() - calculation_started) * 1000,
                total_ms=total_ms,
                database_query_count=3,
            ),
        )

    @staticmethod
    def _calculate_one(
        *,
        packet: ResearchFeaturePacket,
        spec: LabelSpec,
        calendar: list[date],
        calendar_index: dict[date, int],
        bars: dict[date, PriceBar],
        price_source: str,
        price_data_version: str,
        created_at: datetime,
    ) -> ForwardReturnLabel:
        common = {
            "symbol": packet.symbol,
            "feature_packet_id": packet.packet_id,
            "feature_cutoff": packet.cutoff,
            "signal_trade_date": packet.trade_date,
            "label_name": spec.label_name,
            "label_kind": spec.label_kind,
            "label_horizon": spec.horizon,
            "entry_price_type": spec.entry_rule,
            "exit_price_type": spec.exit_rule,
            "holding_trading_days": spec.horizon,
            "execution_realistic": spec.execution_realistic,
            "overlapping_label": spec.horizon > 1,
            "price_adjustment_method": spec.price_adjustment_method,
            "price_source": price_source,
            "price_data_version": price_data_version,
            "universe_id": packet.universe_id,
            "universe_version": packet.universe_version,
            "record_source": packet.record_source,
            "label_spec_hash": spec.spec_hash,
            "created_at": created_at,
        }
        signal_index = calendar_index.get(packet.trade_date)
        if signal_index is None or signal_index + spec.horizon >= len(calendar):
            return ForwardLabelBuilder._invalid(
                common,
                status=LabelStatus.INSUFFICIENT_FORWARD_WINDOW,
                missing=MissingReason.INSUFFICIENT_FORWARD_WINDOW,
                flags=[LabelQualityFlag.INSUFFICIENT_FORWARD_WINDOW],
                entry_status=ExecutionStatus.UNKNOWN,
                exit_status=ExecutionStatus.UNKNOWN,
                tradability=TradabilityStatus.UNKNOWN,
            )
        entry_date = (
            packet.trade_date
            if spec.entry_rule == EntryRule.SIGNAL_CLOSE_REFERENCE
            else calendar[signal_index + 1]
        )
        # A-share T+1: a position bought at next open cannot be sold that close.
        # Statistical labels keep the conventional T-close -> T+N-close window.
        exit_offset = (
            spec.horizon
            if spec.label_kind == LabelKind.STATISTICAL
            else max(spec.horizon, 2)
        )
        if signal_index + exit_offset >= len(calendar):
            return ForwardLabelBuilder._invalid(
                common,
                status=LabelStatus.INSUFFICIENT_FORWARD_WINDOW,
                missing=MissingReason.INSUFFICIENT_FORWARD_WINDOW,
                flags=[LabelQualityFlag.INSUFFICIENT_FORWARD_WINDOW],
                entry_status=ExecutionStatus.UNKNOWN,
                exit_status=ExecutionStatus.UNKNOWN,
                tradability=TradabilityStatus.UNKNOWN,
            )
        exit_date = calendar[signal_index + exit_offset]
        entry_bar = bars.get(entry_date)
        exit_bar = bars.get(exit_date)
        entry_time = datetime.combine(
            entry_date,
            time(15, 0) if spec.label_kind == LabelKind.STATISTICAL else time(9, 30),
            tzinfo=SHANGHAI,
        )
        exit_time = datetime.combine(exit_date, time(15, 0), tzinfo=SHANGHAI)
        details = {
            **common,
            "entry_trade_date": entry_date,
            "entry_time": entry_time,
            "exit_trade_date": exit_date,
            "exit_time": exit_time,
        }
        if entry_bar is None:
            return ForwardLabelBuilder._invalid(
                details,
                status=LabelStatus.INVALID,
                missing=MissingReason.MISSING_PRICE,
                flags=[LabelQualityFlag.MISSING_ENTRY_PRICE],
                entry_status=ExecutionStatus.NO_VALID_QUOTE,
                exit_status=ExecutionStatus.UNKNOWN,
                tradability=TradabilityStatus.NO_VALID_QUOTE,
            )
        entry_price = (
            entry_bar.close
            if spec.entry_rule == EntryRule.SIGNAL_CLOSE_REFERENCE
            else entry_bar.open
        )
        if entry_price is None or entry_price <= 0:
            status = (
                ExecutionStatus.DELISTED
                if entry_bar.delisted else ExecutionStatus.SUSPENDED
                if entry_bar.suspended else ExecutionStatus.NO_VALID_QUOTE
            )
            flag = (
                LabelQualityFlag.DELISTING_RELATED
                if entry_bar.delisted else LabelQualityFlag.SUSPENDED_ENTRY
                if entry_bar.suspended else LabelQualityFlag.MISSING_ENTRY_PRICE
            )
            reason = (
                MissingReason.DELISTED if entry_bar.delisted else
                MissingReason.SUSPENDED_ENTRY if entry_bar.suspended else
                MissingReason.MISSING_PRICE
            )
            return ForwardLabelBuilder._invalid(
                details,
                status=LabelStatus.INVALID,
                missing=reason,
                flags=[flag],
                entry_status=status,
                exit_status=ExecutionStatus.UNKNOWN,
                tradability=TradabilityStatus(status.value),
            )
        quality: list[LabelQualityFlag] = []
        tradability = TradabilityStatus.RESEARCH_RETURN_ONLY
        entry_status = ExecutionStatus.NOT_APPLICABLE
        if spec.execution_realistic:
            entry_status = ExecutionStatus.EXECUTABLE
            tradability = TradabilityStatus.NORMAL
            rate = _limit_rate(packet.symbol, entry_date, is_st=entry_bar.is_st)
            if entry_bar.suspended or entry_bar.volume == 0:
                return ForwardLabelBuilder._invalid(
                    details,
                    status=LabelStatus.INVALID,
                    missing=MissingReason.SUSPENDED_ENTRY,
                    flags=[LabelQualityFlag.SUSPENDED_ENTRY],
                    entry_status=ExecutionStatus.SUSPENDED,
                    exit_status=ExecutionStatus.UNKNOWN,
                    tradability=TradabilityStatus.SUSPENDED,
                )
            if _one_price(entry_bar) and _at_limit(
                float(entry_price), entry_bar.previous_close, rate=rate,
                direction=1, tolerance=0.001,
            ):
                return ForwardLabelBuilder._invalid(
                    details,
                    status=LabelStatus.INVALID,
                    missing=MissingReason.LIMIT_UP_LOCKED_ENTRY,
                    flags=[LabelQualityFlag.LIMIT_UP_LOCKED_ENTRY],
                    entry_status=ExecutionStatus.LIMIT_UP_LOCKED,
                    exit_status=ExecutionStatus.UNKNOWN,
                    tradability=TradabilityStatus.LIMIT_UP_LOCKED,
                )
            if _at_limit(
                float(entry_price), entry_bar.previous_close, rate=rate,
                direction=1, tolerance=0.001,
            ):
                quality.append(LabelQualityFlag.TRADABILITY_INFERRED)
        else:
            quality.extend(
                [
                    LabelQualityFlag.RESEARCH_RETURN_ONLY,
                    LabelQualityFlag.COST_MODEL_NOT_CONFIGURED,
                ]
            )
        if exit_bar is None or exit_bar.close is None or exit_bar.close <= 0:
            status = (
                ExecutionStatus.DELISTED
                if exit_bar is not None and exit_bar.delisted else
                ExecutionStatus.SUSPENDED
                if exit_bar is not None and exit_bar.suspended else
                ExecutionStatus.NO_VALID_QUOTE
            )
            flag = (
                LabelQualityFlag.DELISTING_RELATED
                if status == ExecutionStatus.DELISTED else
                LabelQualityFlag.SUSPENDED_EXIT
                if status == ExecutionStatus.SUSPENDED else
                LabelQualityFlag.MISSING_EXIT_PRICE
            )
            reason = (
                MissingReason.DELISTED if status == ExecutionStatus.DELISTED else
                MissingReason.SUSPENDED_EXIT if status == ExecutionStatus.SUSPENDED else
                MissingReason.MISSING_PRICE
            )
            return ForwardLabelBuilder._invalid(
                details,
                status=LabelStatus.INVALID,
                missing=reason,
                flags=[*quality, flag],
                entry_status=entry_status,
                exit_status=status,
                tradability=TradabilityStatus(status.value),
            )
        exit_status = (
            ExecutionStatus.EXECUTABLE
            if spec.execution_realistic else ExecutionStatus.NOT_APPLICABLE
        )
        if spec.execution_realistic:
            rate = _limit_rate(packet.symbol, exit_date, is_st=exit_bar.is_st)
            if exit_bar.suspended or exit_bar.volume == 0:
                return ForwardLabelBuilder._invalid(
                    details,
                    status=LabelStatus.INVALID,
                    missing=MissingReason.SUSPENDED_EXIT,
                    flags=[*quality, LabelQualityFlag.SUSPENDED_EXIT],
                    entry_status=entry_status,
                    exit_status=ExecutionStatus.SUSPENDED,
                    tradability=TradabilityStatus.SUSPENDED,
                )
            if _one_price(exit_bar) and _at_limit(
                float(exit_bar.close), exit_bar.previous_close, rate=rate,
                direction=-1, tolerance=0.001,
            ):
                return ForwardLabelBuilder._invalid(
                    details,
                    status=LabelStatus.INVALID,
                    missing=MissingReason.LIMIT_DOWN_LOCKED_EXIT,
                    flags=[*quality, LabelQualityFlag.LIMIT_DOWN_LOCKED_EXIT],
                    entry_status=entry_status,
                    exit_status=ExecutionStatus.LIMIT_DOWN_LOCKED,
                    tradability=TradabilityStatus.LIMIT_DOWN_LOCKED,
                )
            if _at_limit(
                float(exit_bar.close), exit_bar.previous_close, rate=rate,
                direction=-1, tolerance=0.001,
            ):
                quality.append(LabelQualityFlag.TRADABILITY_INFERRED)
            quality.append(LabelQualityFlag.COST_MODEL_NOT_CONFIGURED)
        adjusted_entry = _adjusted(entry_price, entry_bar.adjustment_factor)
        adjusted_exit = _adjusted(exit_bar.close, exit_bar.adjustment_factor)
        if adjusted_entry is None or adjusted_exit is None:
            return ForwardLabelBuilder._invalid(
                details,
                status=LabelStatus.INVALID,
                missing=MissingReason.ADJUSTMENT_UNAVAILABLE,
                flags=[*quality, LabelQualityFlag.ADJUSTMENT_UNAVAILABLE],
                entry_status=entry_status,
                exit_status=exit_status,
                tradability=tradability,
            )
        holding_dates = calendar[signal_index + 1 : signal_index + exit_offset + 1]
        holding_bars = [bars.get(day) for day in holding_dates]
        missing_holding = any(item is None for item in holding_bars)
        observed = [item for item in holding_bars if item is not None]
        lows = [
            _adjusted(item.low, item.adjustment_factor)
            for item in observed if item.low is not None
        ]
        highs = [
            _adjusted(item.high, item.adjustment_factor)
            for item in observed if item.high is not None
        ]
        if missing_holding:
            quality.append(LabelQualityFlag.MISSING_HOLDING_BAR)
        if any(item.suspended for item in observed):
            quality.append(LabelQualityFlag.SUSPENDED_DURING_HOLDING)
        raw_return = float(exit_bar.close) / float(entry_price) - 1
        adjusted_return = adjusted_exit / adjusted_entry - 1
        payload = {
            **details,
            "entry_price": float(entry_price),
            "exit_price": float(exit_bar.close),
            "raw_return": raw_return,
            "adjusted_return": adjusted_return,
            "net_return": None,
            "max_adverse_excursion": (
                None if not lows else min(float(value) / adjusted_entry - 1 for value in lows)
            ),
            "max_favorable_excursion": (
                None if not highs else max(float(value) / adjusted_entry - 1 for value in highs)
            ),
            "entry_status": entry_status,
            "exit_status": exit_status,
            "label_status": LabelStatus.PARTIAL if missing_holding else LabelStatus.COMPLETE,
            "tradability_status": tradability,
            "quality_flags": sorted(set(quality), key=lambda item: item.value),
            "missing_reason": None,
        }
        return ForwardLabelBuilder._finalize(payload)

    @staticmethod
    def _invalid(
        common: dict[str, Any],
        *,
        status: LabelStatus,
        missing: MissingReason,
        flags: list[LabelQualityFlag],
        entry_status: ExecutionStatus,
        exit_status: ExecutionStatus,
        tradability: TradabilityStatus,
    ) -> ForwardReturnLabel:
        return ForwardLabelBuilder._finalize(
            {
                **common,
                "entry_status": entry_status,
                "exit_status": exit_status,
                "label_status": status,
                "tradability_status": tradability,
                "quality_flags": sorted(set(flags), key=lambda item: item.value),
                "missing_reason": missing,
            }
        )

    @staticmethod
    def _finalize(payload: dict[str, Any]) -> ForwardReturnLabel:
        identity = {
            "feature_packet_id": payload["feature_packet_id"],
            "label_spec_hash": payload["label_spec_hash"],
            "price_data_version": payload["price_data_version"],
            "algorithm_version": LABEL_ALGORITHM_VERSION,
        }
        content_payload = {
            **identity,
            "entry_trade_date": payload.get("entry_trade_date"),
            "exit_trade_date": payload.get("exit_trade_date"),
            "entry_price": payload.get("entry_price"),
            "exit_price": payload.get("exit_price"),
            "raw_return": payload.get("raw_return"),
            "adjusted_return": payload.get("adjusted_return"),
            "mae": payload.get("max_adverse_excursion"),
            "mfe": payload.get("max_favorable_excursion"),
            "status": payload["label_status"],
            "quality_flags": payload.get("quality_flags", []),
            "missing_reason": payload.get("missing_reason"),
        }
        digest = hashlib.sha256(canonical_json(content_payload).encode("utf-8")).hexdigest()
        return ForwardReturnLabel(
            label_id=f"sv3_label_{hashlib.sha256(canonical_json(identity).encode('utf-8')).hexdigest()[:32]}",
            content_hash=digest,
            **payload,
        )


class LabelCoverageReport(SnapshotModel):
    total_packets: int
    total_labels: int
    valid_labels: int
    invalid_labels: int
    valid_rate: float | None
    by_horizon: dict[int, dict[str, int | float | None]]
    exclusion_reasons: dict[str, int]
    survivorship_audit: dict[str, int]


def label_coverage_report(labels: Iterable[ForwardReturnLabel]) -> LabelCoverageReport:
    values = list(labels)
    by_horizon: dict[int, dict[str, int | float | None]] = {}
    for horizon in SUPPORTED_HORIZONS:
        rows = [item for item in values if item.label_horizon == horizon]
        valid = sum(item.adjusted_return is not None for item in rows)
        by_horizon[horizon] = {
            "total": len(rows),
            "valid": valid,
            "invalid": len(rows) - valid,
            "valid_rate": None if not rows else valid / len(rows),
        }
    reasons = Counter(
        item.missing_reason.value for item in values if item.missing_reason is not None
    )
    survivorship = Counter()
    for item in values:
        for flag in item.quality_flags:
            if flag in {
                LabelQualityFlag.DELISTING_RELATED,
                LabelQualityFlag.SUSPENDED_ENTRY,
                LabelQualityFlag.SUSPENDED_EXIT,
                LabelQualityFlag.NEW_LISTING_INSUFFICIENT_WINDOW,
            }:
                survivorship[flag.value] += 1
    valid_count = sum(item.adjusted_return is not None for item in values)
    return LabelCoverageReport(
        total_packets=len({item.feature_packet_id for item in values}),
        total_labels=len(values),
        valid_labels=valid_count,
        invalid_labels=len(values) - valid_count,
        valid_rate=None if not values else valid_count / len(values),
        by_horizon=by_horizon,
        exclusion_reasons=dict(sorted(reasons.items())),
        survivorship_audit=dict(sorted(survivorship.items())),
    )


__all__ = [
    "DuckDBLocalPriceSource",
    "EntryRule",
    "ExecutionStatus",
    "ExitRule",
    "ForwardLabelBuilder",
    "ForwardReturnLabel",
    "LabelBuildMetrics",
    "LabelBuildResult",
    "LabelCoverageReport",
    "LabelKind",
    "LabelQualityFlag",
    "LabelSpec",
    "LabelStatus",
    "LocalPriceSource",
    "MissingReason",
    "PriceBar",
    "PriceBatch",
    "SUPPORTED_HORIZONS",
    "TradabilityStatus",
    "label_coverage_report",
    "label_specs_v1",
]
