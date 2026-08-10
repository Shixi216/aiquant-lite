from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean, median
from typing import Iterable

from trading.decision_support.execution_status import compute_execution_status
from trading.experiments.formal_strategy_validation import (
    FormalReplayDecision,
    FormalReplayInput,
)
from trading.experiments.parameter_sensitivity import REQUIRED_HORIZONS, FutureBar
from trading.research.technical.analysis import technical_signal
from trading.schemas import Bar


BUY_ACTIONS = frozenset({"STRONG_BUY", "BUY", "SMALL_BUY"})
ZONE_VALID_CALENDAR_DAYS = 5


@dataclass(frozen=True)
class HistoricalDecisionPacket:
    packet_id: str
    parent_packet_id: str | None
    version: int
    symbol: str
    signal_date: date
    expires_on: date
    direction: str
    action: str
    layer: str
    market_regime: str
    formal_score: float
    confidence: float
    veto_triggered: bool
    frozen_entry_zone: tuple[float, ...]
    frozen_preferred_zone: tuple[float, ...]
    frozen_stop_loss_price: float | None
    zone_version: str
    content_sha256: str


@dataclass(frozen=True)
class ExecutionUpdate:
    trade_date: date
    current_price: float
    execution_status: str
    status_reason: str


@dataclass(frozen=True)
class TimelineOutcome:
    packet: HistoricalDecisionPacket
    terminal_reason: str
    terminal_date: date
    entered_allowed_zone: bool
    entered_preferred_zone: bool
    preferred_entry_day: int | None
    preferred_entry_date: date | None
    action_at_entry: str | None
    core_structure_at_entry: bool | None
    returns: dict[int, float | None]
    maximum_favorable_excursion: float | None
    maximum_adverse_excursion: float | None
    updates: tuple[ExecutionUpdate, ...]


@dataclass(frozen=True)
class TimelineReplayResult:
    signal_dates_completed: int
    packets: tuple[HistoricalDecisionPacket, ...]
    outcomes: tuple[TimelineOutcome, ...]
    duplicate_signals_skipped: int
    orders_created: int = 0
    positions_changed: int = 0


@dataclass
class _PacketState:
    packet: HistoricalDecisionPacket
    decision: FormalReplayDecision
    replay_input: FormalReplayInput
    updates: list[ExecutionUpdate]
    entered_allowed_zone: bool = False
    entered_preferred_zone: bool = False
    preferred_entry_day: int | None = None
    preferred_entry_date: date | None = None
    terminal_reason: str | None = None
    terminal_date: date | None = None


def _direction(action: str) -> str:
    if action in BUY_ACTIONS:
        return "BUY"
    if action in {"AVOID", "EXIT", "REDUCE", "STOP_LOSS"}:
        return "RISK_OFF"
    return "WAIT"


def _packet_hash(payload: dict) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _make_packet(
    decision: FormalReplayDecision,
    *,
    version: int,
    parent_packet_id: str | None,
) -> HistoricalDecisionPacket:
    payload = {
        "symbol": decision.symbol,
        "signal_date": decision.signal_date,
        "version": version,
        "parent_packet_id": parent_packet_id,
        "action": decision.action,
        "layer": decision.layer.value,
        "market_regime": decision.market_regime,
        "formal_score": decision.formal_score,
        "confidence": decision.confidence,
        "veto_triggered": decision.veto_triggered,
        "frozen_entry_zone": decision.frozen_entry_zone,
        "frozen_preferred_zone": decision.frozen_preferred_zone,
        "frozen_stop_loss_price": decision.frozen_stop_loss_price,
        "zone_version": "V1",
    }
    digest = _packet_hash(payload)
    return HistoricalDecisionPacket(
        packet_id=f"historical_decision_{digest[:24]}",
        parent_packet_id=parent_packet_id,
        version=version,
        symbol=decision.symbol,
        signal_date=decision.signal_date,
        expires_on=decision.signal_date + timedelta(
            days=ZONE_VALID_CALENDAR_DAYS
        ),
        direction=_direction(decision.action),
        action=decision.action,
        layer=decision.layer.value,
        market_regime=decision.market_regime,
        formal_score=decision.formal_score,
        confidence=decision.confidence,
        veto_triggered=decision.veto_triggered,
        frozen_entry_zone=tuple(decision.frozen_entry_zone),
        frozen_preferred_zone=tuple(decision.frozen_preferred_zone),
        frozen_stop_loss_price=decision.frozen_stop_loss_price,
        zone_version="V1",
        content_sha256=digest,
    )


def _bar_by_date(decision: FormalReplayDecision) -> dict[date, FutureBar]:
    return {bar.trade_date: bar for bar in decision.observation.future_bars}


