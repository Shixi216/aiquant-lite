"""Wealth Lab v3 路由：多资产研究池（v1/v2 不受影响）。"""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.backtest import compute_v2_metrics, run_strategy_backtest
from trading.wealth_lab.repository import WealthLabRepository
from trading.wealth_lab.schemas import envelope
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
    return envelope({"data_mode": "REAL", "universes": list_universes()})


@router.get("/universes/{universe_id}/assets")
def v3_universe_assets(universe_id: str):
    members = universe_members(universe_id)
    if not members:
        raise HTTPException(status_code=404, detail="universe not found")
    rows = [a for a in assets_report(members)]
    return envelope({"data_mode": "REAL", "universe_id": universe_id,
                     "assets": rows, "members": len(rows)})


@router.get("/quality")
def v3_quality(category: str | None = None, page: int = 1, limit: int | None = None):
    rows = assets_report()
    if category:
        rows = [r for r in rows if r["category"] == category]
    total = len(rows)
    page = max(1, page)
    limit = max(1, min(limit or total, total)) if total else 0
    start_index = (page - 1) * limit if limit else 0
    page_rows = rows[start_index: start_index + limit] if limit else []
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["quality_status"]] = counts.get(row["quality_status"], 0) + 1
    return envelope({
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
    return envelope({
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
        return envelope({"data_mode": "UNAVAILABLE", "reason": "池内无共同行情"},
                        status="FAILED", error_code="DATA_UNAVAILABLE",
                        sanitized_error="数据不可用：池内无共同行情")
    start = body.start_date or close.index[0].date()
    end = body.end_date or close.index[-1].date()
    eligible = backtest_eligible(members, start)
    excluded = {s: v["reasons"] for s, v in eligible.items() if not v["eligible"]}
    eligible_symbols = [s for s, v in eligible.items() if v["eligible"]]
    if not eligible_symbols:
        return envelope({"data_mode": "UNAVAILABLE",
                         "reason": f"asof {start} 时点池内无可投资资产（PIT 闸门）"},
                        status="FAILED", error_code="DATA_UNAVAILABLE",
                        sanitized_error="数据不可用：回测起点无 PIT 合格资产")

    import uuid
    pit_snapshots: list[dict] = []

    def eligibility_fn(decision_date, symbols):
        verdict = backtest_eligible(list(symbols), decision_date)
        eligible = {s for s, v in verdict.items() if v["eligible"]}
        pit_snapshots.append({
            "decision_date": decision_date.isoformat(),
            "eligible_count": len(eligible),
            "excluded": {s: v["reasons"][:1] for s, v in verdict.items() if not v["eligible"]},
        })
        return eligible

    try:
        output = run_strategy_backtest(close, strategy, start_date=start, end_date=end,
                                       cost_bps=body.cost_bps, eligibility_fn=eligibility_fn)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    backtest_id = "wlbt_" + uuid.uuid4().hex[:12]
    metrics = compute_v2_metrics(output)
    result = {
        "data_mode": "REAL",
        "backtest_id": backtest_id,
        "universe_id": body.universe_id,
        "strategy": strategy.describe(),
        "common_window": {"start_date": output.dates[0].isoformat(),
                          "end_date": output.dates[-1].isoformat(),
                          "trading_days": len(output.dates)},
        "pit_gate": {"asof": start.isoformat(), "eligible": eligible_symbols,
                     "excluded": excluded,
                     "note": "PIT 判定基于上市/退市/历史长度/流动性；幸存者偏差未消除"},
        "pit_rebalance_snapshots": {
            "note": "每个调仓决策时点重新评估资格；不合格资产清仓至现金",
            "rebalances": len(pit_snapshots),
            "first": pit_snapshots[0] if pit_snapshots else None,
            "last": pit_snapshots[-1] if pit_snapshots else None,
            "eligible_count_min": min((s["eligible_count"] for s in pit_snapshots), default=0),
            "eligible_count_max": max((s["eligible_count"] for s in pit_snapshots), default=0),
        },
        "series": {"dates": [d.isoformat() for d in output.dates],
                   "nav": [round(float(v), 6) for v in output.nav],
                   "drawdown": [round(float(v), 6) for v in output.drawdown],
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
    return envelope(result)
