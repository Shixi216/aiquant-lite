from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from research.sentiment.v3.incremental_evaluation import (
    ALGORITHM_VERSION,
    BASELINE_VERSION,
    DEFAULT_OUTPUT_DIRECTORY,
    ENVIRONMENT_VERSION,
    EnvironmentLabel,
    IncrementalRunResult,
    ORTHOGONAL_VERSION,
    classify_observed_environments,
    deterministic_hash,
    equal_rank_augmentation,
    equal_rank_baseline,
    two_control_residual,
)


def test_baseline_uses_transparent_equal_rank_formula() -> None:
    assert equal_rank_baseline(0.9, 0.2, 0.6) == pytest.approx(
        (0.9 + 0.8 + 0.6) / 3
    )


def test_augmentation_is_equal_rank_average() -> None:
    assert equal_rank_augmentation(0.8, 0.4) == pytest.approx(0.6)


def test_two_control_residual_removes_known_linear_exposure() -> None:
    # y=2*x+3*z; supplied cross-moments imply beta_x=2 and beta_z=3.
    residual = two_control_residual(
        sentiment_centered=8.0,
        liquidity_centered=1.0,
        volatility_centered=2.0,
        var_liquidity=1.0,
        var_volatility=1.0,
        cov_liquidity_volatility=0.0,
        cov_sentiment_liquidity=2.0,
        cov_sentiment_volatility=3.0,
    )
    assert residual == pytest.approx(0.0)


def test_singular_controls_produce_missing_not_zero() -> None:
    assert two_control_residual(
        sentiment_centered=1.0,
        liquidity_centered=1.0,
        volatility_centered=1.0,
        var_liquidity=1.0,
        var_volatility=1.0,
        cov_liquidity_volatility=1.0,
        cov_sentiment_liquidity=1.0,
        cov_sentiment_volatility=1.0,
    ) is None


@pytest.mark.parametrize(
    ("market_return", "turnover_change", "expected"),
    [
        (0.01, 0.2, (EnvironmentLabel.MARKET_UP, EnvironmentLabel.HIGH_TURNOVER)),
        (-0.01, -0.2, (EnvironmentLabel.MARKET_DOWN, EnvironmentLabel.LOW_TURNOVER)),
        (0.0, None, (EnvironmentLabel.MARKET_FLAT, EnvironmentLabel.TURNOVER_UNAVAILABLE)),
    ],
)
def test_environment_labels_are_observed_not_predictive(
    market_return: float,
    turnover_change: float | None,
    expected: tuple[EnvironmentLabel, EnvironmentLabel],
) -> None:
    assert classify_observed_environments(market_return, turnover_change) == expected


def test_hash_is_deterministic_and_order_independent() -> None:
    left = deterministic_hash({"b": [2, 1], "a": 1.123456789})
    right = deterministic_hash({"a": 1.123456789, "b": [2, 1]})
    assert left == right


def test_versions_are_frozen_and_explicit() -> None:
    assert ALGORITHM_VERSION.endswith("1.0.0")
    assert BASELINE_VERSION == "BASELINE_RANK_MODEL_V1"
    assert ORTHOGONAL_VERSION.endswith("_V1")
    assert ENVIRONMENT_VERSION.endswith("_V1")


def test_outputs_use_independent_evaluation_directory() -> None:
    assert DEFAULT_OUTPUT_DIRECTORY == Path("evaluation/sentiment_v3_s3c1")


def test_result_contract_cannot_create_trades() -> None:
    fields = IncrementalRunResult.__dataclass_fields__
    assert fields["formal_weight"].default == 0
    assert fields["external_api_calls"].default == 0
    assert fields["llm_calls"].default == 0
    assert fields["creates_trade"].default is False
    assert fields["alpha_conclusion"].default == "NONE"
    assert "action" not in fields
    assert "order" not in fields
    assert "position" not in fields


def test_pipeline_source_preserves_lineage_and_pit_cutoff() -> None:
    source = Path("research/sentiment/v3/incremental_evaluation.py").read_text(
        encoding="utf-8"
    )
    assert "source_packet_id" in source
    assert "feature_lineage_hash" in source
    assert "cutoff" in source
    assert "forward_return_" in source
    assert "record_source!='REPLAY'" in source


def test_pipeline_does_not_import_production_or_execution_paths() -> None:
    import research.sentiment.v3.incremental_evaluation as module

    source = inspect.getsource(module).lower()
    assert "decision_engine" not in source
    assert "scanner" not in source
    assert "tradeplan" not in source
    assert "order_pipeline" not in source
    assert "buy" not in IncrementalRunResult.__dataclass_fields__
    assert "sell" not in IncrementalRunResult.__dataclass_fields__


def test_registry_and_label_definitions_are_not_written() -> None:
    source = Path("research/sentiment/v3/incremental_evaluation.py").read_text(
        encoding="utf-8"
    )
    assert "Feature_Registry" not in source
    assert "Label_Spec" not in source
    assert "UPDATE " not in source.upper()

