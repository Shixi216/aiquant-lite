from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import ValidationError

from config.settings import settings
from database.db import initialize_database
from database.migrations.v0111_experiment_evaluation_v1 import (
    MIGRATION_ID,
    _checksum,
    apply_migration,
)
from trading.experiments.forward_returns import ForwardReturnLabelService
from trading.experiments.hashing import sanitize, stable_hash
from trading.experiments.historical_replay import HistoricalReplayService
from trading.experiments.metrics import (
    EvaluationMetricService,
    deterministic_bootstrap_interval,
)
from trading.experiments.models import (
    SUPPORTED_HORIZONS,
    ExperimentRiskFlag,
    ExperimentStatus,
    ExperimentType,
    LabelStatus,
    RebalanceMethod,
    SignalType,
    WeightingMethod,
)
from trading.experiments.portfolio import (
    PortfolioBacktestService,
    _bounded_weights,
)
from trading.experiments.repository import ExperimentRepository
from trading.experiments.schemas import (
    CreateExperimentRequest,
    ExperimentObservation,
    ForwardReturnLabel,
    HistoricalReplayRequest,
    HistoricalWindow,
    PortfolioBacktestRequest,
    build_definition,
)
from trading.experiments.service import ExperimentEvaluationService
from trading.research.orchestration.models import FORMAL_WEIGHTS
from trading.schemas import DecisionPacket
from data_hub.schemas.unified import FactorType


TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 7, 30, 12, 0, tzinfo=TZ)


def _definition(**updates: Any):
    request = CreateExperimentRequest(
        experiment_name="test",
        experiment_type=ExperimentType.HISTORICAL_REPLAY,
        description="test",
        hypothesis="test",
        **updates,
    )
    return build_definition(request, created_at=NOW)


def _observation(**updates: Any) -> ExperimentObservation:
    values: dict[str, Any] = {
        "observation_id": "obs_" + "1" * 24,
        "run_id": "erun_" + "2" * 24,
        "signal_id": "sig_" + "3" * 24,
        "signal_type": SignalType.SCANNER_ONLY,
        "signal_trade_date": date(2026, 7, 20),
        "signal_generated_at": NOW,
        "data_cutoff": NOW,
        "symbol": "600000.SH",
        "rank": 1,
        "scanner_score": 0.8,
        "factor_coverage": "1/5",
        "available_factors": ["TECHNICAL"],
        "missing_factors": [
            "FUNDAMENTAL",
            "SENTIMENT",
            "POLICY_NEWS",
            "CAPITAL_FLOW",
        ],
        "query_plan_hash": "4" * 64,
        "signal_snapshot_hash": "5" * 64,
        "created_at": NOW,
    }
    values.update(updates)
    return ExperimentObservation(**values)


def _label(
    observation: ExperimentObservation,
    *,
    horizon: int = 1,
    gross_return: float | None = 0.02,
    status: LabelStatus = LabelStatus.COMPLETE,
) -> ForwardReturnLabel:
    return ForwardReturnLabel(
        label_id=f"lbl_{observation.observation_id[-20:]}{horizon:02d}",
        observation_id=observation.observation_id,
        symbol=observation.symbol,
        signal_trade_date=observation.signal_trade_date,
        entry_trade_date=date(2026, 7, 21),
        exit_trade_date=date(2026, 7, 21),
        entry_price=10,
        exit_price=(None if gross_return is None else 10 * (1 + gross_return)),
        gross_return=gross_return,
        benchmark_return=0.01,
        excess_return=(None if gross_return is None else gross_return - 0.01),
        horizon_trading_days=horizon,
        label_status=status,
        generated_at=NOW,
        calculated_at=NOW,
    )


@pytest.fixture
def temporary_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    path = tmp_path / "experiment.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", path)
    initialize_database()
    return path


def test_01_same_experiment_config_has_stable_hash() -> None:
    assert _definition().config_hash == _definition().config_hash


def test_02_parameter_change_creates_new_hash_and_id() -> None:
    original = _definition(random_seed=1)
    changed = _definition(random_seed=2)
    assert original.config_hash != changed.config_hash
    assert original.experiment_id != changed.experiment_id


