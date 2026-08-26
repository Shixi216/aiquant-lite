from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from time import perf_counter

from trading.research.sentiment.v3.collectors import SentimentV3Collector
from trading.research.sentiment.v3.hashing import stable_content_hash
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import (
    Finality,
    RecordSource,
    SnapshotType,
)


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _print(value: object) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dict__"):
        value = value.__dict__
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _repository(args: argparse.Namespace) -> SentimentV3Repository:
    return SentimentV3Repository(args.database)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sentiment V3 S2A research-only Snapshot CLI"
    )
    parser.add_argument("--database", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    collect = commands.add_parser("collect")
    collect.add_argument("--trade-date", type=_date, default=date.today())
    collect.add_argument(
        "--finality",
        choices=[item.value for item in Finality],
        default=Finality.INTRADAY.value,
    )
    collect.add_argument(
        "--record-source",
        choices=[item.value for item in RecordSource],
        default=RecordSource.REAL.value,
    )
    collect.add_argument("--confirm-final", action="store_true")
    collect.add_argument("--dry-run", action="store_true")
    collect.add_argument("--corrects-market")
    collect.add_argument("--corrects-speculation")

    latest = commands.add_parser("show-latest")
    latest.add_argument("snapshot_type", choices=["MARKET", "SPECULATION"])
    latest.add_argument(
        "--record-source",
        choices=[item.value for item in RecordSource],
        default=RecordSource.REAL.value,
    )

    as_of = commands.add_parser("show-as-of")
    as_of.add_argument("snapshot_type", choices=["MARKET", "SPECULATION"])
    as_of.add_argument("--cutoff", type=_datetime, required=True)
    as_of.add_argument(
        "--record-source",
        choices=[item.value for item in RecordSource],
        default=RecordSource.REAL.value,
    )

    validate = commands.add_parser("validate")
    validate.add_argument(
        "--record-source",
        choices=[item.value for item in RecordSource],
        default=RecordSource.REAL.value,
    )
    validate.add_argument("--limit", type=int, default=1000)
    return parser


def _validate(args: argparse.Namespace) -> int:
    repository = _repository(args)
    record_source = RecordSource(args.record_source)
    snapshots = repository.list_snapshots(
        record_source=record_source,
        limit=args.limit,
    )
    errors: list[dict[str, str]] = []
    for snapshot in snapshots:
        meta = snapshot.meta
        payload = snapshot.model_dump(mode="json", exclude={"meta"})
        payload.pop("provider_health", None)
        calculated = stable_content_hash(
            snapshot_type=meta.snapshot_type,
            source=meta.source,
            trade_date=meta.trade_date,
            symbol_scope=meta.symbol_scope,
            schema_version=meta.schema_version,
            algorithm_version=meta.algorithm_version,
            normalized_payload=payload,
        )
        if calculated != meta.content_hash:
            errors.append(
                {
                    "snapshot_id": meta.snapshot_id,
                    "error": "CONTENT_HASH_MISMATCH",
                }
            )
    _print(
        {
            "valid": not errors,
            "checked": len(snapshots),
            "record_source": record_source.value,
            "errors": errors,
        }
    )
    return 0 if not errors else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = _repository(args)
    if args.command == "collect":
        result = SentimentV3Collector(repository=repository).collect(
            trade_date=args.trade_date,
            finality=Finality(args.finality),
            record_source=RecordSource(args.record_source),
            persist=not args.dry_run,
            confirmed_final=args.confirm_final,
            corrects_market_snapshot_id=args.corrects_market,
            corrects_speculation_snapshot_id=args.corrects_speculation,
        )
        _print(
            {
                "market": result.market.model_dump(mode="json"),
                "speculation": result.speculation.model_dump(mode="json"),
                "performance": result.performance.__dict__,
            }
        )
        return 0
    if args.command == "show-latest":
        started = perf_counter()
        snapshot = repository.get_latest_snapshot(
            snapshot_type=SnapshotType(args.snapshot_type),
            record_source=RecordSource(args.record_source),
        )
        elapsed_ms = (perf_counter() - started) * 1000
        _print(
            {
                "snapshot": (
                    None if snapshot is None else snapshot.model_dump(mode="json")
                ),
                "db_read_ms": elapsed_ms,
            }
        )
        return 0
    if args.command == "show-as-of":
        started = perf_counter()
        snapshot = repository.get_snapshot_as_of(
            snapshot_type=SnapshotType(args.snapshot_type),
            cutoff=args.cutoff,
            record_source=RecordSource(args.record_source),
        )
        elapsed_ms = (perf_counter() - started) * 1000
        _print(
            {
                "snapshot": (
                    None if snapshot is None else snapshot.model_dump(mode="json")
                ),
                "db_read_ms": elapsed_ms,
            }
        )
        return 0
    if args.command == "validate":
        return _validate(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
