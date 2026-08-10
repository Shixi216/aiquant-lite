from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from config.settings import PROJECT_ROOT, settings
from database.db import get_connection, initialize_database
from trading.research.sentiment.aggregator import aggregate_symbol
from trading.research.sentiment.repository import SentimentRepository
from trading.research.sentiment.schemas import (
    SentimentRiskFlag,
)
from trading.research.sentiment.service import SentimentAnalysisService
from trading.schemas import AnalysisMode


REPORT_VERSION = "sentiment-backfill-v1"


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError(
            "timestamps must include a timezone"
        )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill existing event_clusters into shadow sentiment analyses. "
            "The default mode is dry-run."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--event-type")
    parser.add_argument("--symbol")
    parser.add_argument("--start-time", type=_time)
    parser.add_argument("--end-time", type=_time)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--resume",
        nargs="?",
        const="latest",
        metavar="BACKFILL_RUN_ID",
    )
    parser.add_argument("--skip-model-calls", action="store_true")
    parser.add_argument(
        "--enable-model-calls",
        action="store_true",
        help=(
            "Explicitly enable a small, configured model-call budget. "
            "Without this flag, only deterministic local rules run."
        ),
    )
    parser.add_argument("--report-path", type=Path)
    return parser


def _counts() -> dict[str, int]:
    tables = (
        "data_records",
        "event_clusters",
        "event_source_links",
        "sentiment_event_analyses",
        "sentiment_symbol_snapshots",
        "sentiment_market_snapshots",
    )
    with get_connection() as connection:
        return {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in tables
        }


