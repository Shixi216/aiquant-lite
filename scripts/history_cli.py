from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from data_hub.schemas.history import (
    AdjustmentType,
    HistoryBackfillRequest,
    HistoryRunActionRequest,
    SelectionStrategy,
    ShardType,
)
from data_hub.services.history_backfill_service import HistoryBackfillService
from data_hub.services.history_coverage_service import HistoryCoverageService
from data_hub.services.history_provider_service import (
    HistoryProviderVerificationService,
)


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _shard_type(value: str) -> ShardType:
    normalized = value.strip().replace("-", "_").upper()
    if not normalized.endswith("_SHARD"):
        normalized += "_SHARD"
    try:
        return ShardType(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "shard type must be symbol or trade-date"
        ) from exc


def _apply_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="explicitly enable writes; omitted means dry-run",
    )


def _report_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--report-path")


def _backfill_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-cutoff", type=_datetime, required=True)
    parser.add_argument(
        "--shard-type",
        type=_shard_type,
        default=ShardType.SYMBOL_SHARD,
    )
    parser.add_argument("--provider", default="AUTO")
    parser.add_argument(
        "--fallback-provider",
        action="append",
        dest="fallback_providers",
    )
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--board")
    parser.add_argument("--exchange")
    parser.add_argument("--start-symbol")
    parser.add_argument("--end-symbol")
    parser.add_argument("--start-date", type=_date)
    parser.add_argument("--end-date", type=_date)
    parser.add_argument(
        "--trade-date",
        type=_date,
        action="append",
        dest="trade_dates",
    )
    parser.add_argument("--start-trade-date", type=_date)
    parser.add_argument("--end-trade-date", type=_date)
    parser.add_argument("--target-trading-days", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--request-budget", type=int, default=100)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-symbols", type=int, default=100)
    parser.add_argument(
        "--adjustment-type",
        type=AdjustmentType,
        default=AdjustmentType.RAW,
    )
    parser.add_argument(
        "--selection-strategy",
        type=SelectionStrategy,
        default=SelectionStrategy.EXPLICIT,
    )
    parser.add_argument("--minimum-free-bytes", type=int, default=0)
    parser.add_argument("--verify-idempotency", action="store_true")
    _report_args(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermes-OPC bounded historical market-data CLI"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify_provider_capabilities")
    verify.add_argument("--data-cutoff", type=_datetime, required=True)
    verify.add_argument("--symbol", default="600000.SH")
    _apply_flag(verify)
    _report_args(verify)

    plan = commands.add_parser("plan_history_backfill")
    _backfill_args(plan)

    run = commands.add_parser("run_history_backfill")
    _backfill_args(run)
    _apply_flag(run)

    resume = commands.add_parser("resume_history_backfill")
    resume.add_argument("run_id")
    resume.add_argument("--data-cutoff", type=_datetime, required=True)
    resume.add_argument("--request-budget", type=int)
    resume.add_argument("--verify-idempotency", action="store_true")
    _apply_flag(resume)
    _report_args(resume)

    cancel = commands.add_parser("cancel_history_backfill")
    cancel.add_argument("run_id")
    cancel.add_argument("--data-cutoff", type=_datetime, required=True)
    _apply_flag(cancel)
    _report_args(cancel)

    coverage = commands.add_parser("generate_history_coverage_report")
    coverage.add_argument("--data-cutoff", type=_datetime, required=True)
    coverage.add_argument("--as-of-trade-date", type=_date)
    coverage.add_argument(
        "--adjustment-type",
        type=AdjustmentType,
        default=AdjustmentType.RAW,
    )
    _report_args(coverage)
    return parser


def _backfill_request(
    args: argparse.Namespace,
    *,
    dry_run: bool,
) -> HistoryBackfillRequest:
    return HistoryBackfillRequest(
        dry_run=dry_run,
        shard_type=args.shard_type,
        provider=(
            "TUSHARE"
            if args.shard_type == ShardType.TRADE_DATE_SHARD
            and args.provider == "AUTO"
            else args.provider
        ),
        fallback_providers=(
            args.fallback_providers
            or (
                []
                if args.shard_type == ShardType.TRADE_DATE_SHARD
                else ["BAOSTOCK", "AKSHARE"]
            )
        ),
        symbols=args.symbols or [],
        board=args.board,
        exchange=args.exchange,
        start_symbol=args.start_symbol,
        end_symbol=args.end_symbol,
        start_date=args.start_date,
        end_date=args.end_date,
        trade_dates=args.trade_dates or [],
        start_trade_date=args.start_trade_date,
        end_trade_date=args.end_trade_date,
        target_trading_days=args.target_trading_days,
        batch_size=args.batch_size,
        concurrency=args.concurrency,
        request_budget=args.request_budget,
        max_retries=args.max_retries,
        data_cutoff=args.data_cutoff,
        adjustment_type=args.adjustment_type,
        selection_strategy=args.selection_strategy,
        max_symbols=args.max_symbols,
        minimum_free_bytes=args.minimum_free_bytes,
        report_path=args.report_path,
        verify_idempotency=args.verify_idempotency,
    )


def _payload(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _write_report(path: str | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    target = Path(path)
    if target.suffix.lower() != ".json":
        target = target / "history-report.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary = target.with_suffix(".md")
    lines = [
        "# Hermes-OPC historical market-data report",
        "",
        f"- status: `{payload.get('status', 'GENERATED')}`",
        f"- mode: `{payload.get('mode', 'READ_ONLY')}`",
        f"- run_id: `{payload.get('run_id', 'n/a')}`",
        f"- data_cutoff: `{payload.get('data_cutoff', 'n/a')}`",
        f"- request_count: `{payload.get('request_count', 0)}`",
        f"- report: `{target.name}`",
        "",
    ]
    summary.write_text("\n".join(lines), encoding="utf-8")


def run_command(argv: list[str] | None = None) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    if args.command == "verify_provider_capabilities":
        result: Any = HistoryProviderVerificationService().verify(
            data_cutoff=args.data_cutoff,
            symbol=args.symbol,
            persist_calendar=args.apply,
        )
    elif args.command == "plan_history_backfill":
        result = HistoryBackfillService().run(
            _backfill_request(args, dry_run=True)
        )
    elif args.command == "run_history_backfill":
        result = HistoryBackfillService().run(
            _backfill_request(args, dry_run=not args.apply)
        )
    elif args.command == "resume_history_backfill":
        result = HistoryBackfillService().resume(
            args.run_id,
            HistoryRunActionRequest(
                apply=args.apply,
                data_cutoff=args.data_cutoff,
                request_budget=args.request_budget,
                verify_idempotency=args.verify_idempotency,
            ),
        )
    elif args.command == "cancel_history_backfill":
        result = HistoryBackfillService().cancel(
            args.run_id,
            HistoryRunActionRequest(
                apply=args.apply,
                data_cutoff=args.data_cutoff,
            ),
        )
    else:
        result = HistoryCoverageService().generate(
            data_cutoff=args.data_cutoff,
            adjustment_type=args.adjustment_type,
            as_of_trade_date=args.as_of_trade_date,
        )
    payload = _payload(result)
    _write_report(args.report_path, payload)
    return payload


def main() -> int:
    payload = run_command()
    console = dict(payload)
    for key in ("items", "shards", "date_shards", "request_audits"):
        values = console.pop(key, None)
        if isinstance(values, list):
            console[f"{key}_count"] = len(values)
    print(json.dumps(console, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "run_command"]
