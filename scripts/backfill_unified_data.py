from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from data_hub.backfill import BackfillOptions, run_backfill
from data_hub.schemas.market import DataType


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "time must use ISO 8601 format"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "time must include an explicit timezone"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safely backfill Hermes-OPC canonical facts and event clusters. "
            "The default mode is dry-run."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview results without writing the DuckDB database (default).",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="Persist canonical facts, event clusters, and audit state.",
    )
    parser.add_argument(
        "--data-type",
        action="append",
        choices=[item.value for item in DataType],
        dest="data_types",
        help="Filter by one data type; repeat for multiple types.",
    )
    parser.add_argument("--symbol", help="Filter by canonical symbol.")
    parser.add_argument(
        "--start-time",
        type=_aware_datetime,
        help="Inclusive event-time lower bound with timezone.",
    )
    parser.add_argument(
        "--end-time",
        type=_aware_datetime,
        help="Inclusive event-time upper bound with timezone.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of raw records selected.",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        help="Resume an interrupted apply run by ID, or the latest run.",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="JSON/Markdown report base path or directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = BackfillOptions(
        apply=bool(args.apply),
        data_types=tuple(args.data_types or ()),
        symbol=args.symbol,
        start_time=args.start_time,
        end_time=args.end_time,
        limit=args.limit,
        resume=args.resume,
        report_path=args.report_path,
    )
    report = run_backfill(options)
    print(
        json.dumps(
            {
                "backfill_run_id": report["backfill_run_id"],
                "mode": report["mode"],
                "status": report["status"],
                "original_record_count": report[
                    "original_record_count"
                ],
                "selected_record_count": report[
                    "selected_record_count"
                ],
                "processed_count": report["processed_count"],
                "success_count": report["success_count"],
                "skipped_count": report["skipped_count"],
                "conflict_count": report["conflict_count"],
                "failed_count": report["failed_count"],
                "database_totals": report["database_totals"],
                "report_json_path": report["report_json_path"],
                "report_markdown_path": report[
                    "report_markdown_path"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] in {"COMPLETED", "PARTIAL"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
