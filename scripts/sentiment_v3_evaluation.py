from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from trading.research.sentiment.v3.evaluation import EvaluationConfig
from trading.research.sentiment.v3.label_repository import SentimentV3EvaluationRepository
from trading.research.sentiment.v3.labels import (
    DuckDBLocalPriceSource,
    ForwardLabelBuilder,
    label_coverage_report,
    label_specs_v1,
)
from trading.research.sentiment.v3.schemas import RecordSource


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Sentiment V3 S3A local-only label/evaluation research CLI"
    )
    value.add_argument("--database", type=Path)
    commands = value.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build-labels")
    build.add_argument("--packet-id", action="append", required=True)
    build.add_argument("--build-as-of", required=True)
    build.add_argument("--label-name", action="append")
    build.add_argument("--persist", action="store_true")
    build.add_argument(
        "--record-source", choices=[item.value for item in RecordSource], default="REAL"
    )
    validate = commands.add_parser("validate-labels")
    validate.add_argument("--json", action="store_true")
    stats = commands.add_parser("label-stats")
    stats.add_argument(
        "--record-source", choices=[item.value for item in RecordSource], default="REAL"
    )
    stats.add_argument("--limit", type=int, default=10000)
    commands.add_parser("evaluation-config")
    commands.add_parser("validate-evaluator")
    return value


def _repository(path: Path | None) -> SentimentV3EvaluationRepository:
    return SentimentV3EvaluationRepository(path)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "validate-labels":
        specs = label_specs_v1()
        payload = {
            "label_spec_count": len(specs),
            "horizons": sorted({item.horizon for item in specs}),
            "statistical": [item.label_name for item in specs if not item.execution_realistic],
            "executable": [item.label_name for item in specs if item.execution_realistic],
            "external_api_calls": 0,
            "llm_calls": 0,
            "alpha_conclusion": "NONE",
        }
        print(_json(payload) if args.json else payload)
        return 0
    if args.command in {"evaluation-config", "validate-evaluator"}:
        payload = EvaluationConfig().model_dump(mode="json")
        payload.update(
            {
                "validated_with": "TEST/SYNTHETIC_ONLY",
                "features_evaluated": 0,
                "alpha_conclusion": "NONE",
                "external_api_calls": 0,
                "llm_calls": 0,
            }
        )
        print(_json(payload))
        return 0
    repo = _repository(args.database)
    if args.command == "label-stats":
        labels = repo.list_forward_labels(
            record_source=RecordSource(args.record_source), limit=args.limit
        )
        print(_json(label_coverage_report(labels)))
        return 0
    packets = []
    for packet_id in args.packet_id:
        item = repo.get_feature_packet(packet_id)
        if item is None:
            raise SystemExit(f"feature packet not found: {packet_id}")
        if item.record_source != RecordSource(args.record_source):
            raise SystemExit(f"record_source mismatch: {packet_id}")
        packets.append(item)
    specs = list(label_specs_v1())
    if args.label_name:
        requested = set(args.label_name)
        specs = [item for item in specs if item.label_name in requested]
        missing = requested - {item.label_name for item in specs}
        if missing:
            raise SystemExit(f"unknown label names: {sorted(missing)}")
    build_as_of = datetime.fromisoformat(args.build_as_of)
    result = ForwardLabelBuilder(
        price_source=DuckDBLocalPriceSource(connection_factory=repo._connection)
    ).build_batch(packets, specs, build_as_of=build_as_of)
    if args.persist:
        repo.save_forward_labels(result.labels)
    print(
        _json(
            {
                "metrics": result.metrics,
                "coverage": label_coverage_report(result.labels),
                "persisted": bool(args.persist),
                "external_api_calls": 0,
                "llm_calls": 0,
                "alpha_conclusion": "NONE",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
