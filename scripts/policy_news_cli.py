from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from scripts.backfill_policy_news import main as backfill_main
from trading.research.policy_news.evaluation import (
    PolicyNewsEvaluationService,
)
from trading.research.policy_news.schemas import PolicyNewsAnalyzeRequest
from trading.research.policy_news.service import PolicyNewsAnalysisService
from trading.schemas import AnalysisMode


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "timestamps must include a timezone"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermes-OPC shadow policy/news CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    event = commands.add_parser("event")
    event.add_argument("event_cluster_id")
    event.add_argument("--data-cutoff", type=_time, required=True)
    event.add_argument(
        "--mode",
        type=AnalysisMode,
        default=AnalysisMode.RESEARCH,
    )
    event.add_argument("--allow-model-calls", action="store_true")

    symbol = commands.add_parser("symbol")
    symbol.add_argument("symbol")
    symbol.add_argument("--data-cutoff", type=_time, required=True)
    symbol.add_argument(
        "--mode",
        type=AnalysisMode,
        default=AnalysisMode.RESEARCH,
    )
    symbol.add_argument("--allow-model-calls", action="store_true")

    sector = commands.add_parser("sector")
    sector.add_argument("sector")
    sector.add_argument("--data-cutoff", type=_time, required=True)
    sector.add_argument(
        "--mode",
        type=AnalysisMode,
        default=AnalysisMode.RESEARCH,
    )
    sector.add_argument("--allow-model-calls", action="store_true")

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("snapshot_id")
    evaluate.add_argument("--data-cutoff", type=_time, required=True)

    backfill = commands.add_parser("backfill")
    backfill.add_argument(
        "arguments",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to scripts.backfill_policy_news",
    )
    return parser


async def _analyze(args: argparse.Namespace) -> dict:
    request = PolicyNewsAnalyzeRequest(
        analysis_mode=args.mode,
        data_cutoff=args.data_cutoff,
        symbol=getattr(args, "symbol", None),
        sector=getattr(args, "sector", None),
        event_cluster_ids=(
            [args.event_cluster_id]
            if hasattr(args, "event_cluster_id")
            else []
        ),
        allow_model_calls=args.allow_model_calls,
        allow_external_fetch=False,
    )
    result = await PolicyNewsAnalysisService().analyze(request)
    return result.model_dump(mode="json")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backfill":
        return backfill_main(args.arguments)
    if args.command == "evaluate":
        result = PolicyNewsEvaluationService().evaluate(
            snapshot_id=args.snapshot_id,
            data_cutoff=args.data_cutoff,
        )
        payload = result.model_dump(mode="json")
    else:
        payload = asyncio.run(_analyze(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
