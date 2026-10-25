"""Wealth Lab v3 路由：多资产研究池（v1/v2 不受影响）。"""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.backtest import compute_v2_metrics, run_strategy_backtest
from trading.wealth_lab.repository import WealthLabRepository
from trading.wealth_lab.schemas import envelope


def envelope_v3(data, **kwargs):
    return envelope(data, api_version="wealth-lab/v3", **kwargs)
from trading.wealth_lab.strategies import get_strategy
from trading.wealth_lab.universe import (
    assets_report,
    backtest_eligible,
    list_universes,
    universe_members,
)

router = APIRouter(prefix="/v1/wealth-lab/v3", tags=["wealth-lab-v3"])
repository = WealthLabRepository()


class UniverseBacktestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    universe_id: str
    start_date: date | None = None
    end_date: date | None = None
    cost_bps: float = Field(default=5.0, ge=0, le=500)
    params_override: dict = Field(default_factory=dict)


@router.get("/universes")
def v3_universes():
    return envelope_v3({"data_mode": "REAL", "universes": list_universes()})


@router.get("/universes/{universe_id}/assets")
def v3_universe_assets(universe_id: str):
    members = universe_members(universe_id)
    if not members:
        raise HTTPException(status_code=404, detail="universe not found")
    rows = [a for a in assets_report(members)]
    return envelope_v3({"data_mode": "REAL", "universe_id": universe_id,
                     "assets": rows, "members": len(rows)})


@router.get("/quality")
def v3_quality(category: str | None = None, status: str | None = None,
               q: str | None = None, page: int = 1, limit: int | None = None):
    rows = assets_report()
    if category:
        rows = [r for r in rows if r["category"] == category]
    if status:
        rows = [r for r in rows if r["quality_status"] == status.upper()]
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows
                if needle in r["symbol"].lower() or needle in str(r.get("name", "")).lower()]
    total = len(rows)
    page = max(1, page)
    limit = max(1, min(limit or total, total)) if total else 0
    start_index = (page - 1) * limit if limit else 0
    page_rows = rows[start_index: start_index + limit] if limit else []
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["quality_status"]] = counts.get(row["quality_status"], 0) + 1
    return envelope_v3({
        "data_mode": "REAL", "total": total, "quality_counts": counts,
        "page": page, "limit": limit or total,
        "next_page": page + 1 if start_index + limit < total else None,
        "assets": page_rows,
        "notes": ["缺失率以池内真实交易日历（SSE）为分母", "复权缺失不填充；金额已统一为元",
                  "v3.1 新增 product_form/underlying/market_scope/currency_risk 维度"],
    })


@router.get("/eligible")
def v3_eligible(universe_id: str, asof: date):
    members = universe_members(universe_id)
    if not members:
        raise HTTPException(status_code=404, detail="universe not found")
    result = backtest_eligible(members, asof)
    return envelope_v3({
        "data_mode": "REAL", "universe_id": universe_id, "asof": asof.isoformat(),
        "eligible_count": sum(1 for v in result.values() if v["eligible"]),
        "total": len(result),
        "survivorship_note": "基于当前存续名录构建，未含历史退市产品；偏差未消除、仅如实记录",
        "eligibility": [
            {"symbol": s, **v} for s, v in sorted(result.items())
        ],
    })


