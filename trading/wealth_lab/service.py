"""Wealth Lab 服务层：数据池 → 回测引擎 → 契约响应（REAL/DEMO/UNAVAILABLE）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from trading.wealth_lab.backtest import (
    BacktestParams,
    BacktestResult,
    compute_metrics,
    compute_rolling,
    generate_demo_close,
    run_backtest,
)
from trading.wealth_lab.config import (
    ASSETS,
    CONTRACT_VERSION,
    DEFAULT_BENCHMARKS,
    DEFAULT_WEIGHTS,
    ENGINE_VERSION,
    RF_ANNUAL,
)
from trading.wealth_lab.repository import WealthLabRepository

_TZ = timezone(timedelta(hours=8))
_MIN_REAL_DAYS = 250  # REAL 模式要求 ≥250 个共同交易日（约一年）
_ALL_SYMBOLS = list(ASSETS)


def _backtest_id() -> str:
    import uuid

    return "wlbt_" + uuid.uuid4().hex[:12]


class WealthLabService:
    def __init__(self, repository: WealthLabRepository | None = None) -> None:
        self.repository = repository or WealthLabRepository()

    # ---- meta / assets ----
    def meta(self) -> dict:
        pool = self.repository.pool_status()
        mode = "REAL" if pool["row_count"] >= _MIN_REAL_DAYS else "UNAVAILABLE"
        return {
            "data_mode": mode,
            "contract_version": CONTRACT_VERSION,
            "engine_version": ENGINE_VERSION,
            "pool": pool,
        }

    def assets(self) -> dict:
        rows = []
        mode = "REAL"
        for symbol, meta in ASSETS.items():
            coverage = self.repository.asset_coverage(symbol)
            status = "REAL" if coverage["rows"] >= _MIN_REAL_DAYS else (
                "DEMO" if coverage["rows"] > 0 else "UNAVAILABLE"
            )
            if status != "REAL":
                mode = status
            rows.append(
                {
                    "symbol": symbol,
                    "name": meta["name"],
                    "category": meta["category"],
                    "default_weight": meta["default_weight"],
                    "listing_date": meta["listing_date"],
                    "data_first_date": coverage["first"],
                    "data_last_date": coverage["last"],
                    "data_rows": coverage["rows"],
                    "data_status": status,
                    "source_breakdown": coverage["source_breakdown"],
                }
            )
        return {"data_mode": mode, "assets": rows}

    # ---- backtest ----
    def run(self, request) -> dict:
        weights = request.weights or DEFAULT_WEIGHTS
        close = self.repository.read_close_matrix(sorted(weights))
        if close.empty or len(close) < 2:
            if request.allow_demo:
                return self._run_demo(request, weights)
            return self._unavailable("数据池为空或共同交易日不足")
        start = request.start_date or close.index[0].date()
        end = request.end_date or close.index[-1].date()
        params = BacktestParams(
            weights=weights,
            start_date=start,
            end_date=end,
            rebalance=request.rebalance,
            cost_bps=request.cost_bps,
            initial_capital=request.initial_capital,
        )
        frame = close.loc[pd.Timestamp(start): pd.Timestamp(end)]
        if len(frame) < 2:
            if request.allow_demo:
                return self._run_demo(request, weights)
            return self._unavailable(
                f"请求区间 [{start}..{end}] 内共同交易日不足（{len(frame)} 天）"
            )
        try:
            result = run_backtest(close, params)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        data_mode = "REAL" if len(frame) >= _MIN_REAL_DAYS else (
            "DEMO" if request.allow_demo else "REAL"
        )
        backtest_id = _backtest_id()
        data = self._assemble(
            backtest_id, params, result, close, request, data_mode, len(frame)
        )
        self.repository.save_backtest(backtest_id, params_dict(request, weights), data)
        return data

    def _run_demo(self, request, weights: dict) -> dict:
        close = generate_demo_close(sorted(weights))
        demo_start = close.index[0].date()
        demo_end = close.index[-1].date()
        # 合成序列覆盖 2020-04 起；用户区间与之无交集时改用合成序列自身区间
        start = request.start_date or demo_start
        end = request.end_date or demo_end
        if start > demo_end or end < demo_start:
            start, end = demo_start, demo_end
        start = max(start, demo_start)
        end = min(end, demo_end)
        params = BacktestParams(
            weights=weights, start_date=start, end_date=end,
            rebalance=request.rebalance, cost_bps=request.cost_bps,
            initial_capital=request.initial_capital,
        )
        result = run_backtest(close, params)
        backtest_id = _backtest_id()
        data = self._assemble(backtest_id, params, result, close, request, "DEMO", len(close))
        data["series"]["demo"] = True
        data["data_quality"] = {
            "adjusted": False, "sources": ["synthetic"], "missing_days": 0,
            "caveats": ["DEMO 合成数据：仅供前端联调，禁止用于任何真实结论"],
        }
        return data

    def _assemble(
        self,
        backtest_id: str,
        params: BacktestParams,
        result: BacktestResult,
        close: pd.DataFrame,
        request,
        data_mode: str,
        days: int,
    ) -> dict:
        benchmarks = []
        benchmark_nav_series = None
        for name in request.benchmarks or DEFAULT_BENCHMARKS:
            if name == "EQUAL_WEIGHT":
                equal = {s: 1.0 / len(params.weights) for s in params.weights}
                b_params = BacktestParams(
                    weights=equal, start_date=params.start_date, end_date=params.end_date,
                    rebalance=params.rebalance, cost_bps=params.cost_bps,
                    initial_capital=params.initial_capital,
                )
                b_result = run_backtest(close, b_params)
            elif name in close.columns:
                b_result = run_backtest(
                    close, BacktestParams(
                        weights={name: 1.0}, start_date=params.start_date, end_date=params.end_date,
                        rebalance="none", cost_bps=0.0, initial_capital=params.initial_capital,
                    )
                )
            else:
                continue
            benchmarks.append({"benchmark": name, "metrics": compute_metrics(b_result)})
            if name == "EQUAL_WEIGHT":
                benchmark_nav_series = b_result.nav
        breakdown = self.repository.source_breakdown_for(
            sorted(params.weights), params.start_date, params.end_date
        ) if data_mode == "REAL" else {}
        return {
            "data_mode": data_mode,
            "backtest_id": backtest_id,
            "params": {
                "weights": params.weights,
                "start_date": params.start_date.isoformat(),
                "end_date": params.end_date.isoformat(),
                "rebalance": params.rebalance,
                "cost_bps": params.cost_bps,
                "initial_capital": params.initial_capital,
                "benchmarks": request.benchmarks or DEFAULT_BENCHMARKS,
                "allow_demo": request.allow_demo,
            },
            "series": {
                "dates": [d.isoformat() for d in result.dates],
                "portfolio_nav": [round(float(v), 6) for v in result.nav],
                "portfolio_drawdown": [round(float(v), 6) for v in result.drawdown],
                "benchmark_nav": (
                    [round(float(v), 6) for v in benchmark_nav_series]
                    if benchmark_nav_series is not None else []
                ),
                "demo": data_mode == "DEMO",
            },
            "rebalances": [
                {"date": ev.date.isoformat(), "trades": ev.trades, "cost": round(ev.cost, 2)}
                for ev in result.rebalances
            ],
            "metrics": compute_metrics(result),
            "benchmarks": benchmarks,
            "data_quality": {
                "adjusted": True,
                "sources": ["xiaodefa-tushare/fund_daily", "xiaodefa-tushare/fund_adj"],
                "source_breakdown": breakdown,
                "missing_days": 0,
                "caveats": [
                    "净值基于 fund_adj 后复权因子",
                    f"共同交易日 {days} 天",
                ],
            },
        }

    def _unavailable(self, reason: str) -> dict:
        return {
            "data_mode": "UNAVAILABLE",
            "reason": reason,
        }

    # ---- risk / stress ----
    def get_backtest(self, backtest_id: str) -> dict | None:
        return self.repository.load_backtest(backtest_id)

    def risk(self, backtest_id: str) -> dict | None:
        record = self.get_backtest(backtest_id)
        if record is None:
            return None
        weights = record["params"].get("weights") or DEFAULT_WEIGHTS
        close = self.repository.read_close_matrix(sorted(weights))
        params = BacktestParams(
            weights=weights,
            start_date=date.fromisoformat(record["params"]["start_date"]),
            end_date=date.fromisoformat(record["params"]["end_date"]),
            rebalance=record["params"].get("rebalance", "monthly"),
            cost_bps=record["params"].get("cost_bps", 5.0),
            initial_capital=record["params"].get("initial_capital", 1_000_000.0),
        )
        try:
            result = run_backtest(close, params)
        except ValueError:
            result = None
        if result is None:
            return None
        rolling = compute_rolling(result)
        return {
            "data_mode": record["result"].get("data_mode", "REAL"),
            "backtest_id": backtest_id,
            "metrics": record["result"].get("metrics"),
            "rolling": {
                "dates": [d.isoformat() for d in rolling["dates"]],
                "volatility_63d": [round(v, 6) for v in rolling["volatility_63d"]],
                "sharpe_252d": [round(v, 6) for v in rolling["sharpe_252d"]],
                "drawdown": [round(v, 6) for v in rolling["drawdown"]],
            },
            "worst_days": [
                {"date": item["date"].isoformat(), "daily_return": round(item["daily_return"], 6)}
                for item in rolling["worst_days"]
            ],
            "value_at_risk_95_1d": round(rolling["value_at_risk_95_1d"], 6),
        }

    def stress(self, request) -> dict:
        weights = request.weights or DEFAULT_WEIGHTS
        scenario = request.scenario
        if scenario.kind == "synthetic":
            total = sum(weights.get(s, 0.0) * scenario.shocks.get(s, 0.0) for s in weights)
            return {
                "data_mode": "DEMO" if not self._has_real_data() else "REAL",
                "scenario_label": scenario.label,
                "portfolio_shock_return": round(total, 6),
                "portfolio_nav_path": [
                    {"date": datetime.now(_TZ).date().isoformat(), "nav": round(1.0 + total, 6)}
                ],
                "asset_contributions": [
                    {
                        "symbol": s,
                        "weight": round(weights.get(s, 0.0), 4),
                        "shock_return": round(scenario.shocks.get(s, 0.0), 6),
                        "contribution": round(weights.get(s, 0.0) * scenario.shocks.get(s, 0.0), 6),
                    }
                    for s in sorted(set(weights) | set(scenario.shocks))
                ],
            }
        # historical：真实数据窗口。窗口内可用资产子集参与（权重归一），
        # 避免单一资产晚上市（如 511380 上市于 2020-04）拒绝整个窗口。
        close = self.repository.read_close_matrix(
            sorted(weights), scenario.start_date, scenario.end_date, join="outer"
        )
        if close.empty:
            return self._unavailable("数据池为空，无法执行历史压力测试")
        window = close.dropna(axis=1, how="all").dropna()
        if len(window) < 2:
            return self._unavailable(
                f"压力窗口 [{scenario.start_date}..{scenario.end_date}] 数据不足"
            )
        returns = window.pct_change().dropna()
        available = [s for s in returns.columns if s in weights and weights[s] > 0]
        if not available:
            return self._unavailable("压力窗口内无有效资产")
        raw = np.array([weights[s] for s in available])
        weight_vec = raw / raw.sum()  # 窗口内按原权重比例归一
        portfolio = returns[available].to_numpy() @ weight_vec
        nav_path = np.cumprod(1.0 + portfolio)
        asset_cum = (1.0 + returns[available]).prod() - 1.0
        return {
            "data_mode": "REAL",
            "scenario_label": scenario.label,
            "portfolio_shock_return": round(float(nav_path[-1] - 1.0), 6),
            "portfolio_nav_path": [
                {"date": idx.date().isoformat(), "nav": round(float(nav), 6)}
                for idx, nav in zip(returns.index, nav_path)
            ],
            "asset_contributions": [
                {
                    "symbol": symbol,
                    "weight": round(float(weight_vec[i]), 4),
                    "shock_return": round(float(asset_cum[symbol]), 6),
                    "contribution": round(float(weight_vec[i] * asset_cum[symbol]), 6),
                }
                for i, symbol in enumerate(available)
            ],
            "window_missing_assets": sorted(set(weights) - set(available)),
        }

    def _has_real_data(self) -> bool:
        return self.repository.pool_status()["row_count"] >= _MIN_REAL_DAYS


def params_dict(request, weights: dict) -> dict:
    return {
        "weights": weights,
        "start_date": (request.start_date or date(2020, 1, 1)).isoformat(),
        "end_date": (request.end_date or date.today()).isoformat(),
        "rebalance": request.rebalance,
        "cost_bps": request.cost_bps,
        "initial_capital": request.initial_capital,
        "benchmarks": request.benchmarks or DEFAULT_BENCHMARKS,
        "allow_demo": request.allow_demo,
    }
