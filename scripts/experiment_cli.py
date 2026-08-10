from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trading.experiments.models import (
    ExperimentType,
    LabelStatus,
    RebalanceMethod,
    SignalType,
    WeightingMethod,
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


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _json(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-only experiment evaluation CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create_experiment")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--type",
        choices=[item.value for item in ExperimentType],
        required=True,
    )
    create.add_argument("--description", default="")
    create.add_argument("--hypothesis", default="")
    create.add_argument("--start-trade-date")
    create.add_argument("--end-trade-date")
    create.add_argument("--random-seed", type=int, default=20260730)
    create.add_argument("--apply", action="store_true")

    run = subparsers.add_parser("run_experiment")
    run.add_argument("experiment_id")
    run.add_argument("--source-scanner-run-id", required=True)
    run.add_argument("--apply", action="store_true")

    replay = subparsers.add_parser("replay_historical_scanner")
    replay.add_argument("experiment_id")
    replay.add_argument("--start-trade-date")
    replay.add_argument("--end-trade-date")
    replay.add_argument("--random-seed", type=int, default=20260730)
    replay.add_argument("--apply", action="store_true")

    update = subparsers.add_parser("update_forward_returns")
    update.add_argument("--run-id")
    update.add_argument("--experiment-id")
    update.add_argument("--as-of")
    update.add_argument("--apply", action="store_true")

    backtest = subparsers.add_parser("run_portfolio_backtest")
    backtest.add_argument("experiment_id")
    backtest.add_argument("run_id")
    backtest.add_argument(
        "--signal-type",
        choices=[item.value for item in SignalType],
        default=SignalType.SCANNER_ONLY.value,
    )
    backtest.add_argument(
        "--horizon", type=int, choices=(1, 3, 5, 20), default=5
    )
    backtest.add_argument(
        "--weighting",
        choices=[item.value for item in WeightingMethod],
        default=WeightingMethod.TOP_K_EQUAL_WEIGHT.value,
    )
    backtest.add_argument(
        "--rebalance",
        choices=[item.value for item in RebalanceMethod],
        default=RebalanceMethod.NON_OVERLAPPING_COHORT.value,
    )
    backtest.add_argument("--max-position-weight", type=float, default=0.10)
    backtest.add_argument("--apply", action="store_true")

    show = subparsers.add_parser("show_experiment")
    show.add_argument("experiment_id")

    metrics = subparsers.add_parser("show_experiment_metrics")
    metrics.add_argument("run_id")

    export = subparsers.add_parser("export_experiment_report")
    export.add_argument("run_id")
    export.add_argument("--output-prefix", type=Path, required=True)
    export.add_argument("--persist", action="store_true")

    pending = subparsers.add_parser("list_pending_labels")
    pending.add_argument("run_id")
    return parser


def main() -> None:
    args = _parser().parse_args()
    service = ExperimentEvaluationService()
    if args.command == "create_experiment":
        _json(
            service.create_experiment(
                CreateExperimentRequest(
                    experiment_name=args.name,
                    experiment_type=ExperimentType(args.type),
                    description=args.description,
                    hypothesis=args.hypothesis,
                    start_trade_date=_date(args.start_trade_date),
                    end_trade_date=_date(args.end_trade_date),
                    random_seed=args.random_seed,
                    persist=args.apply,
                )
            )
        )
    elif args.command == "run_experiment":
        _json(
            service.run_prospective(
                args.experiment_id,
                RunExperimentRequest(
                    source_scanner_run_id=args.source_scanner_run_id,
                    persist=args.apply,
                ),
            )
        )
    elif args.command == "replay_historical_scanner":
        _json(
            service.replay_historical(
                args.experiment_id,
                HistoricalReplayRequest(
                    start_trade_date=_date(args.start_trade_date),
                    end_trade_date=_date(args.end_trade_date),
                    random_seed=args.random_seed,
                    persist=args.apply,
                ),
            )
        )
    elif args.command == "update_forward_returns":
        _json(
            service.update_forward_returns(
                ForwardReturnUpdateRequest(
                    run_id=args.run_id,
                    experiment_id=args.experiment_id,
                    as_of=(
                        datetime.now().astimezone()
                        if args.as_of is None
                        else datetime.fromisoformat(args.as_of)
                    ),
                    persist=args.apply,
                )
            )
        )
    elif args.command == "run_portfolio_backtest":
        _json(
            service.run_portfolio_backtest(
                PortfolioBacktestRequest(
                    experiment_id=args.experiment_id,
                    run_id=args.run_id,
                    signal_type=SignalType(args.signal_type),
                    horizon_trading_days=args.horizon,
                    weighting_method=WeightingMethod(args.weighting),
                    rebalance_method=RebalanceMethod(args.rebalance),
                    max_position_weight=args.max_position_weight,
                    persist=args.apply,
                )
            )
        )
    elif args.command == "show_experiment":
        _json(service.get_experiment(args.experiment_id))
    elif args.command == "show_experiment_metrics":
        _json(service.metrics(args.run_id))
    elif args.command == "export_experiment_report":
        if not args.persist:
            raise SystemExit("export requires explicit --persist")
        report = service.report(args.run_id)
        paths = write_json_and_markdown(
            report.model_dump(mode="json"),
            output_prefix=args.output_prefix,
        )
        _json({"json": str(paths[0]), "markdown": str(paths[1])})
    elif args.command == "list_pending_labels":
        labels = service.repository.list_labels(args.run_id)
        _json(
            {
                "run_id": args.run_id,
                "pending_labels": [
                    item.model_dump(mode="json")
                    for item in labels
                    if item.label_status
                    in {
                        LabelStatus.PENDING,
                        LabelStatus.INSUFFICIENT_FUTURE_DATA,
                    }
                ],
                "research_only": True,
                "profitability_proven": False,
            }
        )


if __name__ == "__main__":
    main()