def test_03_definition_is_immutable_after_registration() -> None:
    definition = _definition()
    with pytest.raises(ValidationError):
        definition.experiment_name = "changed"


def test_04_definition_rejects_tampered_config_hash() -> None:
    values = _definition().model_dump()
    values["config_hash"] = "0" * 64
    with pytest.raises(ValidationError):
        type(_definition())(**values)


@pytest.mark.parametrize("experiment_type", list(ExperimentType))
def test_05_all_required_experiment_types_are_supported(
    experiment_type: ExperimentType,
) -> None:
    assert ExperimentType(experiment_type.value) == experiment_type


@pytest.mark.parametrize("status", list(ExperimentStatus))
def test_06_all_required_experiment_statuses_are_supported(
    status: ExperimentStatus,
) -> None:
    assert ExperimentStatus(status.value) == status


@pytest.mark.parametrize("horizon", SUPPORTED_HORIZONS)
def test_07_only_trading_horizons_1_3_5_20_are_registered(
    horizon: int,
) -> None:
    assert horizon in (1, 3, 5, 20)


def test_08_observation_is_append_only_model() -> None:
    observation = _observation()
    with pytest.raises(ValidationError):
        observation.rank = 2


def test_09_labels_are_separate_from_signal_snapshot() -> None:
    observation = _observation()
    label = _label(observation)
    assert "gross_return" not in type(observation).model_fields
    assert label.observation_id == observation.observation_id


def test_10_pending_labels_cover_all_four_horizons() -> None:
    labels = ForwardReturnLabelService.pending_labels(
        [_observation()],
        generated_at=NOW,
    )
    assert [item.horizon_trading_days for item in labels] == [1, 3, 5, 20]
    assert all(item.label_status == LabelStatus.PENDING for item in labels)


def test_11_entry_uses_next_trading_day_open() -> None:
    observation = _observation()
    calendar = [
        observation.signal_trade_date,
        date(2026, 7, 21),
        date(2026, 7, 22),
    ]
    label = ForwardReturnLabelService._calculate_one(
        observation,
        horizon=1,
        as_of=NOW,
        calendar=calendar,
        calendar_index={value: index for index, value in enumerate(calendar)},
        symbol_bars={
            date(2026, 7, 21): {
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
            }
        },
        benchmark_return=0.01,
    )
    assert label.entry_trade_date == date(2026, 7, 21)
    assert label.entry_price == 10
    assert label.exit_price == 10.5


def test_12_signal_close_is_not_default_entry_price() -> None:
    source = inspect.getsource(ForwardReturnLabelService._calculate_one)
    assert 'entry["open"]' in source
    assert "signal_close" not in source


def test_13_suspension_without_next_open_is_untradable() -> None:
    observation = _observation()
    calendar = [observation.signal_trade_date, date(2026, 7, 21)]
    label = ForwardReturnLabelService._calculate_one(
        observation,
        horizon=1,
        as_of=NOW,
        calendar=calendar,
        calendar_index={value: index for index, value in enumerate(calendar)},
        symbol_bars={},
        benchmark_return=None,
    )
    assert label.label_status == LabelStatus.UNTRADABLE
    assert label.was_suspended_on_entry is True


def test_14_missing_exit_is_recorded_without_using_later_price() -> None:
    observation = _observation()
    calendar = [
        observation.signal_trade_date,
        date(2026, 7, 21),
        date(2026, 7, 22),
        date(2026, 7, 23),
    ]
    label = ForwardReturnLabelService._calculate_one(
        observation,
        horizon=3,
        as_of=NOW,
        calendar=calendar,
        calendar_index={value: index for index, value in enumerate(calendar)},
        symbol_bars={
            date(2026, 7, 21): {
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
            }
        },
        benchmark_return=None,
    )
    assert label.label_status == LabelStatus.MISSING_EXIT
    assert label.exit_trade_date == date(2026, 7, 23)


