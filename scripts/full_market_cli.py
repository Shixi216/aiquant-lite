from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from data_hub.schemas.full_market import (
    AnalysisMode,
    CandidateEnrichmentRequest,
    DataExpansionRequest,
    ExpansionType,
    MarketSnapshotSyncRequest,
    UniverseSyncRequest,
)
from data_hub.services.candidate_enrichment_service import (
    CandidateEnrichmentService,
)
from data_hub.services.coverage_service import DataCoverageService
from data_hub.services.daily_data_update_service import DailyDataUpdateService
from data_hub.services.data_expansion_service import DataExpansionService
from data_hub.services.entity_linking_service import EntityLinkingService
from data_hub.services.market_snapshot_service import MarketSnapshotService
from data_hub.services.universe_service import StockUniverseService


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _mode(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")


def _common(parser: argparse.ArgumentParser) -> None:
    _mode(parser)
    parser.add_argument("--data-cutoff", type=_datetime, required=True)
    parser.add_argument("--provider", default="AUTO")
    parser.add_argument("--request-budget", type=int, default=10)
    parser.add_argument("--report-path")


def build_parser(command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"Hermes-OPC {command}")
    _common(parser)
    if command == "sync_stock_universe":
        parser.set_defaults(request_budget=3)
    elif command == "sync_market_snapshot":
        parser.set_defaults(provider="AKSHARE", request_budget=1)
        parser.add_argument("--limit", type=int)
        parser.add_argument("--maximum-age-seconds", type=int, default=300)
    elif command == "backfill_daily_bars":
        parser.add_argument("--symbol", action="append", dest="symbols")
        parser.add_argument("--trade-date", type=_date)
        parser.add_argument("--start-date", type=_date)
        parser.add_argument("--end-date", type=_date)
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--resume", action="store_true")
    elif command in {"backfill_announcements", "backfill_finance_news"}:
        parser.add_argument("--trade-date", type=_date)
        parser.add_argument("--start-date", type=_date)
        parser.add_argument("--end-date", type=_date)
        parser.add_argument("--batch-size", type=int, default=50)
        parser.add_argument("--resume", action="store_true")
    elif command == "build_entity_links":
        parser.set_defaults(provider="LOCAL_RULES", request_budget=0)
        parser.add_argument("--limit", type=int)
    elif command == "enrich_candidates":
        parser.add_argument("symbols", nargs="+")
        parser.add_argument(
            "--analysis-mode",
            type=AnalysisMode,
            default=AnalysisMode.RESEARCH,
        )
        parser.add_argument("--minimum-history-days", type=int, default=60)
        parser.add_argument("--model-call-budget", type=int, default=0)
    elif command == "run_daily_data_update":
        parser.set_defaults(request_budget=6)
    elif command == "generate_data_coverage_report":
        parser.set_defaults(request_budget=0)
    else:
        raise ValueError(f"unknown full-market command: {command}")
    return parser


def _write_report(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if target.suffix.lower() == ".json":
        markdown = target.with_suffix(".md")
        markdown.write_text(
            "# Hermes-OPC data foundation report\n\n"
            f"- status: `{payload.get('status', 'generated')}`\n"
            f"- data cutoff: `{payload.get('data_cutoff', 'n/a')}`\n"
            f"- report: `{target.name}`\n",
            encoding="utf-8",
        )


def run_command(
    command: str,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    args = build_parser(command).parse_args(argv)
    dry_run = not args.apply
    if command == "sync_stock_universe":
        result: Any = StockUniverseService().sync(
            UniverseSyncRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                dry_run=dry_run,
                provider=args.provider,
                request_budget=args.request_budget,
                data_cutoff=args.data_cutoff,
                report_path=args.report_path,
            )
        )
    elif command == "sync_market_snapshot":
        result = MarketSnapshotService().sync(
            MarketSnapshotSyncRequest(
                analysis_mode=AnalysisMode.SCREENING,
                dry_run=dry_run,
                provider=args.provider,
                request_budget=args.request_budget,
                data_cutoff=args.data_cutoff,
                maximum_age_seconds=args.maximum_age_seconds,
                limit=args.limit,
            )
        )
    elif command in {
        "backfill_daily_bars",
        "backfill_announcements",
        "backfill_finance_news",
    }:
        expansion_type = {
            "backfill_daily_bars": ExpansionType.DAILY_BARS,
            "backfill_announcements": ExpansionType.ANNOUNCEMENTS,
            "backfill_finance_news": ExpansionType.FINANCE_NEWS,
        }[command]
        result = DataExpansionService().run(
            DataExpansionRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                dry_run=dry_run,
                provider=args.provider,
                request_budget=args.request_budget,
                data_cutoff=args.data_cutoff,
                expansion_type=expansion_type,
                symbols=getattr(args, "symbols", None) or [],
                trade_date=args.trade_date,
                start_date=args.start_date,
                end_date=args.end_date,
                batch_size=args.batch_size,
                resume=args.resume,
                report_path=args.report_path,
            )
        )
    elif command == "build_entity_links":
        result = EntityLinkingService().build(
            DataExpansionRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                dry_run=dry_run,
                provider=args.provider,
                request_budget=args.request_budget,
                data_cutoff=args.data_cutoff,
                expansion_type=ExpansionType.ENTITY_LINKS,
                report_path=args.report_path,
            ),
            limit=args.limit,
        )
    elif command == "enrich_candidates":
        result = CandidateEnrichmentService().enrich(
            CandidateEnrichmentRequest(
                analysis_mode=args.analysis_mode,
                dry_run=dry_run,
                provider=args.provider,
                request_budget=args.request_budget,
                data_cutoff=args.data_cutoff,
                symbols=args.symbols,
                minimum_history_days=args.minimum_history_days,
                model_call_budget=args.model_call_budget,
            )
        )
    elif command == "run_daily_data_update":
        result = DailyDataUpdateService().run(
            data_cutoff=args.data_cutoff,
            apply=args.apply,
            request_budget=args.request_budget,
        )
    else:
        result = DataCoverageService().generate(
            data_cutoff=args.data_cutoff,
            persist=args.apply,
            report_path=args.report_path,
        )
    payload = (
        result.model_dump(mode="json")
        if hasattr(result, "model_dump")
        else result
    )
    _write_report(args.report_path, payload)
    return payload


def main_for(command: str, argv: list[str] | None = None) -> int:
    payload = run_command(command, argv)
    console_payload = dict(payload)
    for field in ("items", "steps"):
        values = console_payload.pop(field, None)
        if isinstance(values, list):
            console_payload[f"{field}_count"] = len(values)
    print(json.dumps(console_payload, ensure_ascii=False, indent=2, default=str))
    return 0


__all__ = ["build_parser", "main_for", "run_command"]