def _duplicate_audit() -> tuple[set[str], list[dict[str, Any]], str | None]:
    report_dir = PROJECT_ROOT / "reports" / "backfill"
    reports = sorted(
        report_dir.glob("*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in reports:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        duplicates = payload.get("suspected_duplicate_events")
        if not isinstance(duplicates, list):
            continue
        event_ids = {
            str(item.get(key))
            for item in duplicates
            if isinstance(item, dict)
            for key in (
                "first_event_cluster_id",
                "second_event_cluster_id",
            )
            if str(item.get(key) or "").startswith("evt_")
        }
        return event_ids, duplicates, str(path)
    return set(), [], None


def _resolve_report_paths(
    requested: Path | None,
    *,
    run_id: str,
) -> tuple[Path, Path]:
    if requested is None:
        directory = PROJECT_ROOT / "reports" / "sentiment"
        json_path = directory / f"{run_id}.json"
    elif requested.suffix.casefold() == ".json":
        json_path = requested
    else:
        json_path = requested / f"{run_id}.json"
    if not json_path.is_absolute():
        json_path = PROJECT_ROOT / json_path
    return json_path, json_path.with_suffix(".md")


def _write_report(
    report: dict[str, Any],
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    lines = [
        "# Hermes-OPC 情绪面历史回填审计",
        "",
        f"- 运行 ID：`{report['backfill_run_id']}`",
        f"- 模式：`{report['mode']}`",
        f"- 状态：`{report['status']}`",
        f"- 事件簇总数：{report['event_cluster_count_before']}",
        f"- 本次选择：{report['selected_event_count']}",
        f"- 已处理：{report['processed_count']}",
        f"- 新增成功：{report['success_count']}",
        f"- 幂等跳过：{report['skipped_count']}",
        f"- 失败：{report['failed_count']}",
        f"- 模型调用：{report['model_call_count']}",
        f"- 疑似重复组：{len(report['suspected_duplicate_events'])}",
        "",
        "## 说明",
        "",
        "- 所有结果均为 `shadow_mode=true`。",
        "- 疑似重复事件只做风险标记，没有自动合并。",
        "- 未启用模型时使用本地确定性事件规则安全降级。",
        "- 本报告不表示预测能力或投资收益。",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resume_state(
    resume: str | None,
) -> tuple[str | None, set[str]]:
    if resume is None:
        return None, set()
    with get_connection() as connection:
        if resume == "latest":
            row = connection.execute(
                """
                SELECT backfill_run_id
                FROM sentiment_backfill_runs
                WHERE status IN ('RUNNING', 'FAILED')
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None, set()
            run_id = row[0]
        else:
            run_id = resume
        completed = {
            row[0]
            for row in connection.execute(
                """
                SELECT event_cluster_id
                FROM sentiment_backfill_items
                WHERE backfill_run_id = ?
                  AND status IN ('SUCCESS', 'SKIPPED')
                """,
                [run_id],
            ).fetchall()
        }
    return run_id, completed


def _record_run_start(
    *,
    run_id: str,
    filters: dict[str, Any],
    started_at: datetime,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO sentiment_backfill_runs (
                backfill_run_id, mode, filters_json, status,
                processed_count, success_count, skipped_count,
                failed_count, model_call_count, started_at
            )
            VALUES (?, 'APPLY', ?, 'RUNNING', 0, 0, 0, 0, 0, ?)
            ON CONFLICT (backfill_run_id) DO UPDATE SET
                status = 'RUNNING'
            """,
            [
                run_id,
                json.dumps(filters, ensure_ascii=False, default=str),
                started_at,
            ],
        )


def _record_item(
    *,
    run_id: str,
    event_id: str,
    status: str,
    analysis_id: str | None,
    error: Exception | None,
) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO sentiment_backfill_items (
                    backfill_run_id, event_cluster_id, status,
                    sentiment_analysis_id, error_type, error_message,
                    processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    backfill_run_id, event_cluster_id
                ) DO UPDATE SET
                    status = EXCLUDED.status,
                    sentiment_analysis_id = EXCLUDED.sentiment_analysis_id,
                    error_type = EXCLUDED.error_type,
                    error_message = EXCLUDED.error_message,
                    processed_at = EXCLUDED.processed_at
                """,
                [
                    run_id,
                    event_id,
                    status,
                    analysis_id,
                    type(error).__name__ if error is not None else None,
                    str(error)[:1000] if error is not None else None,
                    datetime.now().astimezone(),
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def _record_run_finish(
    *,
    run_id: str,
    report: dict[str, Any],
    report_path: Path,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE sentiment_backfill_runs
            SET status = ?, processed_count = ?, success_count = ?,
                skipped_count = ?, failed_count = ?,
                model_call_count = ?, completed_at = ?,
                report_path = ?
            WHERE backfill_run_id = ?
            """,
            [
                report["status"],
                report["processed_count"],
                report["success_count"],
                report["skipped_count"],
                report["failed_count"],
                report["model_call_count"],
                report["completed_at"],
                str(report_path),
                run_id,
            ],
        )


async def run_backfill(
    args: argparse.Namespace,
) -> dict[str, Any]:
    apply = bool(args.apply)
    initialize_database()
    repository = SentimentRepository()
    service = SentimentAnalysisService(repository=repository)
    started_at = datetime.now().astimezone()
    resume_id, completed_ids = _resume_state(args.resume) if apply else (None, set())
    run_id = resume_id or (
        "sentiment_backfill_"
        + started_at.strftime("%Y%m%dT%H%M%S")
        + "_"
        + uuid4().hex[:8]
    )
    filters = {
        "event_type": args.event_type,
        "symbol": args.symbol,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "limit": args.limit,
        "resume": args.resume,
        "skip_model_calls": args.skip_model_calls,
        "enable_model_calls": args.enable_model_calls,
    }
    before = _counts()
    duplicate_ids, suspected_duplicates, duplicate_report = _duplicate_audit()
    cluster_filter = (
        args.event_type
        if args.event_type in {"announcement", "finance_news"}
        else None
    )
    bundles = repository.list_event_bundles(
        data_cutoff=started_at,
        symbol=args.symbol,
        start_time=args.start_time,
        event_type=cluster_filter,
        limit=args.limit,
        strict_point_in_time=True,
    )
    if args.end_time is not None:
        bundles = [
            bundle
            for bundle in bundles
            if bundle.event_time <= args.end_time
        ]
    if apply:
        _record_run_start(
            run_id=run_id,
            filters=filters,
            started_at=started_at,
        )

    success = 0
    skipped = 0
    failed = 0
    processed = 0
    model_calls = 0
    failures: list[dict[str, str]] = []
    statuses: Counter[str] = Counter()
    analyses_by_symbol: dict[str, list[Any]] = {}
    budget = settings.sentiment_backfill_model_call_budget
    explicit_models = (
        args.enable_model_calls
        and not args.skip_model_calls
        and budget > 0
    )
    for bundle in bundles:
        if bundle.event_cluster_id in completed_ids:
            skipped += 1
            processed += 1
            statuses["RESUME_SKIPPED"] += 1
            continue
        try:
            allow_model = explicit_models and model_calls < budget
            extra_flags = (
                [SentimentRiskFlag.DUPLICATE_SUSPECTED]
                if bundle.event_cluster_id in duplicate_ids
                else []
            )
            analysis = await service.analyze_event(
                bundle,
                data_cutoff=max(bundle.data_cutoff, bundle.event_time),
                allow_model_calls=allow_model,
                persist=False,
                extra_risk_flags=extra_flags,
            )
            if (
                args.event_type
                and args.event_type not in {"announcement", "finance_news"}
                and analysis.event_type.value != args.event_type
            ):
                skipped += 1
                statuses["FILTERED"] += 1
                processed += 1
                continue
            model_calls += len(analysis.model_call_ids)
            existing = repository.get_analysis(
                analysis.sentiment_analysis_id
            )
            if existing is not None:
                skipped += 1
                item_status = "SKIPPED"
                persisted = existing
            else:
                success += 1
                item_status = "SUCCESS"
                persisted = (
                    repository.save_analysis(analysis)
                    if apply
                    else analysis
                )
            statuses[persisted.verification_status.value] += 1
            for symbol in persisted.affected_symbols:
                analyses_by_symbol.setdefault(symbol, []).append(persisted)
            if apply:
                _record_item(
                    run_id=run_id,
                    event_id=bundle.event_cluster_id,
                    status=item_status,
                    analysis_id=persisted.sentiment_analysis_id,
                    error=None,
                )
        except Exception as exc:
            failed += 1
            statuses["FAILED"] += 1
            failures.append(
                {
                    "event_cluster_id": bundle.event_cluster_id,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:1000],
                }
            )
            if apply:
                _record_item(
                    run_id=run_id,
                    event_id=bundle.event_cluster_id,
                    status="FAILED",
                    analysis_id=None,
                    error=exc,
                )
        processed += 1

    snapshot_count = 0
    if apply:
        stable_snapshot_cutoff = max(
            (
                max(bundle.data_cutoff, bundle.event_time)
                for bundle in bundles
            ),
            default=started_at,
        )
        for symbol in sorted(analyses_by_symbol):
            all_analyses = repository.list_analyses(
                symbol=symbol,
                data_cutoff=stable_snapshot_cutoff,
            )
            snapshot = aggregate_symbol(
                symbol=symbol,
                analyses=all_analyses,
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=stable_snapshot_cutoff,
                generated_at=datetime.now().astimezone(),
            )
            repository.save_symbol_snapshot(snapshot)
            snapshot_count += 1

    completed_at = datetime.now().astimezone()
    after = _counts()
    json_path, markdown_path = _resolve_report_paths(
        args.report_path,
        run_id=run_id,
    )
    report = {
        "report_version": REPORT_VERSION,
        "backfill_run_id": run_id,
        "mode": "APPLY" if apply else "DRY_RUN",
        "status": "FAILED" if failed else "COMPLETED",
        "started_at": started_at,
        "completed_at": completed_at,
        "filters": filters,
        "raw_data_record_count_before": before["data_records"],
        "raw_data_record_count_after": after["data_records"],
        "event_cluster_count_before": before["event_clusters"],
        "event_cluster_count_after": after["event_clusters"],
        "event_source_link_count_before": before["event_source_links"],
        "event_source_link_count_after": after["event_source_links"],
        "selected_event_count": len(bundles),
        "processed_count": processed,
        "success_count": success,
        "skipped_count": skipped,
        "failed_count": failed,
        "model_call_count": model_calls,
        "sentiment_event_analysis_count_before": (
            before["sentiment_event_analyses"]
        ),
        "sentiment_event_analysis_count_after": (
            after["sentiment_event_analyses"]
        ),
        "sentiment_symbol_snapshot_count_before": (
            before["sentiment_symbol_snapshots"]
        ),
        "sentiment_symbol_snapshot_count_after": (
            after["sentiment_symbol_snapshots"]
        ),
        "snapshots_generated_for_symbols": snapshot_count,
        "status_counts": dict(sorted(statuses.items())),
        "failed_events": failures,
        "suspected_duplicate_events": suspected_duplicates,
        "suspected_duplicate_source_report": duplicate_report,
        "automatic_duplicate_merge_count": 0,
        "shadow_mode": True,
        "formal_strategy_changed": False,
        "report_json_path": str(json_path),
        "report_markdown_path": str(markdown_path),
    }
    _write_report(
        report,
        json_path=json_path,
        markdown_path=markdown_path,
    )
    if apply:
        _record_run_finish(
            run_id=run_id,
            report=report,
            report_path=json_path,
        )
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.enable_model_calls and args.skip_model_calls:
        raise SystemExit(
            "--enable-model-calls and --skip-model-calls are mutually exclusive"
        )
    report = asyncio.run(run_backfill(args))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if report["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