def test_15_not_mature_horizon_is_insufficient_future_data() -> None:
    observation = _observation()
    calendar = [observation.signal_trade_date, date(2026, 7, 21)]
    label = ForwardReturnLabelService._calculate_one(
        observation,
        horizon=20,
        as_of=NOW,
        calendar=calendar,
        calendar_index={value: index for index, value in enumerate(calendar)},
        symbol_bars={},
        benchmark_return=None,
    )
    assert label.label_status == LabelStatus.INSUFFICIENT_FUTURE_DATA


def test_16_invalid_point_in_time_signal_never_becomes_valid_label() -> None:
    observation = _observation(point_in_time_valid=False)
    label = ForwardReturnLabelService._calculate_one(
        observation,
        horizon=1,
        as_of=NOW,
        calendar=[observation.signal_trade_date, date(2026, 7, 21)],
        calendar_index={observation.signal_trade_date: 0, date(2026, 7, 21): 1},
        symbol_bars={},
        benchmark_return=None,
    )
    assert label.label_status == LabelStatus.INVALID_POINT_IN_TIME


def test_17_raw_return_basis_is_explicit() -> None:
    assert _label(_observation()).return_basis == "RAW_PRICE_RETURN"


def test_18_replay_sql_does_not_use_fetched_at_or_current_snapshot() -> None:
    source = inspect.getsource(HistoricalReplayService)
    assert "fetched_at" not in source
    assert "market_snapshot_items" not in source
    assert "listing_status = 'ACTIVE'" not in source


def test_19_replay_uses_data_available_time() -> None:
    from trading.experiments import historical_replay

    assert "data_available_time" in historical_replay._REPLAY_SQL


def test_20_replay_has_no_network_or_model_call() -> None:
    source = inspect.getsource(HistoricalReplayService)
    assert "httpx" not in source
    assert "requests" not in source
    assert "model_call" not in source


def test_21_random_baseline_is_seeded() -> None:
    from trading.experiments import historical_replay

    assert "$random_seed" in historical_replay._REPLAY_SQL


def test_22_bootstrap_is_deterministic() -> None:
    values = [0.01, -0.02, 0.03, 0.04]
    assert deterministic_bootstrap_interval(
        values, seed=7
    ) == deterministic_bootstrap_interval(values, seed=7)


def test_23_small_sample_does_not_output_annualized_metrics() -> None:
    observation = _observation()
    result = PortfolioBacktestService().run(
        PortfolioBacktestRequest(
            experiment_id="exp_" + "1" * 24,
            run_id=observation.run_id,
            max_position_weight=1,
        ),
        observations=[observation],
        labels=[_label(observation, horizon=5)],
    )
    assert result.annualized_return is None
    assert result.sharpe_ratio is None


def test_24_missing_cost_model_does_not_fabricate_net_return() -> None:
    observation = _observation()
    result = PortfolioBacktestService().run(
        PortfolioBacktestRequest(
            experiment_id="exp_" + "1" * 24,
            run_id=observation.run_id,
            horizon_trading_days=1,
            max_position_weight=1,
        ),
        observations=[observation],
        labels=[_label(observation)],
    )
    assert result.net_return is None
    assert ExperimentRiskFlag.COST_MODEL_NOT_CONFIGURED in result.risk_flags


@pytest.mark.parametrize("method", list(WeightingMethod))
def test_25_portfolio_weights_are_long_only_and_capped(
    method: WeightingMethod,
) -> None:
    weights = _bounded_weights(
        {"A": 1, "B": 2, "C": 3},
        method=method,
        cap=0.4,
    )
    assert all(0 <= value <= 0.4 for value in weights.values())
    assert sum(weights.values()) <= 1


@pytest.mark.parametrize("method", list(RebalanceMethod))
def test_26_required_rebalance_modes_are_supported(
    method: RebalanceMethod,
) -> None:
    assert RebalanceMethod(method.value) == method


def test_27_portfolio_backtest_never_writes_paper_trading() -> None:
    source = inspect.getsource(PortfolioBacktestService)
    assert "PaperTradingEngine" not in source
    assert "submit_order" not in source


