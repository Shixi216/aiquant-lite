from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from trading.research.sentiment import (
    SentimentAnalysisService,
    SentimentAnalyzeRequest,
    SentimentEvaluationService,
)
from trading.research.sentiment.market_breadth import (
    MarketBreadthService,
)
from trading.research.sentiment.repository import SentimentRepository
from trading.schemas import AnalysisMode


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "timestamps must include a timezone"
        )
    return parsed


def _print(value: object) -> None:
    payload = (
        value.model_dump(mode="json")
        if hasattr(value, "model_dump")
        else value
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermes-OPC shadow sentiment CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    event = commands.add_parser("event")
    event.add_argument("event_cluster_id")
    event.add_argument("--data-cutoff", type=_time, required=True)
    event.add_argument("--allow-model-calls", action="store_true")

    symbol = commands.add_parser("symbol")
    symbol.add_argument("symbol")
    symbol.add_argument("--data-cutoff", type=_time, required=True)
    symbol.add_argument(
        "--mode",
        choices=[mode.value for mode in AnalysisMode],
        default=AnalysisMode.RESEARCH.value,
    )
    symbol.add_argument("--allow-model-calls", action="store_true")

    market = commands.add_parser("market")
    market.add_argument("--data-cutoff", type=_time, required=True)
    market.add_argument(
        "--mode",
        choices=[mode.value for mode in AnalysisMode],
        default=AnalysisMode.RESEARCH.value,
    )

    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("snapshot_id")
    evaluation.add_argument("--data-cutoff", type=_time, required=True)

    backfill = commands.add_parser("backfill")
    backfill.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    repository = SentimentRepository()
    service = SentimentAnalysisService(repository=repository)
    if args.command == "event":
        bundle = repository.get_event_bundle(args.event_cluster_id)
        if bundle is None:
            raise SystemExit(f"Event not found: {args.event_cluster_id}")
        result = await service.analyze_event(
            bundle,
            data_cutoff=args.data_cutoff,
            allow_model_calls=args.allow_model_calls,
            persist=True,
        )
        _print(result)
        return 0
    if args.command == "symbol":
        result = await service.analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode(args.mode),
                data_cutoff=args.data_cutoff,
                symbol=args.symbol,
                allow_model_calls=args.allow_model_calls,
            )
        )
        _print(result)
        return 0
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"event", "symbol"}:
        return asyncio.run(_run_async(args))
    repository = SentimentRepository()
    if args.command == "market":
        result = MarketBreadthService(repository).calculate(
            analysis_mode=AnalysisMode(args.mode),
            data_cutoff=args.data_cutoff,
            persist=True,
        )
        _print(result)
        return 0
    if args.command == "evaluate":
        result = SentimentEvaluationService(repository).evaluate(
            snapshot_id=args.snapshot_id,
            data_cutoff=args.data_cutoff,
        )
        _print(result)
        return 0
    if args.command == "backfill":
        from scripts.backfill_sentiment import main as backfill_main

        return backfill_main(args.arguments)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
