"""Wealth Lab v2 HTTP 路由（/v1/wealth-lab/v2/*，独立扩展，v1 不受影响）。"""
from __future__ import annotations

from datetime import date

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from trading.wealth_lab.backtest import StrategyBacktestOutput, compute_v2_metrics, run_strategy_backtest
from trading.wealth_lab.config import ASSETS, DEFAULT_WEIGHTS
from trading.wealth_lab.decision_packets import WealthDecisionPacketStore
from trading.wealth_lab.factors import score_factors
from trading.wealth_lab.optimization import optimize_portfolio
from trading.wealth_lab.repository import WealthLabRepository
from trading.wealth_lab.risk import WealthRiskEngine
from trading.wealth_lab.schemas import envelope
from trading.wealth_lab.strategies import get_strategy, list_strategies
from trading.wealth_lab.report import build_report, render_html

router = APIRouter(prefix="/v1/wealth-lab/v2", tags=["wealth-lab-v2"])
repository = WealthLabRepository()
packets = WealthDecisionPacketStore()


class StrategyBacktestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    params_override: dict = Field(default_factory=dict)
    symbols: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    cost_bps: float = Field(default=5.0, ge=0, le=500)
    initial_capital: float = Field(default=1_000_000.0, gt=0)


class CompareBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_ids: list[str] = Field(min_length=1)
    start_date: date | None = None
    end_date: date | None = None
    cost_bps: float = Field(default=5.0, ge=0, le=500)


class OptimizationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: str = Field(pattern="^(min_vol|max_sharpe|hrp)$")
    symbols: list[str] | None = None
    estimation_window_days: int = Field(default=252, ge=60, le=756)
    asof: date | None = None
    risk_free_rate: float = Field(default=0.02, ge=0, le=0.2)
    adapter: str = Field(default="auto", pattern="^(auto|pypfopt|native)$")


class RiskCheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, float]
    backtest_id: str | None = None
    portfolio_context: dict = Field(default_factory=dict)


class AiResearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str | None = None
    backtest_id: str | None = None
    context: str | None = None


class AiRiskReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_id: str | None = None
    claims: list[str] = Field(default_factory=list)


class DecisionPacketBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: str | None = None
    strategy_id: str
    strategy_version: str | None = None
    weights: dict[str, float]
    backtest_id: str | None = None
    research_id: str | None = None
    risk_check: dict | None = None


