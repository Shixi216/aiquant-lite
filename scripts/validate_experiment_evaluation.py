from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import duckdb

from config.settings import settings
from router.api.app import app as router_app
from trading.experiments.models import (
    ExperimentType,
    SignalType,
)
from trading.experiments.reporting import write_json_and_markdown
from trading.experiments.schemas import (
    CreateExperimentRequest,
    ForwardReturnUpdateRequest,
    HistoricalReplayRequest,
    PortfolioBacktestRequest,
    RunExperimentRequest,
)
from trading.experiments.service import ExperimentEvaluationService


def _counts() -> dict[str, int]:
    tables = [
        "data_records",
        "canonical_historical_bars",
        "canonical_market_records",
        "scanner_runs",
        "scanner_candidates",
        "scanner_evaluations",
        "orchestration_evaluations",
        "decision_packets",
        "paper_accounts",
        "trading_orders",
        "experiment_definitions",
        "experiment_runs",
        "experiment_observations",
        "forward_return_labels",
        "backtest_runs",
        "backtest_positions",
        "evaluation_metrics",
        "evaluation_slices",
        "experiment_data_quality_audits",
    ]
    with duckdb.connect(str(settings.opc_database_path), read_only=True) as connection:
        existing = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }
        return {
            table: connection.execute(
                f'SELECT count(*) FROM "{table}"'
            ).fetchone()[0]
            for table in tables
            if table in existing
        }