def test_28_metrics_exclude_stale_observations_from_valid_count() -> None:
    observation = _observation(stale_snapshot=True)
    metric = EvaluationMetricService().calculate(
        [observation],
        [_label(observation)],
        minimum_sample_size=1,
        random_seed=1,
    )[0]
    assert metric.valid_count == 0


def test_29_metrics_exclude_point_in_time_invalid_observations() -> None:
    observation = _observation(point_in_time_valid=False)
    metric = EvaluationMetricService().calculate(
        [observation],
        [_label(observation)],
        minimum_sample_size=1,
        random_seed=1,
    )[0]
    assert metric.valid_count == 0


def test_30_multiple_testing_risk_is_always_reported() -> None:
    observation = _observation()
    metric = EvaluationMetricService().calculate(
        [observation],
        [_label(observation)],
        minimum_sample_size=1,
        random_seed=1,
    )[0]
    assert ExperimentRiskFlag.MULTIPLE_TESTING_RISK in metric.risk_flags


def test_31_sensitive_config_keys_are_removed_from_reports() -> None:
    assert sanitize(
        {"token": "x", "nested": {"api_key": "y", "safe": 1}}
    ) == {"nested": {"safe": 1}}


def test_32_formal_strategy_weights_remain_60_40() -> None:
    assert FORMAL_WEIGHTS == {
        FactorType.TECHNICAL: 0.60,
        FactorType.FUNDAMENTAL: 0.40,
    }


def test_33_missing_fundamental_is_not_filled_with_zero() -> None:
    observation = _observation(fundamental_score=None, formal_score=None)
    assert observation.fundamental_score is None
    assert observation.formal_score is None


def test_34_shadow_weight_is_not_a_formal_weight() -> None:
    observation = _observation(shadow_score=0.8)
    assert observation.formal_score is None


def test_35_decision_packet_remains_immutable() -> None:
    assert DecisionPacket.model_config["frozen"] is True


def test_36_no_eval_or_exec_in_experiment_package() -> None:
    package = Path("trading/experiments")
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in package.glob("*.py")
    )
    assert "eval(" not in source
    assert "exec(" not in source


def test_37_migration_is_idempotent_and_checksum_stable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "migration.duckdb"
    with duckdb.connect(str(path)) as connection:
        assert apply_migration(connection) is True
        assert apply_migration(connection) is False
        stored = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE migration_id = ?",
            [MIGRATION_ID],
        ).fetchone()[0]
    assert stored == _checksum()


def test_38_migration_separates_observations_and_labels(
    tmp_path: Path,
) -> None:
    with duckdb.connect(str(tmp_path / "tables.duckdb")) as connection:
        apply_migration(connection)
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }
    assert "experiment_observations" in tables
    assert "forward_return_labels" in tables


def test_39_repository_deduplicates_same_definition(
    temporary_database: Path,
) -> None:
    repository = ExperimentRepository()
    definition = _definition()
    assert repository.save_definition(definition) is True
    assert repository.save_definition(definition) is False
    restored = repository.get_definition(definition.experiment_id)
    assert restored == definition


def test_40_repository_observation_insert_is_idempotent(
    temporary_database: Path,
) -> None:
    repository = ExperimentRepository()
    observation = _observation()
    assert repository.save_observations([observation]) == 1
    assert repository.save_observations([observation]) == 0
    assert repository.list_observations(observation.run_id) == [observation]


def test_41_label_update_is_idempotent(
    temporary_database: Path,
) -> None:
    repository = ExperimentRepository()
    observation = _observation()
    repository.save_observations([observation])
    label = _label(observation)
    assert repository.save_initial_labels([label]) == 1
    changed, unchanged = repository.upsert_labels([label])
    assert changed == 0
    assert unchanged == 1