def _backtest(strategy_id: str, body: StrategyBacktestBody) -> dict:
    strategy = get_strategy(strategy_id, **body.params_override)
    symbols = body.symbols or [s for s in DEFAULT_WEIGHTS]
    close = repository.read_close_matrix(symbols)
    if set(symbols) - set(close.columns):
        raise HTTPException(status_code=422, detail="requested asset price series missing")
    if close.empty or len(close) < 2:
        return {"data_mode": "UNAVAILABLE", "reason": "数据池不足"}
    start = body.start_date or close.index[0].date()
    end = body.end_date or close.index[-1].date()
    try:
        output = run_strategy_backtest(
            close, strategy, start_date=start, end_date=end,
            cost_bps=body.cost_bps, initial_capital=body.initial_capital,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    import uuid
    backtest_id = "wlbt_" + uuid.uuid4().hex[:12]
    metrics = compute_v2_metrics(output)
    source_rows = repository.source_breakdown_for(symbols, output.dates[0], output.dates[-1])
    recorded_sources = sorted({source for counts in source_rows.values() for source in counts})
    data_mode = "REAL" if len(output.dates) >= 250 else "REAL"
    result = {
        "data_mode": data_mode,
        "backtest_id": backtest_id,
        "strategy": strategy.describe(),
        "series": {
            "dates": [d.isoformat() for d in output.dates],
            "nav": [round(float(v), 6) for v in output.nav],
            "drawdown": [round(float(v), 6) for v in output.drawdown],
            "cash_weight": [round(float(v), 6) for v in output.cash_weight],
            "demo": False,
        },
        "metrics": metrics,
        "weight_history": output.weight_history,
        "rebalances": [
            {"date": rb.date.isoformat(), "trades": rb.trades, "cost": rb.cost}
            for rb in output.rebalances
        ],
        "data_quality": {
            "adjusted": True,
            "sources": [*(f"etf_daily/{source}" for source in recorded_sources), "fund_adj/source-not-recorded"],
            "missing_days": 0,
            "caveats": [f"共同交易日 {len(output.dates)} 天", "决策只用截至前一交易日数据；首日为空历史预设配置",
                        "现金收益为零；Sharpe=(年化复合收益-2%)/年化日收益标准差；成本含初始建仓",
                        "复权因子上游来源未逐行存档；source 仅表示已记录的行情来源"],
        },
    }
    repository.save_backtest(backtest_id, {
        "strategy_id": strategy_id, **body.model_dump(mode="json"),
        "_output": {"daily_returns": output.daily_returns.tolist(),
                    "nav": output.nav.tolist(), "drawdown": output.drawdown.tolist()},
    }, result)
    return result


@router.get("/universe")
def v2_universe():
    rows = []
    mode = "REAL"
    for symbol, meta in ASSETS.items():
        coverage = repository.asset_coverage(symbol)
        status = "REAL" if coverage["rows"] >= 250 else "UNAVAILABLE"
        if status != "REAL":
            mode = status
        rows.append({
            "symbol": symbol, "name": meta["name"],
            "category": f"CN_{meta['category']}" if not meta["category"].startswith("CN_") else meta["category"],
            "exchange": "SSE" if symbol.endswith(".SH") else "SZSE",
            "currency": "CNY", "listing_date": meta["listing_date"],
            "data_first_date": coverage["first"], "data_last_date": coverage["last"],
            "data_rows": coverage["rows"], "data_status": status,
            "source_breakdown": coverage["source_breakdown"],
            "risk_notes": meta.get("risk_notes", ""),
        })
    return envelope({"data_mode": mode, "assets": rows})


@router.get("/factors")
def v2_factors(asof: date | None = None):
    data = score_factors(repository, list(ASSETS), asof)
    if data["data_mode"] == "UNAVAILABLE":
        return envelope(data, status="FAILED", error_code="DATA_UNAVAILABLE",
                        sanitized_error=f"数据不可用：{data.get('reason','')}")
    return envelope(data)


@router.get("/strategies")
def v2_strategies():
    return envelope({"data_mode": "REAL", "strategies": list_strategies()})


@router.post("/strategies/{strategy_id}/backtest")
def v2_strategy_backtest(strategy_id: str, body: StrategyBacktestBody):
    try:
        data = _backtest(strategy_id, body)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "unknown strategy" in str(exc) else 422, detail=str(exc)) from exc
    if data.get("data_mode") == "UNAVAILABLE":
        return envelope(data, status="FAILED", error_code="DATA_UNAVAILABLE",
                        sanitized_error=f"数据不可用：{data.get('reason','')}")
    return envelope(data)


@router.post("/strategies/compare")
def v2_strategies_compare(body: CompareBody):
    comparison = []
    for sid in body.strategy_ids:
        try:
            single = _backtest(sid, StrategyBacktestBody(
                start_date=body.start_date, end_date=body.end_date, cost_bps=body.cost_bps))
        except ValueError:
            continue
        comparison.append({"strategy_id": sid, "metrics": single.get("metrics")})
    return envelope({
        "data_mode": "REAL",
        "window": {"start_date": body.start_date, "end_date": body.end_date, "cost_bps": body.cost_bps},
        "comparison": comparison,
    })


@router.post("/optimization/run")
def v2_optimization(body: OptimizationBody):
    symbols = body.symbols or list(DEFAULT_WEIGHTS)
    close = repository.read_close_matrix(symbols)
    if close.empty:
        return envelope({"data_mode": "UNAVAILABLE", "reason": "数据池不足"},
                        status="FAILED", error_code="DATA_UNAVAILABLE",
                        sanitized_error="数据不可用：数据池不足")
    if body.asof:
        close = close.loc[: pd.Timestamp(body.asof)]
    window = close.iloc[-(body.estimation_window_days + 1):]
    returns = window.pct_change().dropna()
    try:
        result = optimize_portfolio(
            body.method, returns,
            risk_free_rate=body.risk_free_rate,
            max_weight=0.6, adapter=body.adapter,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"优化失败：{type(exc).__name__}") from exc
    return envelope({
        "data_mode": "REAL",
        "method": result["method"], "adapter_used": result["adapter_used"],
        "asof": window.index[-1].date().isoformat(),
        "estimation_window": f"{window.index[0].date()}..{window.index[-1].date()}",
        "weights": result["weights"], "cash_weight": 0.0,
        **result["stats"],
        "notes": ["估计值仅为窗口内统计，非预测承诺", "只用截至 asof 的数据（防未来函数）"],
    })


