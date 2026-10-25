"""Wealth Lab v3.3 三模型研究系统测试（无网络/无付费调用）。"""
from __future__ import annotations

from datetime import date

import pytest

from trading.wealth_lab.research.audit import create_run, get_run, log_call, calls_for_run
from trading.wealth_lab.research.orchestrator import ResearchPlan, validate_plan
from trading.wealth_lab.research.pipeline import BudgetExceeded, ResearchOrchestrator
from trading.wealth_lab.research.roles import build_role_specs


def test_role_mapping_uses_configured_models_only():
    """验证角色使用 router_settings 配置值（环境无关：有 .env 和无 .env 都正确）。"""
    from router.config import router_settings
    specs = build_role_specs()
    # RESEARCHER/REVIEWER 的 model_id 必须来自配置（而非硬编码特定版本号）
    assert specs["RESEARCHER"].actual_model_id == router_settings.qwen_model
    assert specs["REVIEWER"].actual_model_id == router_settings.deepseek_model
    assert specs["RESEARCHER"].provider == "qwen"
    assert specs["REVIEWER"].provider == "deepseek"
    # PLANNER 只在显式配置时可用（禁止猜测 model_id）
    planner = specs["PLANNER"]
    assert planner.actual_model_id in (None, "deepseek-flash")
    if planner.actual_model_id is None:
        assert planner.available is False


def test_plan_validation_whitelist_and_truncation():
    plan = ResearchPlan(
        research_goal="测试目标", universe_id="core4-v1",
        strategy_candidates=["S2_cross_momentum", "S3_dual_momentum",
                             "S4_inverse_volatility", "S8_hrp", "S0_fixed_weight"],
        start_date=date(2024, 1, 1), end_date=date(2026, 10, 23))
    errors = validate_plan(plan, {"core4-v1": {}},
                           {"S2_cross_momentum", "S3_dual_momentum", "S4_inverse_volatility",
                            "S8_hrp", "S0_fixed_weight"})
    assert errors == []  # 全部合法，仅数量超限
    assert len(plan.strategy_candidates) == 3  # 确定性截断
    bad = ResearchPlan.model_validate({
        "research_goal": "x" * 10, "universe_id": "nonexistent",
        "strategy_candidates": ["S99"], "start_date": "2024-01-01", "end_date": "2024-12-31"})
    errors = validate_plan(bad, {"core4-v1": {}}, {"S0_fixed_weight"})
    assert any("universe_id" in e for e in errors)
    assert any("S0-S8" in e for e in errors)
    late = ResearchPlan.model_validate({
        "research_goal": "x" * 10, "universe_id": "core4-v1",
        "strategy_candidates": ["S0_fixed_weight"],
        "start_date": "2024-01-01", "end_date": "2026-12-31"})
    assert any("2026-10-23" in e for e in validate_plan(late, {"core4-v1": {}}, {"S0_fixed_weight"}))


def test_budget_enforcement():
    orch = ResearchOrchestrator()
    with pytest.raises(BudgetExceeded):
        for _ in range(3):
            orch._charge("wlrun_test_budget", 2)


def test_numeric_verification_decimal_and_percent():
    orch = ResearchOrchestrator()
    metrics = {"total_return": 0.4594701767, "annual_return": 0.061873,
               "sharpe_ratio": 0.913, "max_drawdown": -0.053351,
               "annual_volatility": 0.045846}
    assert orch._numeric_verification("S4策略总收益0.4594701767380096", metrics)["passed"]
    assert orch._numeric_verification("总收益 45.95%，夏普 0.913，最大回撤 -5.34%", metrics)["passed"]
    assert orch._numeric_verification("年化波动0.045846属于低波动", metrics)["passed"]
    result = orch._numeric_verification("总收益高达 78.3%，夏普 2.5", metrics)
    assert not result["passed"] and len(result["unsupported"]) >= 1
    # 编造的小数（不在指标中）被拦截
    assert not orch._numeric_verification("最大回撤仅0.9987", metrics)["passed"]


def test_audit_tables_full_fields(tmp_path, monkeypatch):
    import trading.wealth_lab.research.audit as audit_mod
    monkeypatch.setattr(audit_mod, "WEALTH_LAB_DB_PATH", tmp_path / "audit.duckdb")
    from datetime import datetime, timedelta, timezone
    TZ = timezone(timedelta(hours=8))
    create_run("wlrun_audit_test", "STABLE", "goal", 2)
    call_id = log_call(
        "wlrun_audit_test", "RESEARCHER", "Qwen 3.8 Max 0902", "qwen3.8-max-0902",
        "qwen", "wealth-researcher-qwen-v1", datetime.now(TZ),
        latency_ms=1234, input_tokens=800, output_tokens=500, status="OK",
        associated_backtest_id="wlbt_x", evidence_ids=["wlbt_x"])
    calls = calls_for_run("wlrun_audit_test")
    assert len(calls) == 1
    c = calls[0]
    for field in ("call_id", "research_run_id", "agent_role", "display_model_name",
                  "actual_model_id", "provider", "prompt_version", "started_at",
                  "finished_at", "latency_ms", "input_tokens", "output_tokens",
                  "status", "associated_backtest_id", "evidence_ids"):
        assert field in c
    assert c["evidence_ids"] == ["wlbt_x"] and call_id == c["call_id"]
    record = get_run("wlrun_audit_test")
    assert record["mode"] == "STABLE" and record["model_call_budget"] == 2


def test_model_unavailable_records_and_raises(tmp_path, monkeypatch):
    """PLANNER 未配置时：审计记录 MODEL_UNAVAILABLE 且抛错（不冒充其他模型）。"""
    import trading.wealth_lab.research.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "ROLE_SPECS", {
        **build_role_specs(),
        "PLANNER": build_role_specs()["PLANNER"].__class__(
            role="PLANNER", display_model_name="DeepSeek V4 Flash", provider="deepseek",
            actual_model_id=None, config_source="test", available=False, note="not configured"),
    })
    import asyncio
    with pytest.raises(RuntimeError, match="MODEL_UNAVAILABLE"):
        asyncio.run(orch_mod.call_with_audit("wlrun_x", "PLANNER", "s", "p",
                                             temperature=0.1, max_tokens=10))