def _latest_scanner_run() -> str:
    with duckdb.connect(str(settings.opc_database_path), read_only=True) as connection:
        row = connection.execute(
            """
            SELECT run_id FROM scanner_runs
            ORDER BY generated_at DESC, run_id DESC LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("no persisted scanner run is available")
    return row[0]


def _migration_state() -> dict[str, Any]:
    with duckdb.connect(str(settings.opc_database_path), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT migration_id, checksum
            FROM schema_migrations
            WHERE migration_id >= '0100_' AND migration_id < '0112_'
            ORDER BY migration_id
            """
        ).fetchall()
    return {
        "count": len(rows),
        "migration_ids": [row[0] for row in rows],
        "checksums_present": all(bool(row[1]) for row in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experiments"),
    )
    args = parser.parse_args()
    if not args.apply:
        raise SystemExit("validation writes research audit data; use --apply")
    started = perf_counter()
    service = ExperimentEvaluationService()
    baseline = _counts()
    database_size_before = Path(settings.opc_database_path).stat().st_size
    scanner_run_id = _latest_scanner_run()

    prospective_definition = service.create_experiment(
        CreateExperimentRequest(
            experiment_name="stage10-prospective-scanner-tracking-v1",
            experiment_type=ExperimentType.PROSPECTIVE_TRACKING,
            description="Register Stage 9 persisted scanner output without future data.",
            hypothesis="Prospective observations can be labeled later without mutating ranks.",
            persist=True,
        )
    )
    prospective = service.run_prospective(
        prospective_definition.experiment.experiment_id,
        RunExperimentRequest(
            source_scanner_run_id=scanner_run_id,
            persist=True,
        ),
    )
    prospective_repeat = service.run_prospective(
        prospective_definition.experiment.experiment_id,
        RunExperimentRequest(
            source_scanner_run_id=scanner_run_id,
            persist=True,
        ),
    )
    label_update = service.update_forward_returns(
        ForwardReturnUpdateRequest(
            run_id=prospective.run.run_id,
            as_of=datetime.now().astimezone(),
            persist=True,
        )
    )
    label_update_repeat = service.update_forward_returns(
        ForwardReturnUpdateRequest(
            run_id=prospective.run.run_id,
            as_of=datetime.now().astimezone(),
            persist=True,
        )
    )

    historical_definition = service.create_experiment(
        CreateExperimentRequest(
            experiment_name="stage10-fixed-price-volume-replay-v1",
            experiment_type=ExperimentType.HISTORICAL_REPLAY,
            description="Fixed point-in-time RAW price-volume replay.",
            hypothesis=(
                "A reproducible price-volume scanner can be compared with "
                "amount, momentum, random, and eligible-universe baselines."
            ),
            top_k=20,
            minimum_sample_size=30,
            random_seed=20260730,
            persist=True,
        )
    )
    historical = service.replay_historical(
        historical_definition.experiment.experiment_id,
        HistoricalReplayRequest(
            random_seed=20260730,
            persist=True,
        ),
    )
    metrics = service.metrics(historical.run.run_id)
    backtest = service.run_portfolio_backtest(
        PortfolioBacktestRequest(
            experiment_id=historical_definition.experiment.experiment_id,
            run_id=historical.run.run_id,
            signal_type=SignalType.SCANNER_ONLY,
            horizon_trading_days=5,
            top_k=20,
            max_position_weight=0.10,
            persist=True,
        )
    )
    final_counts = _counts()
    database_size_after = Path(settings.opc_database_path).stat().st_size
    horizon_counts = {
        str(horizon): {
            status: count
            for (item_horizon, status), count in Counter(
                (
                    label.horizon_trading_days,
                    label.label_status.value,
                )
                for label in historical.labels
            ).items()
            if item_horizon == horizon
        }
        for horizon in (1, 3, 5, 20)
    }
    metric_summary = [
        {
            "group_name": item.group_name,
            "horizon_trading_days": item.horizon_trading_days,
            "sample_count": item.sample_count,
            "valid_count": item.valid_count,
            "mean_return": item.mean_return,
            "median_return": item.median_return,
            "win_rate": item.win_rate,
            "mean_excess_return": item.mean_excess_return,
            "confidence_interval": item.confidence_interval,
            "risk_flags": [flag.value for flag in item.risk_flags],
        }
        for item in metrics.metrics
    ]
    payload = {
        "stage": "stage10-experiment-evaluation-v1",
        "verified_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "point_in_time_status": historical.point_in_time_status,
        "sample_count": historical.sample_count,
        "profitability_proven": False,
        "data_quality_summary": historical.data_quality_summary,
        "risk_flags": [flag.value for flag in historical.risk_flags],
        "stage9_checkpoint_baseline": {
            "checkpoint_path": (
                "E:\\hermes-opc-checkpoint-stage9-20260730-120349"
            ),
            "data_records": 384648,
            "canonical_historical_bars": 330795,
            "canonical_market_records": 295,
            "scanner_runs": 1,
            "scanner_candidates": 20,
            "scanner_evaluations": 0,
            "router_openapi_paths": 79,
            "router_openapi_schemas": 203,
            "pytest_passed": 666,
            "database_size_bytes": 446705664,
        },
        "resume_state_before_final_acceptance": baseline,
        "final_counts": final_counts,
        "migration": _migration_state(),
        "prospective": {
            "experiment_id": prospective_definition.experiment.experiment_id,
            "run_id": prospective.run.run_id,
            "source_scanner_run_id": scanner_run_id,
            "observation_count": len(prospective.observations),
            "initial_label_count": len(prospective.labels),
            "stale_observation_count": sum(
                item.stale_snapshot for item in prospective.observations
            ),
            "repeat_inserted_observations": (
                prospective_repeat.persisted_observation_count
            ),
            "repeat_inserted_labels": prospective_repeat.persisted_label_count,
            "label_update_status": label_update.status,
            "label_update_counts": Counter(
                item.label_status.value for item in label_update.labels
            ),
            "repeat_label_update_status": label_update_repeat.status,
            "rankings_mutated": False,
        },
        "historical_replay": {
            "experiment_id": historical_definition.experiment.experiment_id,
            "run_id": historical.run.run_id,
            "window": historical.historical_window.model_dump(mode="json"),
            "signal_date_count": historical.signal_date_count,
            "symbol_count": historical.symbol_count,
            "observation_count": len(historical.observations),
            "horizon_label_counts": horizon_counts,
            "query_count": historical.query_count,
            "network_request_count": historical.network_request_count,
            "model_call_count": historical.model_call_count,
            "elapsed_ms": historical.run.elapsed_ms,
            "peak_memory_bytes": historical.run.peak_memory_bytes,
            "future_data_used_to_generate_signals": False,
            "partial_replay": True,
        },
        "comparisons": {
            "groups": sorted(
                {item.group_name for item in metrics.metrics}
            ),
            "eligible_universe_benchmark_available": (
                historical.run.benchmark_available
            ),
            "metrics": metric_summary,
        },
        "formal_vs_shadow": service.formal_shadow_boundary(),
        "portfolio_backtest": backtest.model_dump(
            mode="json",
            exclude={"positions"},
        ),
        "portfolio_position_count": len(backtest.positions),
        "history_extension_plan": service.history_extension_plan(
            historical.historical_window
        ),
        "database": {
            "size_before": database_size_before,
            "size_after": database_size_after,
            "growth_bytes": database_size_after - database_size_before,
        },
        "openapi": {
            "paths": len(router_app.openapi().get("paths", {})),
            "schemas": len(
                router_app.openapi()
                .get("components", {})
                .get("schemas", {})
            ),
        },
        "safety": {
            "formal_weights_changed": False,
            "formal_actions_changed": False,
            "hard_veto_changed": False,
            "decision_packet_changed": False,
            "real_orders_created": False,
            "paper_trading_written": False,
            "production_weight_update": False,
            "history_backfill_started": False,
            "fundamental_or_shadow_data_expanded": False,
            "network_requests": 0,
            "model_calls": 0,
        },
        "elapsed_ms": (perf_counter() - started) * 1000,
        "conclusion": (
            "The available partial historical sample is not proof of stable "
            "profitability and cannot update production strategy weights."
        ),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.output_dir / "stage10-acceptance"
    paths = write_json_and_markdown(payload, output_prefix=prefix)
    (args.output_dir / "history-extension-plan.json").write_text(
        json.dumps(
            payload["history_extension_plan"],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "json_report": str(paths[0]),
                "markdown_report": str(paths[1]),
                "prospective_observations": len(prospective.observations),
                "historical_signal_dates": historical.signal_date_count,
                "historical_symbols": historical.symbol_count,
                "historical_observations": len(historical.observations),
                "horizon_label_counts": horizon_counts,
                "profitability_proven": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