@router.post("/risk/check")
def v2_risk_check(body: RiskCheckBody):
    coverage = {s: repository.asset_coverage(s) for s in body.weights}
    liquidity = repository.liquidity_20d_avg(list(body.weights))
    metrics = {}
    if body.backtest_id:
        record = repository.load_backtest(body.backtest_id)
        if record:
            metrics = record["result"].get("metrics", {})
    context = body.portfolio_context or {}
    result = WealthRiskEngine().check(
        body.weights, coverage=coverage, liquidity_20d=liquidity,
        metrics=metrics, stress_loss=context.get("stress_loss_recent"),
        ai_claims=context.get("ai_claims"),
        computed_numbers={k: v for k, v in metrics.items() if isinstance(v, (int, float))},
    )
    return envelope({"data_mode": "REAL", **result})


@router.post("/ai/research")
async def v2_ai_research(body: AiResearchBody):
    from trading.wealth_lab.ai.agents import WealthResearchAgent
    facts: dict = {"universe": [f"{s} {m['name']}" for s, m in ASSETS.items()],
                   "strategies": [], "factors": [], "context": body.context}
    backtest_id = body.backtest_id
    record = None
    if backtest_id:
        record = _linked_backtest(backtest_id, body.strategy_id)
    elif body.strategy_id:
        single = _backtest(body.strategy_id, StrategyBacktestBody())
        backtest_id = single.get("backtest_id")
        if backtest_id:
            record = _linked_backtest(backtest_id, body.strategy_id)
    if record:
        facts["strategy_id"] = record["params"]["strategy_id"]
        facts["strategies"] = [{"strategy_id": facts["strategy_id"], **record["result"]["metrics"]}]
        facts["backtest_id"] = backtest_id
        facts["backtest_window"] = _window(record)
        facts["data_quality"] = record["result"].get("data_quality", {})
    else:
        facts["context"] = {"user_context": body.context, "warning": "缺少计算证据，不得陈述任何收益或回测指标"}
    try:
        result = await WealthResearchAgent().run(facts)
    except Exception as exc:  # noqa: BLE001 - AI 失败不影响确定性模块
        return envelope({"data_mode": "UNAVAILABLE", "reason": f"AI 调用失败（{type(exc).__name__}）"},
                        status="DEGRADED", error_code="AI_UNAVAILABLE",
                        sanitized_error="AI 研究暂不可用（确定性量化模块不受影响）")
    repository.save_research_context(result["research_id"], {
        "backtest_id": backtest_id, "facts": facts, "research": result,
        "computed_numbers": record["result"].get("metrics", {}) if record else {},
    })
    return envelope({"data_mode": "REAL", "used_backtest_id": backtest_id, **result},
                    warnings=["AI_OUTPUT_IS_RESEARCH_ONLY"])


@router.post("/ai/risk-review")
async def v2_ai_risk_review(body: AiRiskReviewBody):
    from trading.wealth_lab.ai.agents import WealthRiskReviewer
    context = repository.load_research_context(body.research_id) if body.research_id else None
    numbers = {k: v for k, v in (context or {}).get("computed_numbers", {}).items()
               if isinstance(v, (int, float))}
    if not numbers:
        return envelope({"data_mode": "UNAVAILABLE", "verdict": "NEEDS_MORE_EVIDENCE",
                         "unchecked_claims": body.claims or ["研究 ID 缺少可追溯的计算证据"],
                         "hallucination_flags": [], "human_review_required": True,
                         "notes": "请提供本次回测产生的研究 ID；历史研究未持久化证据时需重新研究"},
                        status="DEGRADED", warnings=["MISSING_COMPUTED_EVIDENCE", "AI_OUTPUT_IS_RESEARCH_ONLY"])
    try:
        result = await WealthRiskReviewer().run(
            {"claims": body.claims, "research_id": body.research_id,
             "research": context["research"], "backtest_id": context["backtest_id"],
             "facts": context["facts"]}, numbers)
    except Exception as exc:  # noqa: BLE001
        return envelope({"data_mode": "UNAVAILABLE", "reason": f"AI 调用失败（{type(exc).__name__}）"},
                        status="DEGRADED", error_code="AI_UNAVAILABLE",
                        sanitized_error="AI 风险复核暂不可用")
    context["risk_review"] = result
    repository.save_research_context(body.research_id, context)
    return envelope({"data_mode": "REAL", **result}, warnings=["AI_OUTPUT_IS_RESEARCH_ONLY"])


