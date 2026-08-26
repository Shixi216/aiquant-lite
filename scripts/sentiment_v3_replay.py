from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from trading.research.sentiment.v3.historical_replay import (
    HistoricalReplayStore,
    SourceKind,
    TushareReplayBackfill,
    build_availability_manifest,
    build_research_set,
    default_replay_database,
)
from trading.research.sentiment.v3.historical_replay_build import (
    HistoricalReplayMaterializer,
    replay_database_size,
)


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Sentiment V3 S3B.0 local REPLAY builder")
    value.add_argument("--database", type=Path, default=default_replay_database())
    commands = value.add_subparsers(dest="command", required=True)
    commands.add_parser("audit")
    commands.add_parser("estimate")
    backfill = commands.add_parser("backfill-sources")
    backfill.add_argument("--source", action="append", choices=[item.value for item in SourceKind])
    backfill.add_argument("--force-date", action="append", type=_date, default=[])
    build = commands.add_parser("build-dataset")
    build.add_argument("--chunk-size", type=int, default=10)
    build.add_argument("--force-date", action="append", type=_date, default=[])
    commands.add_parser("stats")
    commands.add_parser("validate")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = HistoricalReplayStore(args.database)
    manifest = build_availability_manifest(store)
    research_set = build_research_set(manifest)
    if args.command == "audit":
        print(_json({"manifest": manifest, "research_set": research_set}))
        return 0
    materializer = HistoricalReplayMaterializer(store)
    if args.command == "estimate":
        print(_json(materializer.estimate(research_set)))
        return 0
    if args.command == "backfill-sources":
        date_from, date_to, *_ = store.raw_range()
        worker = TushareReplayBackfill(store)
        reference = worker.bootstrap_reference_data(start=date_from, end=date_to)
        with store.connection() as connection:
            store.attach_source(connection)
            dates = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT trade_date FROM source.canonical_historical_bars
                    WHERE adjustment_type='RAW' AND verification_status<>'CONFLICT'
                    ORDER BY trade_date
                    """
                ).fetchall()
            ]
        sources = (
            [SourceKind(item) for item in args.source]
            if args.source
            else [
                SourceKind.DAILY_REFERENCE,
                SourceKind.DAILY_BASIC,
                SourceKind.STK_LIMIT,
                SourceKind.MARGIN,
                SourceKind.DRAGON_TIGER,
            ]
        )
        metrics = worker.backfill(
            dates=dates,
            sources=sources,
            force_dates=args.force_date,
        )
        final_manifest = build_availability_manifest(store)
        print(
            _json(
                {
                    "reference": reference,
                    "metrics": metrics,
                    "manifest": final_manifest,
                    "research_set": build_research_set(final_manifest),
                }
            )
        )
        return 0
    if args.command == "build-dataset":
        if any(item.replay_status.value == "REPLAY_SOURCE_BACKFILLABLE" for item in manifest.features):
            raise SystemExit("historical sources are incomplete; run backfill-sources first")
        dataset_id, metrics = materializer.build(
            research_set,
            registry_sha=manifest.registry_file_sha256,
            label_spec_sha=manifest.label_spec_sha256,
            evaluation_sha=manifest.evaluation_sha256,
            chunk_size=args.chunk_size,
            force_dates=args.force_date,
        )
        print(_json({"dataset_id": dataset_id, "metrics": metrics.__dict__}))
        return 0
    if args.command in {"stats", "validate"}:
        with store.connection() as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                ).fetchall()
            }
            payload = {
                "database": str(store.replay_database),
                "database_size": replay_database_size(store.replay_database),
                "packet_count": connection.execute(
                    "SELECT COUNT(*) FROM replay_feature_packets"
                ).fetchone()[0]
                if "replay_feature_packets" in tables
                else 0,
                "label_rows": connection.execute(
                    "SELECT COUNT(*) FROM replay_labels_wide"
                ).fetchone()[0]
                if "replay_labels_wide" in tables
                else 0,
                "external_api_calls": 0,
                "llm_calls": 0,
                "alpha_conclusion": "NONE",
            }
            if args.command == "validate" and payload["packet_count"]:
                payload["pit_leakage_count"] = connection.execute(
                    """
                    SELECT COUNT(*) FROM replay_feature_packets
                    WHERE market_available_time>decision_cutoff
                       OR stock_available_time>decision_cutoff
                       OR margin_available_time>decision_cutoff
                       OR dragon_tiger_available_time>decision_cutoff
                       OR date_part('hour',decision_cutoff AT TIME ZONE 'Asia/Shanghai')>9
                       OR (date_part('hour',decision_cutoff AT TIME ZONE 'Asia/Shanghai')=9
                           AND date_part('minute',decision_cutoff AT TIME ZONE 'Asia/Shanghai')>=30)
                    """
                ).fetchone()[0]
        print(_json(payload))
        return 0 if payload.get("pit_leakage_count", 0) == 0 else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
