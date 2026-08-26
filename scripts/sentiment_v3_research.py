from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from trading.research.sentiment.v3.feature_assembly import (
    DatasetCutoffPolicy,
    PacketType,
    ResearchDatasetBuilder,
    ResearchFeatureAssembler,
)
from trading.research.sentiment.v3.feature_registry import FEATURE_REGISTRY_V1
from trading.research.sentiment.v3.repository import SentimentV3Repository
from trading.research.sentiment.v3.schemas import RecordSource


def _datetime(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone")
    return result


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _print(value: object) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sentiment V3 S2E research-only CLI")
    parser.add_argument("--database", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)

    assemble = commands.add_parser("assemble")
    assemble.add_argument("symbol")
    assemble.add_argument("--cutoff", type=_datetime, required=True)
    assemble.add_argument("--trade-date", type=_date)
    assemble.add_argument(
        "--packet-type",
        choices=[item.value for item in PacketType],
        default=PacketType.EOD_RESEARCH_PACKET.value,
    )
    assemble.add_argument(
        "--record-source",
        choices=[item.value for item in RecordSource],
        default=RecordSource.REAL.value,
    )
    assemble.add_argument("--history-ready-only", action="store_true")
    assemble.add_argument("--persist", action="store_true")

    show = commands.add_parser("show")
    show.add_argument("packet_id")

    registry = commands.add_parser("feature-registry")
    registry.add_argument(
        "--set", choices=["ALL", "HISTORY_SAFE_V1", "FORWARD_SHADOW_V1"], default="ALL"
    )

    dataset = commands.add_parser("build-dataset")
    dataset.add_argument("--symbols", required=True, help="comma-separated normalized symbols")
    dataset.add_argument("--date-from", type=_date, required=True)
    dataset.add_argument("--date-to", type=_date, required=True)
    dataset.add_argument(
        "--cutoff-policy",
        choices=[item.value for item in DatasetCutoffPolicy],
        default=DatasetCutoffPolicy.EOD.value,
    )
    dataset.add_argument(
        "--record-source",
        choices=[item.value for item in RecordSource],
        default=RecordSource.REAL.value,
    )
    dataset.add_argument("--history-ready-only", action="store_true")
    dataset.add_argument("--persist", action="store_true")

    commands.add_parser("shadow-stats")
    commands.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository = SentimentV3Repository(args.database)
    assembler = ResearchFeatureAssembler(repository=repository)
    if args.command == "assemble":
        packet = assembler.assemble(
            symbol=args.symbol,
            decision_time=args.cutoff,
            cutoff=args.cutoff,
            trade_date=args.trade_date,
            record_source=RecordSource(args.record_source),
            packet_type=PacketType(args.packet_type),
            include_history_ready_only=args.history_ready_only,
            persist=args.persist,
        )
        _print(packet)
        return 0
    if args.command == "show":
        _print(repository.get_feature_packet(args.packet_id))
        return 0
    if args.command == "feature-registry":
        definitions = FEATURE_REGISTRY_V1.definitions
        if args.set == "HISTORY_SAFE_V1":
            definitions = FEATURE_REGISTRY_V1.history_safe()
        elif args.set == "FORWARD_SHADOW_V1":
            definitions = FEATURE_REGISTRY_V1.forward_shadow()
        _print(
            {
                "stats": FEATURE_REGISTRY_V1.stats(),
                "definitions": [item.model_dump(mode="json") for item in definitions],
            }
        )
        return 0
    if args.command == "build-dataset":
        dataset = ResearchDatasetBuilder(assembler=assembler).build(
            symbols=[item.strip() for item in args.symbols.split(",") if item.strip()],
            date_from=args.date_from,
            date_to=args.date_to,
            cutoff_policy=DatasetCutoffPolicy(args.cutoff_policy),
            record_source=RecordSource(args.record_source),
            include_history_ready_only=args.history_ready_only,
            persist_packets=args.persist,
            persist_dataset=args.persist,
        )
        _print(dataset)
        return 0
    if args.command == "shadow-stats":
        _print(repository.shadow_stats())
        return 0
    if args.command == "validate":
        errors = []
        for packet in repository.list_feature_packets(limit=100_000):
            if any(
                value.available_time > packet.cutoff for value in packet.feature_lineage.values()
            ):
                errors.append({"packet_id": packet.packet_id, "error": "PIT_VIOLATION"})
        _print({"valid": not errors, "errors": errors})
        return 0 if not errors else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