def _linked_backtest(backtest_id: str, strategy_id: str | None) -> dict:
    record = repository.load_backtest(backtest_id)
    if record is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    if strategy_id and record["params"].get("strategy_id") != strategy_id:
        raise HTTPException(status_code=422, detail="strategy_id does not match backtest_id")
    return record


def _window(record: dict) -> dict:
    dates = record["result"].get("series", {}).get("dates", [])
    return {"start_date": dates[0] if dates else None, "end_date": dates[-1] if dates else None}


@router.post("/decision-packets")
def v2_create_packet(body: DecisionPacketBody):
    context = repository.load_research_context(body.research_id) if body.research_id else None
    if body.research_id and context is None:
        raise HTTPException(status_code=404, detail="research evidence not found; rerun research")
    linked_id = (context or {}).get("backtest_id")
    if body.backtest_id and linked_id and body.backtest_id != linked_id:
        raise HTTPException(status_code=422, detail="research_id and backtest_id refer to different runs")
    backtest_id = body.backtest_id or linked_id
    record = _linked_backtest(backtest_id, body.strategy_id) if backtest_id else None
    if record and body.strategy_version and body.strategy_version != record["result"]["strategy"]["strategy_version"]:
        raise HTTPException(status_code=422, detail="strategy_version does not match backtest")
    metrics = record["result"].get("metrics", {}) if record else {}
    coverage = {s: repository.asset_coverage(s) for s in body.weights}
    risk_check = body.risk_check or WealthRiskEngine().check(body.weights, coverage=coverage, metrics=metrics)
    warnings = ["RESEARCH_ONLY_NO_EXECUTION"]
    if not metrics:
        warnings.append("MISSING_BACKTEST_CONTEXT: provide backtest_id or linked research_id")
        risk_check = {**risk_check, "verdict": risk_check["verdict"] if risk_check["verdict"] in {"VETO", "INSUFFICIENT_DATA"} else "WARN",
                      "checks": [*risk_check.get("checks", []), {"category": "DATA", "rule": "backtest_evidence",
                                 "severity": "WARN", "triggered": True, "detail": "缺少回测关联，收益/回撤/波动未核验"}]}
    request = {**body.model_dump(), "backtest_id": backtest_id}
    if record:
        cost = record["params"].get("cost_bps")
        request["cost_assumptions"] = {"cost_bps": cost, "model": "sum|delta_value|*bps/1e4", "currency": "CNY"}
        if cost is None:
            warnings.append("LEGACY_COST_ASSUMPTIONS_NOT_RECORDED")
        warnings.append("METRICS_DESCRIBE_LINKED_STRATEGY_HISTORY_NOT_CURRENT_WEIGHTS")
    packet = packets.create(
        request, metrics=metrics, risk_check=risk_check,
        ai_research=(context or {}).get("research"), ai_risk_review=(context or {}).get("risk_review"),
        data_sources=record["result"].get("data_quality", {}).get("sources", []) if record else [],
        backtest_window=_window(record) if record else {"start_date": None, "end_date": None},
    )
    return envelope({"data_mode": "REAL", **packet}, warnings=warnings)


@router.get("/decision-packets/{packet_id}")
def v2_get_packet(packet_id: str):
    packet = packets.get(packet_id)
    if packet is None:
        raise HTTPException(status_code=404, detail="decision packet not found")
    return envelope(packet)


@router.get("/reports/{backtest_id}")
def v2_report(backtest_id: str, format: str = "json"):
    record = repository.load_backtest(backtest_id)
    if record is None:
        raise HTTPException(status_code=404, detail="backtest not found")
    import numpy as np
    saved = record["params"].get("_output")
    if not saved:
        raise HTTPException(status_code=409, detail="legacy run lacks exact daily returns; rerun backtest for report")
    series = record["result"]["series"]
    output = StrategyBacktestOutput(
        dates=[date.fromisoformat(d) for d in series["dates"]], nav=np.array(saved["nav"]),
        drawdown=np.array(saved["drawdown"]), daily_returns=np.array(saved["daily_returns"]),
        cash_weight=np.array(series["cash_weight"]), weight_history=record["result"]["weight_history"],
        rebalances=[],
    )
    report = build_report(output, backtest_id)
    report["metrics"] = record["result"]["metrics"]
    if format == "html":
        return HTMLResponse(render_html(report))
    return envelope({"data_mode": "REAL", **report})