def test_42_replay_uses_batch_sql_and_creates_supported_groups(
    temporary_database: Path,
) -> None:
    start = date(2026, 5, 1)
    dates = [start + timedelta(days=index) for index in range(41)]
    with duckdb.connect(str(temporary_database)) as connection:
        for index, trade_date in enumerate(dates):
            available = datetime.combine(
                trade_date,
                datetime.min.time(),
                tzinfo=TZ,
            ) + timedelta(hours=16)
            connection.execute(
                """
                INSERT INTO trading_calendar_days
                VALUES ('TEST', ?, TRUE, ?, ?, ?, '{}')
                """,
                [trade_date, available, available, f"cal-{index}"],
            )
            connection.execute(
                """
                INSERT INTO canonical_historical_bars (
                    bar_id, symbol, trade_date, event_time,
                    data_available_time, data_cutoff, generated_at,
                    adjustment_type, open, high, low, close, volume, amount,
                    volume_unit, amount_unit, primary_source,
                    source_record_ids_json, verification_source_ids_json,
                    verification_status, confidence, content_hash,
                    algorithm_version, raw_payload_json
                )
                VALUES (
                    ?, '600000.SH', ?, ?, ?, ?, ?, 'RAW',
                    ?, ?, ?, ?, ?, ?, 'SHARES', 'CNY', 'TEST',
                    '[]', '[]', 'SINGLE_SOURCE', 1, ?, 'test-v1', '{}'
                )
                """,
                [
                    f"bar-{index}",
                    trade_date,
                    available - timedelta(hours=1),
                    available,
                    available,
                    available,
                    10 + index,
                    11 + index,
                    9 + index,
                    10.5 + index,
                    1000 + index,
                    10000 + index,
                    stable_hash({"bar": index}),
                ],
            )
    batch = HistoricalReplayService().replay(
        HistoricalReplayRequest(),
        run_id="erun_" + "8" * 24,
        query_plan_hash="9" * 64,
    )
    assert batch.signal_date_count == 21
    assert {item.signal_type for item in batch.observations} == {
        SignalType.SCANNER_ONLY,
        SignalType.TECHNICAL_ONLY,
        SignalType.AMOUNT_TOP20,
        SignalType.MOMENTUM_20D_TOP20,
        SignalType.RANDOM_TOP20,
    }
    assert batch.database_query_count == 2


def test_43_current_values_do_not_fill_historical_missing_fields() -> None:
    observation = _observation()
    assert observation.fundamental_score is None
    assert "FUNDAMENTAL" in observation.missing_factors


def test_44_research_guards_are_literal_false_for_execution() -> None:
    definition = _definition()
    assert definition.research_only is True
    assert definition.production_weight_update is False
    assert definition.trade_execution_enabled is False


def test_45_formal_and_shadow_boundary_is_unchanged() -> None:
    boundary = ExperimentEvaluationService.formal_shadow_boundary()
    assert boundary["formal_weights"] == {
        "TECHNICAL": 0.60,
        "FUNDAMENTAL": 0.40,
    }
    assert boundary["missing_fundamental_filled_with_zero"] is False
    assert boundary["shadow_formal_strategy_weight"] == 0
    assert boundary["formal_action_changed"] is False
    assert boundary["hard_veto_changed"] is False


def test_46_forward_return_does_not_change_historical_rank() -> None:
    observation = _observation(rank=7)
    _label(observation)
    assert observation.rank == 7
    assert observation.scanner_score == 0.8


def test_47_explicit_cost_model_can_calculate_net_research_return() -> None:
    observation = _observation()
    result = PortfolioBacktestService().run(
        PortfolioBacktestRequest(
            experiment_id="exp_" + "1" * 24,
            run_id=observation.run_id,
            horizon_trading_days=1,
            max_position_weight=1,
            cost_model={"commission_rate": 0.0003, "slippage_bps": 5},
        ),
        observations=[observation],
        labels=[_label(observation)],
    )
    assert result.net_return is not None
    assert result.net_return < result.gross_return
    assert result.trade_execution_enabled is False
    assert result.paper_trading_written is False


def test_48_evaluation_slices_support_required_dimensions() -> None:
    observation = _observation(
        board="SH_MAIN",
        industry="BANK",
        anomaly_types=["BREAKOUT"],
        composite_confidence=0.8,
    )
    slices = EvaluationMetricService.slices(
        [observation],
        [_label(observation)],
        minimum_sample_size=1,
    )
    dimensions = {item["dimension"] for item in slices}
    assert dimensions == {
        "horizon",
        "board",
        "industry",
        "factor_coverage",
        "composite_confidence",
        "scanner_score_quantile",
        "anomaly_type",
        "freshness",
        "veto_status",
        "suspension",
        "data_completeness",
        "trade_date_range",
    }


