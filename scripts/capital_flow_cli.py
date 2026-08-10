from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

from trading.research.capital_flow import (
    CapitalFlowAnalysisService,
    CapitalFlowAnalyzeRequest,
)
from trading.research.capital_flow.evaluation import CapitalFlowEvaluationService
from trading.research.capital_flow.schemas import CapitalFlowScope
from trading.schemas import AnalysisMode


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermes-OPC deterministic shadow capital-flow CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("symbol", "sector"):
        item = commands.add_parser(command)
        item.add_argument(command)
        item.add_argument("--data-cutoff", type=_time, required=True)
        item.add_argument("--mode", type=AnalysisMode, default=AnalysisMode.RESEARCH)
    market = commands.add_parser("market")
    market.add_argument("--data-cutoff", type=_time, required=True)
    market.add_argument("--mode", type=AnalysisMode, default=AnalysisMode.RESEARCH)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("snapshot_id")
    evaluate.add_argument("--data-cutoff", type=_time, required=True)
    backfill = commands.add_parser("backfill")
    backfill.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


async def _analyze(args: argparse.Namespace) -> object:
    scope = CapitalFlowScope(args.command.upper())
    return await CapitalFlowAnalysisService().analyze(
        CapitalFlowAnalyzeRequest(
            analysis_mode=args.mode,
            data_cutoff=args.data_cutoff,
            scope=scope,
            symbol=getattr(args, "symbol", None),
            sector=getattr(args, "sector", None),
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "backfill":
        from scripts.backfill_capital_flow import main as backfill_main

        return backfill_main(args.arguments)
    if args.command == "evaluate":
        result = CapitalFlowEvaluationService().evaluate(
            snapshot_id=args.snapshot_id,
            data_cutoff=args.data_cutoff,
        )
    else:
        result = asyncio.run(_analyze(args))
    print(
        json.dumps(
            result.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
