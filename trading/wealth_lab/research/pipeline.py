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
        """确定性核验（v3.6 终修）：
        1) 指标名绑定+方向+逐数字核验
        2) 零检查不放行（含无句子命中场景）
        3) 配置值不替代缺失的回测指标
        4) 收益类小数用相对容差（绝对 0.05 对小数过宽）
        """
        import re
        from trading.wealth_lab.config import RF_ANNUAL
        from trading.wealth_lab.risk import WealthRiskPolicy
        policy = WealthRiskPolicy()

        # 核心回测指标（必须来自 metrics，配置值不能替代）
        core = {k: v for k, v in metrics.items() if isinstance(v, (int, float))}
        # 配置事实（仅用于独立百分比匹配的兜底，不用于指标名绑定）
        config_facts = {
            "risk_free_rate": RF_ANNUAL, "cost_per_side": 0.0005, "cash_return": 0.0,
            "equity_exposure_limit": policy.max_equity_exposure,
            "max_single_asset_weight": policy.max_single_asset_weight,
            "drawdown_limit": policy.max_portfolio_drawdown,
            "volatility_limit": policy.max_volatility,
        }

        text = self._flatten_json_text(text)
        # 扩大句子筛选：含任何指标关键词或 % 的都入选
        all_keywords = ("夏普", "Sharpe", "回撤", "年化", "Sortino", "Calmar",
                        "总收益", "累计收益", "收益", "波动", "换手", "胜率")
        sentences = [s for s in re.split(r"[。\n]", text) if "%" in s or
                     any(k in s for k in all_keywords)]

        unsupported = []
        total_checked = 0
        for sentence in sentences:
            ok, n = self._sentence_supported_v3(sentence, core, config_facts)
            total_checked += n
            if not ok:
                unsupported.append(sentence[:80])

        # 零检查：有候选句但零数字 → 不通过；无候选句但原文含数字 → 也不通过
        has_numbers_in_text = bool(re.search(r"\d+\.\d+", text))
        if total_checked == 0:
            if sentences or has_numbers_in_text:
                return {"engine": "deterministic", "checked_sentences": len(sentences),
                        "checked_numbers": 0,
                        "unsupported": ["（零数字核验，不视为通过）"],
                        "note": "零检查不放行（含无命中句场景）", "passed": False}

        if not sentences and not has_numbers_in_text:
            return {"engine": "deterministic", "checked_sentences": 0,
                    "checked_numbers": 0, "unsupported": [],
                    "note": "无财务数字文本，无需核验", "passed": True}

        return {"engine": "deterministic", "checked_sentences": len(sentences),
                "checked_numbers": total_checked, "unsupported": unsupported,
                "note": "逐数字+指标名绑定+方向+相对容差核验", "passed": not unsupported}

    def _sentence_supported_v3(self, sentence: str, core: dict, config_facts: dict) -> tuple[bool, int]:
        """v3.6：配置值不替代核心指标；收益小数用相对容差；复合词取最近指标名。"""
        import re as _re
        from trading.wealth_lab.risk.engine import _METRIC_KEYWORDS

        # 收集所有匹配，按数字位置分组——同数字取最近（最晚 start）的关键词
        all_matches = []  # (keyword_start, keyword, key, is_ratio, claimed, is_pct)
        for keyword, (key, is_ratio) in _METRIC_KEYWORDS.items():
            pattern = _re.compile(r"(?<![a-zA-Z_])" + _re.escape(keyword)
                                  + r"[^0-9\-]{0,6}(-?\d+(?:\.\d+)?)(\s*%)?")
            for match in pattern.finditer(sentence.lower()):
                all_matches.append((match.start(), keyword, key, is_ratio,
                                    float(match.group(1)), bool(match.group(2))))
        # 同一数字位置取最近关键词（start 最大）
        best = {}
        for start, keyword, key, is_ratio, claimed, is_pct in all_matches:
            num_key = str(claimed)
            if num_key not in best or start > best[num_key][0]:
                best[num_key] = (start, keyword, key, is_ratio, claimed, is_pct)

        checked = 0
        for _, keyword, key, is_ratio, claimed, is_pct in best.values():
            expected = core.get(key)
            if expected is None:
                checked += 1
                return False, checked  # 指标缺失不能通过
            checked += 1
            # 方向核验
            if key in ("total_return", "annual_return") and expected > 0 and claimed < 0:
                return False, checked
            if key == "max_drawdown" and expected < 0 and claimed > 0 and is_pct:
                return False, checked
            # 数值核验
            if is_pct:
                actual = abs(expected) * 100
                claimed_val = abs(claimed)
            else:
                actual = abs(expected) * (100 if is_ratio and abs(claimed) > 1 else 1)
                claimed_val = abs(claimed)
            # 比率类用绝对容差；收益/回撤类用相对容差
            if key in ("sharpe_ratio", "sortino_ratio", "calmar_ratio",
                       "monthly_win_rate", "avg_turnover_per_rebalance"):
                if abs(claimed_val - actual) > 0.05:
                    return False, checked
            else:
                if abs(claimed_val - actual) > max(actual * 5e-3, 0.005):
                    return False, checked

        # 独立百分比：同时查 core 和 config_facts
        all_known = {**core, **config_facts}
        for val_str in _re.findall(r"(-?\d+(?:\.\d+)?)\s*%", sentence):
            val = float(val_str)
            checked += 1
            normalized = abs(val) / 100
            matched = any(abs(normalized - abs(v)) <= max(5e-4, abs(v) * 5e-3)
                          for v in all_known.values())
            if not matched:
                matched = any(abs(abs(val) - abs(v) * 100) <= max(0.05, abs(v) * 100 * 5e-3)
                              for v in all_known.values())
            if not matched:
                return False, checked

        return True, checked

    def _flatten_json_text(self, text: str) -> str:
        """JSON 输出 -> 拼接字符串值为连贯文本。"""
        from trading.wealth_lab.research.orchestrator import _parse_json

        def walk(node):
            if isinstance(node, str):
                return [node]
            if isinstance(node, dict):
                return [x for v in node.values() for x in walk(v)]
            if isinstance(node, list):
                return [x for v in node for x in walk(v)]
            return []

        try:
            return "。".join(walk(_parse_json(text)))
        except Exception:
            return text

    def _sentence_supported_v2(self, sentence: str, known: dict) -> tuple[bool, int]:
        """收紧版：返回 (通过, 核验数字数)。逐数字独立核验。"""
        import re as _re
        from trading.wealth_lab.risk.engine import _METRIC_KEYWORDS
        checked = 0
        # 1) 指标名绑定核验
        for keyword, (key, is_ratio) in _METRIC_KEYWORDS.items():
            pattern = _re.compile(r"(?<![a-zA-Z_])" + _re.escape(keyword)
                                  + r"[^0-9\-]{0,6}(-?\d+(?:\.\d+)?)(\s*%)?")
            for match in pattern.finditer(sentence.lower()):
                claimed = float(match.group(1))
                is_pct = bool(match.group(2))
                expected = known.get(key)
                if expected is None:
                    continue
                checked += 1
                # 方向核验
                if key in ("total_return", "annual_return") and expected > 0 and claimed < 0:
                    return False, checked
                if key == "max_drawdown" and expected < 0 and claimed > 0 and is_pct:
                    return False, checked
                # 数值核验（双口径）
                if is_pct:
                    actual = abs(expected) * 100
                    claimed_val = abs(claimed)
                else:
                    actual = abs(expected) * (100 if is_ratio and abs(claimed) > 1 else 1)
                    claimed_val = abs(claimed)
                if abs(claimed_val - actual) > max(0.05, actual * 0.005):
                    return False, checked
        # 2) 独立百分比核验
        for val_str in _re.findall(r"(-?\d+(?:\.\d+)?)\s*%", sentence):
            val = float(val_str)
            checked += 1
            normalized = abs(val) / 100
            matched = any(abs(normalized - abs(v)) <= max(5e-4, abs(v) * 5e-3)
                          for v in known.values() if isinstance(v, (int, float)))
            if not matched:
                matched = any(abs(abs(val) - abs(v) * 100) <= max(0.05, abs(v) * 100 * 5e-3)
                              for v in known.values() if isinstance(v, (int, float)))
            if not matched:
                return False, checked
        return True, checked

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
