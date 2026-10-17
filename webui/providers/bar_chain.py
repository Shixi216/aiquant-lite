"""Chart bar chain: Router canonical history → local replay → provider tail.

1. Router ``GET /v1/historical-bars`` (after Router restart this is first
   priority and already returns forward-adjusted bars).
2. Local sidecar replay DB (RAW + adj_factor).
3. Tushare tail fill for missing trading days only.

Merging happens at the RAW layer first; QFQ is applied once, afterwards,
with a single anchor — an adjusted tail is never concatenated onto adjusted
history. A failed seam check rejects the merge instead of hiding a break.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import asyncio

import httpx

from webui.canonical.gateway import RouterResult, fetch_json
from webui.calculations import forward_adjust, moving_average, seam_check
from webui.calculations.bar_metrics import change_pct_series
from webui.providers import local_bars, quote_tail
from webui.providers.symbol import normalize_symbol


TAIL_FILL_REASON_OK = "ok"
TAIL_FILL_REASON_NOT_TRIGGERED = "not_triggered"
TAIL_FILL_REASON_PROVIDER_FAILED = "provider_failed"
TAIL_FILL_REASON_SEAM_REJECTED = "seam_rejected"


@dataclass
class ChartPayload:
    symbol: str
    bars: list[dict] = field(default_factory=list)
    data_source: str = "unavailable"
    formal_status: str = "unavailable"
    data_time: str | None = None
    market_coverage_date: str | None = None
    adjustment: dict = field(default_factory=dict)
    tail_fill: dict | None = None
    unavailable_reason: str | None = None


def _empty_adjustment(requested: str) -> dict:
    return {
        "requested_adjustment_type": requested,
        "effective_adjustment_type": requested,
        "adjustment_fallback": False,
        "adjustment_fallback_reason": None,
        "factor_anchor_date": None,
    }


async def _router_history(
    client: httpx.AsyncClient | None,
    symbol: str,
    days: int,
    adjustment_type: str,
) -> RouterResult | None:
    if client is None:
        return None
    return await fetch_json(
        client,
        f"/v1/historical-bars/{symbol}",
        {"days": days, "adjustment_type": adjustment_type},
    )


def _apply_qfq(
    bars: list[dict],
    requested: str,
) -> tuple[list[dict], dict, list[float | None]]:
    """Apply QFQ once over the whole merged RAW series (single anchor)."""
    factors = {
        bar["trade_date"]: float(bar["adj_factor"])
        for bar in bars
        if bar.get("adj_factor") not in (None, 0)
    }
    anchor_date, base = forward_adjust.base_factor_of(factors)
    if requested != "FORWARD_ADJUSTED" or not factors:
        outcome = forward_adjust.outcome_for(
            requested,
            factor_missing_total=not factors and requested == "FORWARD_ADJUSTED",
            factor_missing_partial=False,
            anchor_date=None,
        )
        closes = [bar["close"] for bar in bars]
        return bars, outcome.__dict__ | {"factor_anchor_date": None}, closes
    if len(factors) != len(bars):
        outcome = forward_adjust.outcome_for(
            requested,
            factor_missing_total=False,
            factor_missing_partial=True,
            anchor_date=None,
        )
        closes = [bar["close"] for bar in bars]
        return bars, outcome.__dict__, closes
    ratios = forward_adjust.ratio_series(
        [bar["trade_date"] for bar in bars], factors, float(base)
    )
    adjusted: list[dict] = []
    for bar, ratio in zip(bars, ratios):
        adjusted.append(
            {
                **bar,
                "open": None if bar["open"] is None else bar["open"] * ratio,
                "high": None if bar["high"] is None else bar["high"] * ratio,
                "low": None if bar["low"] is None else bar["low"] * ratio,
                "close": None if bar["close"] is None else bar["close"] * ratio,
            }
        )
    outcome = forward_adjust.outcome_for(
        requested,
        factor_missing_total=False,
        factor_missing_partial=False,
        anchor_date=anchor_date,
    )
    closes = [bar["close"] for bar in adjusted]
    return adjusted, outcome.__dict__, closes


def _decorate(bars: list[dict]) -> list[dict]:
    closes = [bar["close"] for bar in bars]
    change_pct = change_pct_series(closes)
    ma = moving_average.ma_series(closes)
    decorated: list[dict] = []
    for index, bar in enumerate(bars):
        decorated.append(
            {
                "trade_date": bar["trade_date"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
                "amount": bar["amount"],
                "change_pct": change_pct[index],
                "ma5": ma["ma5"][index],
                "ma10": ma["ma10"][index],
                "ma20": ma["ma20"][index],
                "ma60": ma["ma60"][index],
            }
        )
    return decorated


async def build_chart(
    symbol: str,
    *,
    days: int,
    router_client: httpx.AsyncClient | None = None,
    end_date: str | None = None,
    adjustment_type: str = "FORWARD_ADJUSTED",
    run_in_executor=True,
) -> ChartPayload:
    canonical = normalize_symbol(symbol)
    if canonical is None:
        raise ValueError(f"invalid A-share symbol: {symbol}")

    payload = ChartPayload(symbol=canonical)

    # ① Router canonical history (post-restart primary source)
    router_result = await _router_history(
        router_client, canonical, days, adjustment_type
    )
    if router_result is not None and router_result.ok:
        data = router_result.data or {}
        bars = data.get("bars", [])
        payload.bars = bars
        payload.data_source = "router_canonical_history"
        payload.formal_status = "fact"
        payload.data_time = data.get("last_trade_date")
        payload.adjustment = {
            "requested_adjustment_type": data.get("requested_adjustment_type"),
            "effective_adjustment_type": data.get("effective_adjustment_type"),
            "adjustment_fallback": data.get("adjustment_fallback"),
            "adjustment_fallback_reason": data.get("adjustment_fallback_reason"),
            "factor_anchor_date": data.get("factor_anchor_date"),
        }
        payload.tail_fill = {
            "triggered": False,
            "ok": True,
            "reason_code": TAIL_FILL_REASON_NOT_TRIGGERED,
        }
        return payload

    # ② Local replay sidecar (RAW + factors)
    local = await asyncio.to_thread(local_bars.fetch_local_raw_bars, canonical)
    if not local.bars:
        payload.unavailable_reason = "no_local_history"
        payload.data_source = "unavailable"
        payload.formal_status = "unavailable"
        payload.adjustment = _empty_adjustment(adjustment_type)
        return payload

    bars = list(local.bars)
    data_source = "local_replay_history"
    tail_fill: dict = {
        "triggered": False,
        "ok": True,
        "reason_code": TAIL_FILL_REASON_NOT_TRIGGERED,
    }

    # ③ Provider tail fill for missing trading days only
    local_last = local.last_trade_date
    effective_end = end_date or _today_iso()
    if local_last and local_last < effective_end:
        start = quote_tail.next_day_after(local_last)
        tail = await quote_tail.fetch_tail(
            canonical,
            start_date=start,
            end_date=effective_end,
        )
        if tail is None or not tail.bars:
            tail_fill = {
                "triggered": True,
                "ok": False,
                "reason_code": TAIL_FILL_REASON_PROVIDER_FAILED,
            }
        else:
            merged_tail = []
            tail_factors_complete = True
            for bar in tail.bars:
                factor = bar.get("adj_factor") or tail.factors.get(
                    bar["trade_date"]
                )
                if factor in (None, 0):
                    tail_factors_complete = False
                merged_tail.append({**bar, "adj_factor": factor})
            if not tail_factors_complete:
                # without factors we cannot prove adjustment consistency:
                # refuse the merge instead of splicing RAW onto adjusted
                tail_fill = {
                    "triggered": True,
                    "ok": False,
                    "reason_code": TAIL_FILL_REASON_SEAM_REJECTED,
                    "detail": "tail_adjustment_factors_missing",
                }
            else:
                merged = bars + merged_tail
                adjusted_merged, _adj, _closes = _apply_qfq(
                    merged, adjustment_type
                )
                seam_index = len(bars) - 1
                if adjustment_type == "FORWARD_ADJUSTED":
                    local_last_close = adjusted_merged[seam_index]["close"]
                    tail_first_open = adjusted_merged[seam_index + 1]["open"]
                else:
                    local_last_close = bars[-1]["close"]
                    tail_first_open = merged_tail[0]["open"]
                verdict = seam_check.check_seam(
                    local_last_close=local_last_close,
                    tail_first_open=tail_first_open,
                )
                if not verdict.ok:
                    tail_fill = {
                        "triggered": True,
                        "ok": False,
                        "reason_code": TAIL_FILL_REASON_SEAM_REJECTED,
                        "detail": verdict.reason,
                    }
                else:
                    tail_fill = {
                        "triggered": True,
                        "ok": True,
                        "reason_code": TAIL_FILL_REASON_OK,
                    }
                    bars = merged
                    data_source = "local_replay_history+provider_tail_fill"

    adjusted, adjustment, _closes = _apply_qfq(bars, adjustment_type)
    payload.bars = _decorate(adjusted)[-days:]
    payload.data_source = data_source
    payload.formal_status = "fact"
    payload.data_time = payload.bars[-1]["trade_date"] if payload.bars else local_last
    payload.market_coverage_date = await asyncio.to_thread(local_bars.full_market_coverage_date)
    payload.adjustment = adjustment
    payload.tail_fill = tail_fill
    return payload


def _today_iso() -> str:
    from datetime import date as _date

    return _date.today().isoformat()


__all__ = ["ChartPayload", "build_chart", "TAIL_FILL_REASON_OK"]
