from __future__ import annotations

import argparse
import json
import os
from datetime import date
from typing import Any

from database.db import get_connection
from trading.research.sentiment.v3.adj_factor_coverage import (
    AdjustmentFactorCoverageAuditor,
    TushareAdjustmentFactorDateProvider,
)
from trading.research.sentiment.v3.adj_factor_batch_backfill import (
    BatchedAdjustmentFactorBackfill,
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _range() -> tuple[date, date]:
    with get_connection(read_only=True) as connection:
        result = connection.execute(
            """
            SELECT MIN(trade_date), MAX(trade_date)
            FROM canonical_historical_bars
            WHERE adjustment_type = 'RAW'
              AND verification_status <> 'CONFLICT'
              AND regexp_matches(symbol, '^[0-9]{6}\\.(SH|SZ|BJ)$')
            """
        ).fetchone()
    if result[0] is None or result[1] is None:
        raise RuntimeError("No eligible canonical RAW bars")
    return result[0], result[1]


def audit_command(_: argparse.Namespace) -> None:
    report = AdjustmentFactorCoverageAuditor(
        lambda: get_connection(read_only=True)
    ).run()
    _print(report.as_dict())


def backfill_command(args: argparse.Namespace) -> None:
    if not args.apply:
        raise SystemExit("Backfill requires explicit --apply")
    if os.environ.get("HERMES_DB_MAINTENANCE") != "1":
        raise SystemExit("Backfill requires HERMES_DB_MAINTENANCE=1")
    default_start, default_end = _range()
    service = BatchedAdjustmentFactorBackfill(
        connection_factory=get_connection,
        provider=TushareAdjustmentFactorDateProvider(),
        minimum_request_interval_seconds=args.request_interval,
        database_batch_dates=args.database_batch_dates,
    )
    result = service.run(
        start_date=args.start or default_start,
        end_date=args.end or default_end,
        run_id=args.run_id,
        max_dates=args.max_dates,
    )
    _print(result.__dict__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sentiment V3 S3A.1 adjustment-factor audit and repair"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit = subparsers.add_parser("audit")
    audit.set_defaults(handler=audit_command)

    backfill = subparsers.add_parser("backfill")
    backfill.add_argument("--apply", action="store_true")
    backfill.add_argument("--start", type=_date)
    backfill.add_argument("--end", type=_date)
    backfill.add_argument("--run-id")
    backfill.add_argument("--max-dates", type=int)
    backfill.add_argument("--request-interval", type=float, default=0.20)
    backfill.add_argument("--database-batch-dates", type=int, default=20)
    backfill.set_defaults(handler=backfill_command)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
