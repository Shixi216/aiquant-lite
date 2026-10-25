"""研究编排器：固定状态机、预算、超时、禁递归。

STABLE_RESEARCH（默认）：真实数据→策略回测→硬风险VETO→Qwen研究→Pro复核→
数值核验→决策包。模型调用≤2。
EXPERIMENTAL_AI_RESEARCH（flag）：Flash规划→白名单校验→用户确认(APPROVAL_PENDING)→
量化实验≤3→VETO→Qwen→Pro→决策包。模型调用≤3。
任何环节失败记录原因并终止；模型不可用按 MODEL_UNAVAILABLE 降级对应阶段。
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from trading.wealth_lab.research import audit
from trading.wealth_lab.research.orchestrator import (
    REVIEWER_SYSTEM,
    RESEARCHER_SYSTEM,
    ResearchPlan,
    call_with_audit,
    run_planner,
    validate_plan,
)
from trading.wealth_lab.research.roles import experimental_ai_enabled

_TZ = timezone(timedelta(hours=8))
TOTAL_TIMEOUT_SECONDS = 720
MAX_MODEL_CALLS = {"STABLE": 2, "EXPERIMENTAL": 3}
MAX_EXPERIMENTS = 3


class BudgetExceeded(RuntimeError):
    pass


class ResearchOrchestrator:
    def __init__(self):
        self._calls: dict[str, int] = {}

    def _charge(self, run_id: str, budget: int):
        used = self._calls.get(run_id, 0) + 1
        if used > budget:
            raise BudgetExceeded(f"model call budget {budget} exceeded")
        self._calls[run_id] = used
        return used

    # ---------- 稳定模式 ----------
    def run_stable(self, backtest_id: str, allow_reuse: bool = True) -> dict:
        if allow_reuse:
            cached = self._find_completed_stable(backtest_id)
            if cached is not None:
                cached["reused"] = True
                cached["note"] = "复用同 backtest_id 的已完成研究（相同数据与参数）；如需重跑传 allow_reuse=false"
                return cached
        run_id = "wlrun_" + uuid.uuid4().hex[:12]
        audit.create_run(run_id, "STABLE", None, MAX_MODEL_CALLS["STABLE"])
        result = asyncio.run(self._stable_pipeline(run_id, backtest_id))
        if result.get("status") == "COMPLETED":
            self._save_stable_cache(backtest_id, result)
        return result

    def _find_completed_stable(self, backtest_id: str) -> dict | None:
        import duckdb as _duckdb
        from trading.wealth_lab.config import WEALTH_LAB_DB_PATH
        try:
            with _duckdb.connect(str(WEALTH_LAB_DB_PATH), read_only=True) as con:
                row = con.execute(
                    "SELECT result_json FROM wealth_research_cache "
                    "WHERE backtest_id=? ORDER BY created_at DESC LIMIT 1",
                    [backtest_id]).fetchone()
        except Exception:
            return None
        if not row:
            return None
        import json as _json
        return _json.loads(row[0])

    def _save_stable_cache(self, backtest_id: str, result: dict) -> None:
        import json as _json
        import duckdb as _duckdb
        from trading.wealth_lab.config import WEALTH_LAB_DB_PATH
        from datetime import datetime, timedelta, timezone
        TZ = timezone(timedelta(hours=8))
        try:
            with _duckdb.connect(str(WEALTH_LAB_DB_PATH)) as con:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS wealth_research_cache ("
                    "backtest_id VARCHAR, created_at TIMESTAMPTZ, result_json VARCHAR)")
                con.execute("INSERT INTO wealth_research_cache VALUES (?,?,?)",
                            [backtest_id, datetime.now(TZ),
                             _json.dumps(result, ensure_ascii=False, default=str)])
        except Exception:
            pass

    async def _stable_pipeline(self, run_id: str, backtest_id: str) -> dict:
        try:
            return await asyncio.wait_for(
                self._stable_inner(run_id, backtest_id), timeout=TOTAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            audit.update_run(run_id, status="FAILED", error_reason="total timeout")
            raise RuntimeError("RESEARCH_TOTAL_TIMEOUT")
        except Exception as exc:
            audit.update_run(run_id, status="FAILED", error_reason=f"{type(exc).__name__}: {str(exc)[:200]}")
            raise

    async def _stable_inner(self, run_id: str, backtest_id: str) -> dict:
        # BACKTESTING（确定性）：加载既有回测证据
        record = self._load_backtest(backtest_id)
        if record is None:
            audit.update_run(run_id, status="FAILED", error_reason=f"backtest not found: {backtest_id}")
            raise ValueError(f"backtest not found: {backtest_id}")
        result = record["result"]
        audit.update_run(run_id, status="HARD_RISK_CHECK", stage="HARD_RISK_CHECK",
                         backtest_id=backtest_id)
        # HARD_RISK_CHECK（确定性 VETO）
        risk = self._hard_risk_check(result)
        if risk["verdict"] == "VETO":
            audit.update_run(run_id, status="FAILED", error_reason="hard VETO",
                             model_calls_used=self._calls.get(run_id, 0))
            return {"research_run_id": run_id, "status": "VETOED", "risk_check": risk,
                    "note": "硬风险 VETO：任何模型都不能解除"}
        # RESEARCH（Qwen，1 次）
        audit.update_run(run_id, status="RESEARCH", stage="RESEARCH")
        self._charge(run_id, MAX_MODEL_CALLS["STABLE"])
        facts = self._facts(result, backtest_id)
        researcher = await call_with_audit(
            run_id, "RESEARCHER", RESEARCHER_SYSTEM,
            f"已知事实（全部来自确定性计算）：\n{facts}\n请输出结构化研究摘要。",
            temperature=0.2, max_tokens=2400, backtest_id=backtest_id)
        # REVIEW（DeepSeek Pro，1 次）
        audit.update_run(run_id, status="REVIEW", stage="REVIEW",
                         model_calls_used=self._calls.get(run_id, 0))
        self._charge(run_id, MAX_MODEL_CALLS["STABLE"])
        reviewer = await call_with_audit(
            run_id, "REVIEWER", REVIEWER_SYSTEM,
            f"原始证据：\n{facts}\n\nQwen 研究结论：\n{researcher['content'][:3000]}\n\n"
            f"确定性风控结果：{risk['verdict']}（模型无权解除）\n请独立审阅。",
            temperature=0.1, max_tokens=1400, backtest_id=backtest_id)
        # VERIFICATION（确定性数值核验；AI SUPPORTED 不替代）
        audit.update_run(run_id, status="VERIFICATION", stage="VERIFICATION")
        verification = self._numeric_verification(researcher["content"], result.get("metrics", {}))
        if not verification["passed"]:
            audit.update_run(run_id, status="FAILED",
                             error_reason=f"numeric verification failed: {verification['unsupported'][:2]}",
                             model_calls_used=self._calls.get(run_id, 0))
            return {"research_run_id": run_id, "status": "VERIFICATION_FAILED",
                    "numeric_verification": verification,
                    "note": "AI 结论含无法核验的数字；按规则终止，不生成决策包"}
        # 决策包（research_only）
        packet = self._build_packet(run_id, record, risk, researcher, reviewer)
        audit.update_run(run_id, status="COMPLETED", stage="COMPLETED",
                         decision_packet_id=packet["packet_id"],
                         model_calls_used=self._calls.get(run_id, 0))
        return {
            "research_run_id": run_id, "status": "COMPLETED",
            "mode": "STABLE", "backtest_id": backtest_id,
            "risk_check": risk, "research": self._json_or_none(researcher["content"]),
            "review": self._json_or_none(reviewer["content"]),
            "numeric_verification": verification,
            "decision_packet_id": packet["packet_id"],
            "model_calls": audit.calls_for_run(run_id),
        }

    # ---------- 实验模式 ----------
    def start_experimental(self, goal: str) -> dict:
        """规划→校验→APPROVAL_PENDING（等待用户确认，不自动实验）。"""
        if not experimental_ai_enabled():
            raise PermissionError("EXPERIMENTAL_AI_RESEARCH 未启用（Feature Flag 默认关闭）")
        run_id = "wlrun_" + uuid.uuid4().hex[:12]
        audit.create_run(run_id, "EXPERIMENTAL", goal, MAX_MODEL_CALLS["EXPERIMENTAL"])
        plan = asyncio.run(self._plan_stage(run_id, goal))
        return {"research_run_id": run_id, "status": "APPROVAL_PENDING", "plan": plan}

    async def _plan_stage(self, run_id: str, goal: str) -> dict:
        try:
            return await asyncio.wait_for(self._plan_inner(run_id, goal), timeout=TOTAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            audit.update_run(run_id, status="FAILED", error_reason="total timeout (planning)")
            raise RuntimeError("PLANNING_TIMEOUT")

    async def _plan_inner(self, run_id: str, goal: str) -> dict:
        from trading.wealth_lab.strategies import list_strategies
        from trading.wealth_lab.universe import list_universes
        universes = {u["universe_id"]: u for u in list_universes()}
        strategies = {s["strategy_id"] for s in list_strategies()}
        # PLANNING（Flash，1 次；未配置 → MODEL_UNAVAILABLE 降级该阶段）
        audit.update_run(run_id, status="PLANNING", stage="PLANNING")
        self._charge(run_id, MAX_MODEL_CALLS["EXPERIMENTAL"])
        plan = await run_planner(run_id, goal, list(universes), sorted(strategies))
        # VALIDATION（确定性白名单）
        audit.update_run(run_id, status="VALIDATION", stage="VALIDATION")
        errors = validate_plan(plan, universes, strategies)
        if errors:
            audit.update_run(run_id, status="FAILED", error_reason=f"plan invalid: {errors}")
            raise ValueError(f"PLAN_INVALID: {errors}")
        audit.update_run(run_id, status="APPROVAL_PENDING", stage="APPROVAL",
                         plan_json=plan.model_dump(mode="json"))
        return plan.model_dump()

    def resume_experimental(self, run_id: str) -> dict:
        """用户确认后：量化实验→VETO→Qwen→Pro→决策包（模型调用剩余 2 次）。"""
        record = audit.get_run(run_id)
        if record is None or record["mode"] != "EXPERIMENTAL" or record["status"] != "APPROVAL_PENDING":
            raise ValueError("run not in APPROVAL_PENDING")
        return asyncio.run(self._experimental_after_approval(run_id))

    async def _experimental_after_approval(self, run_id: str) -> dict:
        try:
            return await asyncio.wait_for(self._exp_inner(run_id), timeout=TOTAL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            audit.update_run(run_id, status="FAILED", error_reason="total timeout")
            raise RuntimeError("RESEARCH_TOTAL_TIMEOUT")
        except Exception as exc:
            audit.update_run(run_id, status="FAILED",
                             error_reason=f"{type(exc).__name__}: {str(exc)[:200]}")
            raise

    async def _exp_inner(self, run_id: str) -> dict:
        import json as _json
        record = audit.get_run(run_id)
        plan = ResearchPlan.model_validate(_json.loads(record["plan_json"]))
        # BACKTESTING（确定性引擎，实验≤3 组，真实执行）
        audit.update_run(run_id, status="BACKTESTING", stage="BACKTESTING")
        experiments = []
        used = 0
        for strategy_id in plan.strategy_candidates[:MAX_EXPERIMENTS]:
            used += 1
            bt = self._run_experiment(strategy_id, plan)
            experiments.append(bt)
        audit.update_run(run_id, status="HARD_RISK_CHECK", stage="HARD_RISK_CHECK",
                         experiments_used=used, backtest_id=experiments[0]["backtest_id"] if experiments else None)
        best = self._pick_best(experiments)
        risk = self._hard_risk_check(best)
        if risk["verdict"] == "VETO":
            audit.update_run(run_id, status="FAILED", error_reason="hard VETO after experiments")
            return {"research_run_id": run_id, "status": "VETOED", "risk_check": risk}
        # RESEARCH（Qwen）
        audit.update_run(run_id, status="RESEARCH", stage="RESEARCH")
        self._charge(run_id, MAX_MODEL_CALLS["EXPERIMENTAL"])
        facts = self._facts(best, best["backtest_id"]) + f"\n实验组数: {used}"
        researcher = await call_with_audit(
            run_id, "RESEARCHER", RESEARCHER_SYSTEM,
            f"研究目标：{plan.research_goal}\n已知事实（真实计算）：\n{facts}\n请输出结构化研究摘要。",
            temperature=0.2, max_tokens=2400, backtest_id=best["backtest_id"])
        # REVIEW（Pro）
        audit.update_run(run_id, status="REVIEW", stage="REVIEW",
                         model_calls_used=self._calls.get(run_id, 0))
        self._charge(run_id, MAX_MODEL_CALLS["EXPERIMENTAL"])
        reviewer = await call_with_audit(
            run_id, "REVIEWER", REVIEWER_SYSTEM,
            f"原始证据：\n{facts}\n\nQwen 结论：\n{researcher['content'][:3000]}\n\n"
            f"确定性风控：{risk['verdict']}\n请独立审阅。",
            temperature=0.1, max_tokens=1400, backtest_id=best["backtest_id"])
        audit.update_run(run_id, status="VERIFICATION", stage="VERIFICATION")
        verification = self._numeric_verification(researcher["content"], best.get("metrics", {}))
        if not verification["passed"]:
            audit.update_run(run_id, status="FAILED",
                             error_reason=f"numeric verification failed: {verification['unsupported'][:2]}",
                             model_calls_used=self._calls.get(run_id, 0))
            return {"research_run_id": run_id, "status": "VERIFICATION_FAILED",
                    "numeric_verification": verification,
                    "note": "AI 结论含无法核验的数字；按规则终止，不生成决策包"}
        packet = self._build_packet(run_id, {"result": best}, risk, researcher, reviewer)
        audit.update_run(run_id, status="COMPLETED", stage="COMPLETED",
                         decision_packet_id=packet["packet_id"],
                         model_calls_used=self._calls.get(run_id, 0))
        return {"research_run_id": run_id, "status": "COMPLETED", "mode": "EXPERIMENTAL",
                "plan": plan.model_dump(), "experiments": [
                    {"strategy_id": e["strategy"]["strategy_id"], "backtest_id": e["backtest_id"],
                     "metrics": e.get("metrics")} for e in experiments],
                "risk_check": risk, "research": self._json_or_none(researcher["content"]),
                "review": self._json_or_none(reviewer["content"]),
                "numeric_verification": verification,
                "decision_packet_id": packet["packet_id"],
                "model_calls": audit.calls_for_run(run_id)}

    # ---------- 确定性环节 ----------
    def _load_backtest(self, backtest_id: str) -> dict | None:
        from trading.wealth_lab.repository import WealthLabRepository
        return WealthLabRepository().load_backtest(backtest_id)

    def _run_experiment(self, strategy_id: str, plan: ResearchPlan) -> dict:
        from trading.wealth_lab.routes_v3 import UniverseBacktestBody, v3_universe_backtest
        response = v3_universe_backtest(strategy_id, UniverseBacktestBody(
            universe_id=plan.universe_id, start_date=plan.start_date,
            end_date=plan.end_date))
        payload = response["data"]
        if payload.get("data_mode") == "UNAVAILABLE":
            raise RuntimeError(f"experiment rejected: {payload.get('reason')}")
        return payload

    def _pick_best(self, experiments: list) -> dict:
        return max(experiments, key=lambda e: e.get("metrics", {}).get("sharpe_ratio", -9))

    def _hard_risk_check(self, result: dict) -> dict:
        from trading.wealth_lab.risk import WealthRiskEngine
        from trading.wealth_lab.universe import assets_report
        # 兼容 v3(rebalance_records) / v2(weight_history) 两种回测结构
        records = result.get("rebalance_records")
        if records:
            weights = dict(records[-1].get("target_weights", {}))
        else:
            snapshots = result.get("weight_history") or []
            weights = dict(snapshots[-1].get("weights", {})) if snapshots else {}
        # WealthRiskEngine 期望 coverage: {symbol: {rows, last}}
        coverage = {
            a["symbol"]: {"rows": a.get("valid_days", 0), "last": a.get("data_last")}
            for a in assets_report(list(weights))
        }
        return WealthRiskEngine().check(weights, coverage=coverage,
                                        metrics=result.get("metrics", {}))

    def _facts(self, result: dict, backtest_id: str) -> str:
        import json as _json
        records = result.get("rebalance_records") or []
        last = records[-1] if records else {}
        # 瘦身：仅保留 Qwen 研究所需关键字段（压缩重复证据，降低调用耗时）
        return _json.dumps({
            "backtest_id": backtest_id,
            "universe_id": result.get("universe_id"),
            "strategy": (result.get("strategy") or {}).get("strategy_id"),
            "window": {"start": (result.get("time_semantics") or {}).get("effective_start_date"),
                       "end": (result.get("time_semantics") or {}).get("end_date"),
                       "days": (result.get("time_semantics") or {}).get("actual_trading_days")},
            "metrics": result.get("metrics"),
            "last_rebalance": {"date": last.get("decision_date"),
                               "weights": last.get("target_weights"),
                               "cash_weight": last.get("cash_weight"),
                               "eligible_count": len(last.get("eligible_assets", []))},
            "pit_rule": "每个调仓决策时点重评资格；幸存者偏差未消除",
            "cost_model": "单边5bps，Σ|Δw|×bps/1e4，现金零收益，rf=2%",
        }, ensure_ascii=False, indent=1)[:5000]

    def _numeric_verification(self, text: str, metrics: dict) -> dict:
        """确定性核验：百分比口径 + 小数口径（AI 常以 0.4595 形式复述指标值）。

        值域包含回测指标与系统级已知配置事实（无风险利率 2%、单边成本 5bps、
        现金收益 0），引用配置假设不属于编造。
        """
        import re
        from trading.wealth_lab.config import RF_ANNUAL
        from trading.wealth_lab.risk import WealthRiskPolicy
        policy = WealthRiskPolicy()
        known = {**metrics,
                 "risk_free_rate": RF_ANNUAL, "cost_per_side": 0.0005, "cash_return": 0.0,
                 "equity_exposure_limit": policy.max_equity_exposure,
                 "max_single_asset_weight": policy.max_single_asset_weight,
                 "drawdown_limit": policy.max_portfolio_drawdown,
                 "volatility_limit": policy.max_volatility}
        values = [abs(float(v)) for v in known.values() if isinstance(v, (int, float))]
        sentences = [s for s in re.split(r"[。\n]", text) if "%" in s or
                     any(k in s for k in ("夏普", "Sharpe", "回撤", "年化", "Sortino", "Calmar"))]
        unsupported = []
        for sentence in sentences:
            if self._sentence_supported(sentence, metrics, values):
                continue
            unsupported.append(sentence[:80])
        return {"engine": "deterministic", "checked_sentences": len(sentences),
                "unsupported": unsupported,
                "note": "AI SUPPORTED 不替代本核验；核验失败即终止，不生成研究结论",
                "passed": not unsupported}

    def _sentence_supported(self, sentence: str, metrics: dict, values: list) -> bool:
        from trading.wealth_lab.risk.engine import _claim_supported
        from trading.wealth_lab.config import RF_ANNUAL
        import re as _re
        from trading.wealth_lab.risk import WealthRiskPolicy
        policy = WealthRiskPolicy()
        known = {**metrics, "risk_free_rate": RF_ANNUAL,
                 "cost_per_side": 0.0005, "cash_return": 0.0,
                 "equity_exposure_limit": policy.max_equity_exposure,
                 "max_single_asset_weight": policy.max_single_asset_weight,
                 "drawdown_limit": policy.max_portfolio_drawdown,
                 "volatility_limit": policy.max_volatility}
        if _claim_supported(sentence, known):
            return True
        # 小数口径：句中小数形式数字与任一指标值近似（相对误差 1e-3）
        for token in _re.findall(r"-?\d+\.\d{3,}", sentence):
            number = abs(float(token))
            if any(abs(number - v) <= max(1e-3, v * 1e-3) for v in values):
                return True
        return False

    def _build_packet(self, run_id, record, risk, researcher, reviewer) -> dict:
        from trading.wealth_lab.decision_packets import WealthDecisionPacketStore
        result = record["result"]
        weights = {}
        for rec in result.get("rebalance_records", [])[-1:]:
            weights = rec.get("target_weights", {})
        import json as _json
        research_payload = self._json_or_none(researcher["content"]) or {}
        review_payload = self._json_or_none(reviewer["content"]) or {}
        return WealthDecisionPacketStore().create(
            {"portfolio_id": f"research-{run_id}", "strategy_id": result.get("strategy", {}).get("strategy_id", "unknown"),
             "strategy_version": result.get("strategy", {}).get("strategy_version", "1.0"),
             "weights": weights or {}, "backtest_id": result.get("backtest_id"),
             "cost_assumptions": None},
            metrics=result.get("metrics", {}), risk_check=risk,
            ai_research={**research_payload, "model": "Qwen 3.8 Max 0902"},
            ai_risk_review={**review_payload, "model": "DeepSeek V4 Pro"},
            data_sources=["xiaodefa-tushare/fund_daily", "xiaodefa-tushare/fund_adj"],
            backtest_window=result.get("time_semantics") or {},
        )

    def _json_or_none(self, text: str):
        from trading.wealth_lab.research.orchestrator import _parse_json
        try:
            return _parse_json(text)
        except Exception:
            return None