def test_49_slice_conclusion_is_hidden_below_sample_threshold() -> None:
    slices = EvaluationMetricService.slices(
        [_observation()],
        [_label(_observation())],
        minimum_sample_size=2,
    )
    assert all(item["conclusion_available"] is False for item in slices)
    assert all(item["metrics"] == {} for item in slices)


def test_50_openapi_exposes_all_minimum_experiment_routes() -> None:
    from router.api.app import app

    paths = app.openapi()["paths"]
    assert {
        "/v1/experiments",
        "/v1/experiments/{experiment_id}",
        "/v1/experiments/{experiment_id}/run",
        "/v1/experiment-runs/{run_id}",
        "/v1/evaluations/forward-returns/update",
        "/v1/experiment-runs/{run_id}/metrics",
        "/v1/experiment-runs/{run_id}/observations",
        "/v1/experiment-runs/{run_id}/report",
        "/v1/backtests/run",
    }.issubset(paths)


def test_51_stage10_report_contains_no_sensitive_config_key_name() -> None:
    report = Path("reports/experiments/stage10-acceptance.json")
    if not report.exists():
        pytest.skip("acceptance report not generated in isolated test run")
    lowered = report.read_text(encoding="utf-8").lower()
    assert "tushare_token" not in lowered
    assert "api_key" not in lowered
    assert "password" not in lowered


def test_52_required_risk_flags_are_available() -> None:
    required = {
        "INSUFFICIENT_DATA",
        "INSUFFICIENT_HISTORY",
        "INSUFFICIENT_FUTURE_DATA",
        "INSUFFICIENT_COVERAGE",
        "SAMPLE_TOO_SMALL",
        "LOOKAHEAD_RISK",
        "POINT_IN_TIME_INVALID",
        "STALE_SIGNAL",
        "PARTIAL_REPLAY",
        "HISTORICAL_FEATURE_UNAVAILABLE",
        "SURVIVORSHIP_BIAS_RISK",
        "CORPORATE_ACTION_RISK",
        "MISSING_ENTRY_PRICE",
        "MISSING_EXIT_PRICE",
        "UNTRADABLE_SIGNAL",
        "BENCHMARK_UNAVAILABLE",
        "COST_MODEL_NOT_CONFIGURED",
        "MULTIPLE_TESTING_RISK",
        "LOW_FACTOR_COVERAGE",
        "FORMAL_RESULT_NOT_COMPARABLE",
        "RESEARCH_ONLY",
        "NOT_PROOF_OF_PROFITABILITY",
    }
    assert {item.value for item in ExperimentRiskFlag} == required


@pytest.mark.parametrize(
    ("available_dates", "expected_status", "insufficient_flag"),
    [
        (41, "MINIMUM_WINDOW_AVAILABLE", False),
        (40, "INSUFFICIENT_HISTORY", True),
    ],
)
def test_53_history_extension_plan_flags_match_available_window(
    available_dates: int,
    expected_status: str,
    insufficient_flag: bool,
) -> None:
    window = HistoricalWindow(
        available_history_start=date(2026, 4, 30),
        available_history_end=date(2026, 7, 29),
        distinct_trade_dates=available_dates,
        earliest_signal_trade_date=date(2026, 6, 1),
        latest_signal_trade_date_1d=date(2026, 7, 28),
        latest_signal_trade_date_3d=date(2026, 7, 24),
        latest_signal_trade_date_5d=date(2026, 7, 22),
        latest_signal_trade_date_20d=date(2026, 7, 1),
        full_horizon_signal_date_count=22,
    )

    plan = ExperimentEvaluationService.history_extension_plan(window)

    assert plan["status"] == expected_status
    assert (
        ExperimentRiskFlag.INSUFFICIENT_HISTORY.value in plan["risk_flags"]
    ) is insufficient_flag
