from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from research.sentiment.v3.evaluation import (
    CONTROL_FACTORS,
    DEFAULT_OUTPUT_DIR,
    EvaluationRunResult,
    ResearchRegime,
    S3CEvaluationPipeline,
    calculate_ic_ir,
    calculate_max_drawdown,
    calculate_membership_turnover,
    classify_research_regime,
    deterministic_hash,
)


def test_01_deterministic_hash_is_order_independent():
    assert deterministic_hash({"a":1,"b":2}) == deterministic_hash({"b":2,"a":1})


def test_02_ic_ir_handles_zero_variance_without_fabricating_zero():
    assert calculate_ic_ir(0.1,0.2) == pytest.approx(0.5)
    assert calculate_ic_ir(0.1,0.0) is None
    assert calculate_ic_ir(None,0.2) is None


def test_03_max_drawdown_uses_time_ordered_equity_curve():
    result=calculate_max_drawdown([0.10,-0.20,0.05])
    assert result == pytest.approx(-0.20)
    assert calculate_max_drawdown([]) is None


def test_04_turnover_is_membership_change_not_trade_signal():
    assert calculate_membership_turnover({"A","B"},{"A","C"}) == pytest.approx(0.5)
    assert calculate_membership_turnover(set(),{"A"}) is None


def test_05_regime_uses_only_research_diagnostics():
    assert classify_research_regime(
        observations=60,trend_20d=0.02,volatility_20d=0.04,
        prior_volatility_q75=0.03,turnover_change_5d=0.1,
    ) == ResearchRegime.HIGH_VOLATILITY
    assert classify_research_regime(
        observations=60,trend_20d=0.02,volatility_20d=0.01,
        prior_volatility_q75=0.03,turnover_change_5d=0.1,
    ) == ResearchRegime.TREND_UP
    assert classify_research_regime(
        observations=60,trend_20d=-0.02,volatility_20d=0.01,
        prior_volatility_q75=0.03,turnover_change_5d=-0.1,
    ) == ResearchRegime.TREND_DOWN


def test_06_early_history_is_sideway_not_future_filled():
    assert classify_research_regime(
        observations=10,trend_20d=0.1,volatility_20d=0.2,
        prior_volatility_q75=None,turnover_change_5d=1.0,
    ) == ResearchRegime.SIDEWAY


def test_07_factor_availability_keeps_unavailable_controls():
    assert CONTROL_FACTORS == (
        "MOMENTUM","VOLATILITY","SIZE","VALUE","QUALITY","LIQUIDITY"
    )


def test_08_all_default_outputs_are_in_independent_evaluation_directory():
    assert DEFAULT_OUTPUT_DIR == Path("evaluation/sentiment_v3_s3c")
    pipeline=S3CEvaluationPipeline()
    assert pipeline.output_database.parent == DEFAULT_OUTPUT_DIR


def test_09_pipeline_sql_uses_next_open_labels_and_pit_sector_membership():
    source=Path("research/sentiment/v3/evaluation.py").read_text(encoding="utf-8")
    assert "exec_1d__adjusted_return" in source
    assert "data_available_time<=p.cutoff" in source
    assert "historical_industry_memberships" in source
    assert "stock_industry_memberships" not in source


def test_10_evaluation_models_have_no_trading_action_fields():
    result=EvaluationRunResult(
        run_id="r",deterministic_hash="h",algorithm_version="v",
        source_s3c_run_id="s",source_s3c_hash="sh",source_dataset_id="d",
        source_dataset_hash="dh",date_from="2025-01-01",date_to="2025-01-02",
        source_packet_count=1,evaluation_row_count=1,date_count=1,symbol_count=1,
        method_count=3,ic_result_count=1,group_result_count=1,
        available_control_factor_count=3,unavailable_control_factors=("SIZE",),
        regime_source="proxy",regime_quality_flag="flag",
        recommendation="REMAIN_RESEARCH_SHADOW",timings={},output_database_bytes=1,
    )
    fields=set(asdict(result))
    assert not fields & {"action","signal","order","position","trade_plan"}
    assert result.formal_weight == 0 and not result.creates_trade


def test_11_runner_validates_source_times_before_cutoff():
    source=Path("research/sentiment/v3/evaluation.py").read_text(encoding="utf-8")
    for field in (
        "market_available_time","stock_available_time","margin_available_time",
        "dragon_tiger_available_time",
    ):
        assert f"r.{field} > p.cutoff" in source


def test_12_no_production_trading_imports():
    source=Path("research/sentiment/v3/evaluation.py").read_text(encoding="utf-8")
    assert "decision_engine" not in source.lower()
    assert "order_pipeline" not in source.lower()
    assert "canonical_shadow" not in source.lower()