def _core_structure_at(
    state: _PacketState,
    entry_date: date,
) -> bool | None:
    bars = [point.bar for point in state.replay_input.bars]
    for future in state.decision.observation.future_bars:
        if future.trade_date > entry_date:
            break
        if (
            future.open is None
            or future.high is None
            or future.low is None
            or future.close is None
        ):
            continue
        bars.append(
            Bar(
                trade_date=future.trade_date,
                open=float(future.open),
                high=float(future.high),
                low=float(future.low),
                close=float(future.close),
                volume=float(future.volume or 0),
            )
        )
    if len(bars) < 60:
        return None
    bars = bars[-60:]
    closes = [bar.close for bar in bars]
    sma20 = fmean(closes[-20:])
    sma60 = fmean(closes[-60:])
    score = technical_signal(bars).score
    return closes[-1] > sma20 > sma60 and score > 0.3


def _factor(bar: FutureBar) -> float:
    value = bar.adjustment_factor
    return 1.0 if value is None or value <= 0 else float(value)


def _entry_metrics(
    state: _PacketState,
) -> tuple[dict[int, float | None], float | None, float | None]:
    entry_date = state.preferred_entry_date
    if entry_date is None:
        return {horizon: None for horizon in REQUIRED_HORIZONS}, None, None
    bars = list(state.decision.observation.future_bars)
    index = next(
        (i for i, bar in enumerate(bars) if bar.trade_date == entry_date),
        None,
    )
    if index is None or bars[index].close is None:
        return {horizon: None for horizon in REQUIRED_HORIZONS}, None, None
    entry_bar = bars[index]
    entry_price = float(entry_bar.close)
    entry_factor = _factor(entry_bar)
    returns: dict[int, float | None] = {}
    for horizon in REQUIRED_HORIZONS:
        exit_index = index + horizon
        if exit_index >= len(bars) or bars[exit_index].close is None:
            returns[horizon] = None
            continue
        exit_bar = bars[exit_index]
        returns[horizon] = (
            float(exit_bar.close) * _factor(exit_bar)
            / (entry_price * entry_factor)
            - 1
        )
    following = bars[index + 1 :]
    highs = [
        float(bar.high) * _factor(bar) / (entry_price * entry_factor) - 1
        for bar in following
        if bar.high is not None
    ]
    lows = [
        float(bar.low) * _factor(bar) / (entry_price * entry_factor) - 1
        for bar in following
        if bar.low is not None
    ]
    return (
        returns,
        None if not highs else max(highs),
        None if not lows else min(lows),
    )


def _finish(state: _PacketState) -> TimelineOutcome:
    if state.terminal_reason is None or state.terminal_date is None:
        raise RuntimeError("timeline packet is not terminal")
    returns, favorable, adverse = _entry_metrics(state)
    return TimelineOutcome(
        packet=state.packet,
        terminal_reason=state.terminal_reason,
        terminal_date=state.terminal_date,
        entered_allowed_zone=state.entered_allowed_zone,
        entered_preferred_zone=state.entered_preferred_zone,
        preferred_entry_day=state.preferred_entry_day,
        preferred_entry_date=state.preferred_entry_date,
        action_at_entry=(
            state.packet.action if state.entered_preferred_zone else None
        ),
        core_structure_at_entry=(
            _core_structure_at(state, state.preferred_entry_date)
            if state.preferred_entry_date is not None
            else None
        ),
        returns=returns,
        maximum_favorable_excursion=favorable,
        maximum_adverse_excursion=adverse,
        updates=tuple(state.updates),
    )