@router.post("/strategies/{strategy_id}/backtest")
def v3_universe_backtest(strategy_id: str, body: UniverseBacktestBody):
    members = universe_members(body.universe_id)
    if not members:
        raise HTTPException(status_code=404, detail="universe not found")
    try:
        strategy = get_strategy(strategy_id, **body.params_override)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    close = repository.read_close_matrix(members)
    if close.empty:
        return envelope_v3({"data_mode": "UNAVAILABLE", "reason": "池内无共同行情"},
                        status="FAILED", error_code="DATA_UNAVAILABLE",
                        sanitized_error="数据不可用：池内无共同行情")
    start = body.start_date or close.index[0].date()
    end = body.end_date or close.index[-1].date()
    eligible = backtest_eligible(members, start)
    excluded = {s: v["reasons"] for s, v in eligible.items() if not v["eligible"]}
    eligible_symbols = [s for s, v in eligible.items() if v["eligible"]]

    import uuid

    # PIT 拒绝时给出建议起始日（P2-3）：逐月向前扫描
    def _suggested_start(members_list, wanted):
        import calendar
        probe = wanted
        for _ in range(30):
            verdict = backtest_eligible(members_list, probe)
            if any(v["eligible"] for v in verdict.values()):
                return probe.isoformat()
            year, month = (probe.year + 1, 1) if probe.month == 12 else (probe.year, probe.month + 1)
            probe = date(year, month, min(4, calendar.monthrange(year, month)[1]))
        return None

    eligible_at_start = backtest_eligible(members, start)
    if not any(v["eligible"] for v in eligible_at_start.values()):
        suggested = _suggested_start(members, start)
        return envelope_v3({
            "data_mode": "UNAVAILABLE",
            "reason": f"asof {start} 时点池内无可投资资产（PIT 闸门）",
            "requested_start_date": start.isoformat(),
            "suggested_start_date": suggested,
            "excluded_reasons": {s_: v["reasons"] for s_, v in eligible_at_start.items()},
        }, status="FAILED", error_code="DATA_UNAVAILABLE",
           sanitized_error="数据不可用：回测起点无 PIT 合格资产"
                           + (f"；建议起始日 {suggested}" if suggested else ""))

    pit_records: dict[str, dict] = {}

    def eligibility_fn(decision_date, symbols):
        verdict = backtest_eligible(list(symbols), decision_date)
        eligible = {s for s, v in verdict.items() if v["eligible"]}
        pit_records[decision_date.isoformat()] = {
            "eligible_assets": sorted(eligible),
            "excluded_reasons": {s: v["reasons"][:2] for s, v in verdict.items() if not v["eligible"]},
        }
        return eligible

    try:
        output = run_strategy_backtest(close, strategy, start_date=start, end_date=end,
                                       cost_bps=body.cost_bps, eligibility_fn=eligibility_fn)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    backtest_id = "wlbt_" + uuid.uuid4().hex[:12]
    metrics = compute_v2_metrics(output)

    # 逐调仓完整记录（P0-1）
    cost_by_date: dict[str, float] = {}
    for rb in output.rebalances:
        cost_by_date[rb.date.isoformat()] = cost_by_date.get(rb.date.isoformat(), 0.0) + rb.cost
    rebalance_records = []
    for snap in output.weight_history:
        date_key = snap["date"]
        pit = pit_records.get(snap.get("decision_data_through", ""), {})
        weights = snap["weights"]
        rebalance_records.append({
            "decision_date": date_key,
            "decision_data_through": snap["decision_data_through"],
            "eligible_assets": pit.get("eligible_assets", []),
            "selected_assets": sorted(weights),
            "target_weights": weights,
            "cash_weight": round(max(0.0, 1.0 - sum(weights.values())), 6),
            "cost": cost_by_date.get(date_key, 0.0),
            "excluded_reasons": pit.get("excluded_reasons", {}),
        })
    first_invested = next((r["decision_date"] for r in rebalance_records
                           if r["selected_assets"]), None)
    start_shift_reason = None
    if output.dates[0].isoformat() != start.isoformat():
        start_shift_reason = f"请求起点 {start} 非交易日或无行情，顺延至首个有效交易日"
    elif rebalance_records and not rebalance_records[0]["selected_assets"]:
        start_shift_reason = "起点时点策略历史预热（min_history 不足），首期持币"

    result = {
        "data_mode": "REAL",
        "backtest_id": backtest_id,
        "universe_id": body.universe_id,
        "strategy": strategy.describe(),
        "time_semantics": {
            "requested_start_date": start.isoformat(),
            "effective_start_date": output.dates[0].isoformat(),
            "first_investment_date": first_invested,
            "end_date": output.dates[-1].isoformat(),
            "actual_trading_days": len(output.dates),
            "start_shift_reason": start_shift_reason,
            "note": "决策只用截至前一交易日数据；PIT 资格逐调仓重评",
        },
        "common_window": {"start_date": output.dates[0].isoformat(),
                          "end_date": output.dates[-1].isoformat(),
                          "trading_days": len(output.dates)},
        "pit_gate": {"asof": start.isoformat(), "eligible": eligible_symbols,
                     "excluded": excluded,
                     "note": "PIT 判定基于上市/退市/历史长度/流动性；幸存者偏差未消除"},
        "pit_rebalance_snapshots": {
            "note": "每个调仓决策时点重新评估资格；不合格资产清仓至现金",
            "rebalances": len(rebalance_records),
            "first": rebalance_records[0] if rebalance_records else None,
            "last": rebalance_records[-1] if rebalance_records else None,
            "eligible_count_min": min((len(r["eligible_assets"]) for r in rebalance_records), default=0),
            "eligible_count_max": max((len(r["eligible_assets"]) for r in rebalance_records), default=0),
        },
        "rebalance_records": rebalance_records,
        "series": {"dates": [d.isoformat() for d in output.dates],
                   "nav": [float(v) for v in output.nav],
                   "drawdown": [float(v) for v in output.drawdown],
                   "cash_weight": [round(float(v), 6) for v in output.cash_weight],
                   "demo": False},
        "metrics": metrics,
        "weight_history": output.weight_history,
        "rebalances": [{"date": rb.date.isoformat(), "trades": rb.trades, "cost": rb.cost}
                       for rb in output.rebalances],
    }
    repository.save_backtest(
        backtest_id,
        {"strategy_id": strategy_id, "universe_id": body.universe_id, **body.params_override},
        result,
    )
    return envelope_v3(result)