class FormalTimelineReplayService:
    """Pure research replay over frozen formal decisions; never persists."""

    def replay(
        self,
        decisions: Iterable[FormalReplayDecision],
        inputs: Iterable[FormalReplayInput],
    ) -> TimelineReplayResult:
        decision_items = sorted(
            decisions,
            key=lambda item: (item.signal_date, item.symbol),
        )
        input_map = {
            (item.signal_date, item.symbol): item for item in inputs
        }
        by_signal_date: dict[date, list[FormalReplayDecision]] = {}
        all_dates: set[date] = set()
        for decision in decision_items:
            by_signal_date.setdefault(decision.signal_date, []).append(decision)
            all_dates.add(decision.signal_date)
            all_dates.update(
                bar.trade_date
                for bar in decision.observation.future_bars
            )
        active: dict[str, _PacketState] = {}
        histories: dict[str, list[HistoricalDecisionPacket]] = {}
        outcomes: list[TimelineOutcome] = []
        packets: list[HistoricalDecisionPacket] = []
        duplicate_signals_skipped = 0

        for current_date in sorted(all_dates):
            for symbol, state in list(active.items()):
                if current_date <= state.packet.signal_date:
                    continue
                bar = _bar_by_date(state.decision).get(current_date)
                if bar is not None and bar.close is not None:
                    current_price = float(bar.close)
                    status, reason = compute_execution_status(
                        current_price=current_price,
                        action=state.packet.action,
                        veto_triggered=state.packet.veto_triggered,
                        frozen_entry_zone=list(state.packet.frozen_entry_zone),
                        frozen_preferred_zone=list(
                            state.packet.frozen_preferred_zone
                        ),
                        frozen_stop_loss_price=float(
                            state.packet.frozen_stop_loss_price or 0
                        ),
                    )
                    state.updates.append(
                        ExecutionUpdate(
                            trade_date=current_date,
                            current_price=current_price,
                            execution_status=status,
                            status_reason=reason,
                        )
                    )
                    entry = state.packet.frozen_entry_zone
                    preferred = state.packet.frozen_preferred_zone
                    if len(entry) == 2 and entry[0] <= current_price <= entry[1]:
                        state.entered_allowed_zone = True
                    if (
                        state.packet.action in BUY_ACTIONS
                        and len(preferred) == 2
                        and preferred[0] <= current_price <= preferred[1]
                    ):
                        state.entered_preferred_zone = True
                        state.preferred_entry_date = current_date
                        state.preferred_entry_day = len(state.updates)
                        state.terminal_reason = "ENTERED_PREFERRED_ZONE"
                    elif (
                        state.packet.veto_triggered
                        or state.packet.action == "AVOID"
                    ):
                        state.terminal_reason = "VETO_OR_AVOID"
                    elif (
                        state.packet.frozen_stop_loss_price is not None
                        and state.packet.frozen_stop_loss_price > 0
                        and current_price
                        <= state.packet.frozen_stop_loss_price
                    ):
                        state.terminal_reason = "SIGNAL_INVALID"
                    elif (
                        state.packet.action in BUY_ACTIONS
                        and len(entry) == 2
                        and current_price > entry[1]
                    ):
                        state.terminal_reason = "FORBID_CHASE"
                    if state.terminal_reason is not None:
                        state.terminal_date = current_date
                if (
                    state.terminal_reason is None
                    and current_date >= state.packet.expires_on
                ):
                    state.terminal_reason = "EXPIRED"
                    state.terminal_date = current_date
                if state.terminal_reason is not None:
                    outcomes.append(_finish(state))
                    del active[symbol]

            for decision in by_signal_date.get(current_date, []):
                replay_input = input_map.get(
                    (decision.signal_date, decision.symbol)
                )
                if replay_input is None:
                    raise ValueError("formal replay input is missing")
                direction = _direction(decision.action)
                previous = active.get(decision.symbol)
                if previous is not None:
                    if previous.packet.direction == direction:
                        duplicate_signals_skipped += 1
                        continue
                    previous.terminal_reason = "FORMAL_REDECISION"
                    previous.terminal_date = current_date
                    outcomes.append(_finish(previous))
                    del active[decision.symbol]
                history = histories.setdefault(decision.symbol, [])
                parent = history[-1].packet_id if history else None
                packet = _make_packet(
                    decision,
                    version=len(history) + 1,
                    parent_packet_id=parent,
                )
                history.append(packet)
                packets.append(packet)
                state = _PacketState(
                    packet=packet,
                    decision=decision,
                    replay_input=replay_input,
                    updates=[],
                )
                if decision.veto_triggered or decision.action == "AVOID":
                    state.terminal_reason = "VETO_OR_AVOID"
                    state.terminal_date = current_date
                    outcomes.append(_finish(state))
                else:
                    active[decision.symbol] = state

        last_date = max(all_dates) if all_dates else None
        for state in active.values():
            state.terminal_reason = "DATA_WINDOW_END"
            state.terminal_date = last_date or state.packet.signal_date
            outcomes.append(_finish(state))

        return TimelineReplayResult(
            signal_dates_completed=len(by_signal_date),
            packets=tuple(packets),
            outcomes=tuple(sorted(
                outcomes,
                key=lambda item: (
                    item.packet.signal_date,
                    item.packet.symbol,
                    item.packet.version,
                ),
            )),
            duplicate_signals_skipped=duplicate_signals_skipped,
        )


def summarize_timeline_returns(
    outcomes: Iterable[TimelineOutcome],
) -> dict[int, dict[str, float | int | None]]:
    items = [item for item in outcomes if item.entered_preferred_zone]
    result: dict[int, dict[str, float | int | None]] = {}
    for horizon in REQUIRED_HORIZONS:
        values = [
            float(item.returns[horizon])
            for item in items
            if item.returns.get(horizon) is not None
        ]
        result[horizon] = {
            "sample_count": len(values),
            "win_rate": (
                None if not values else sum(value > 0 for value in values) / len(values)
            ),
            "average_return": None if not values else fmean(values),
            "median_return": None if not values else median(values),
        }
    return result


__all__ = [
    "BUY_ACTIONS",
    "ExecutionUpdate",
    "FormalTimelineReplayService",
    "HistoricalDecisionPacket",
    "TimelineOutcome",
    "TimelineReplayResult",
    "ZONE_VALID_CALENDAR_DAYS",
    "summarize_timeline_returns",
]