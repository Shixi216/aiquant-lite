from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, time
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from config.settings import PROJECT_ROOT, settings
from database.db import get_connection, initialize_database
from trading.research.policy_news.aggregator import (
    aggregate_sector,
    aggregate_symbol,
)
from trading.research.policy_news.repository import PolicyNewsRepository
from trading.research.policy_news.schemas import (
    PolicyEventCategory,
    PolicyRiskFlag,
)
from trading.research.policy_news.service import PolicyNewsAnalysisService
from trading.schemas import AnalysisMode


REPORT_VERSION = "policy-news-backfill-v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


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
            "Backfill shared event_clusters into shadow policy/news "
            "analyses. The default mode is dry-run."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--category",
        choices=[item.value for item in PolicyEventCategory],
    )
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
    model_mode = parser.add_mutually_exclusive_group()
    model_mode.add_argument(
        "--skip-model-calls",
        action="store_true",
        help="Use deterministic local extraction only (the default).",
    )
    model_mode.add_argument(
        "--enable-model-calls",
        action="store_true",
        help="Allow a small explicit model-call budget.",
    )
    parser.add_argument("--max-model-calls", type=int, default=0)
    parser.add_argument("--report-path", type=Path)
    return parser


def _counts() -> dict[str, int]:
    tables = (
        "data_records",
        "event_clusters",
        "event_source_links",
        "sentiment_event_analyses",
        "policy_news_event_analyses",
        "policy_news_symbol_snapshots",
        "policy_news_sector_snapshots",
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


def _sentiment_fingerprint() -> str:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT md5(
                string_agg(
                    sentiment_analysis_id || ':' || input_snapshot_hash,
                    '|' ORDER BY sentiment_analysis_id
                )
            )
            FROM sentiment_event_analyses
            """
        ).fetchone()
    return str(row[0] or "")


def _duplicate_audit() -> tuple[set[str], list[dict[str, Any]], str | None]:
    reports = sorted(
        (PROJECT_ROOT / "reports" / "backfill").glob("*.json"),
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
        json_path = (
            PROJECT_ROOT
            / "reports"
            / "policy-news"
            / f"{run_id}.json"
        )
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
        "# Hermes-OPC 政策与消息面历史回填审计",
        "",
        f"- 运行 ID：`{report['backfill_run_id']}`",
        f"- 模式：`{report['mode']}`",
        f"- 状态：`{report['status']}`",
        f"- 事件簇总数：{report['event_cluster_count_before']}",
        f"- 本次选择：{report['selected_event_count']}",
        f"- 已处理：{report['processed_count']}",
        f"- 政策分析新增：{report['success_count']}",
        f"- 不适用或幂等跳过：{report['skipped_count']}",
        f"- 失败：{report['failed_count']}",
        f"- 模型调用：{report['model_call_count']}",
        f"- 股票快照：{report['symbol_snapshot_count_after']}",
        f"- 行业快照：{report['sector_snapshot_count_after']}",
        f"- 完整正文事件：{report['full_text_event_count']}",
        "",
        "## 边界",
        "",
        "- 所有政策因子均为 `shadow_mode=true`，正式权重为 0。",
        "- 未归入三类范围的事件记为 NOT_APPLICABLE，没有强行分类。",
        "- 疑似重复事件只做风险标记，没有自动合并。",
        "- 情绪分析表在回填前后保持不变。",
        "- 本报告不表示预测能力，也不表示投资收益。",
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
                FROM policy_news_backfill_runs
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
                FROM policy_news_backfill_items
                WHERE backfill_run_id = ?
                  AND status IN ('SUCCESS', 'SKIPPED', 'NOT_APPLICABLE')
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
            INSERT INTO policy_news_backfill_runs (
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
    category: str | None,
    error: Exception | None,
) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO policy_news_backfill_items (
                    backfill_run_id, event_cluster_id, status,
                    policy_analysis_id, event_category,
                    error_type, error_message, processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    backfill_run_id, event_cluster_id
                ) DO UPDATE SET
                    status = EXCLUDED.status,
                    policy_analysis_id = EXCLUDED.policy_analysis_id,
                    event_category = EXCLUDED.event_category,
                    error_type = EXCLUDED.error_type,
                    error_message = EXCLUDED.error_message,
                    processed_at = EXCLUDED.processed_at
                """,
                [
                    run_id,
                    event_id,
                    status,
                    analysis_id,
                    category,
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
            UPDATE policy_news_backfill_runs
            SET status = ?, processed_count = ?, success_count = ?,
                skipped_count = ?, failed_count = ?,
                model_call_count = ?, completed_at = ?, report_path = ?
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


def _backfill_cutoff(bundle) -> datetime:
    values = [
        bundle.event_time,
        bundle.data_cutoff,
        *(source.fetched_at for source in bundle.source_records),
    ]
    payload = bundle.primary_source.payload
    for key in ("published_at", "announcement_date", "publication_time"):
        raw = payload.get(key)
        if not raw:
            continue
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            if len(str(raw).strip()) <= 10:
                parsed = datetime.combine(
                    parsed.date(),
                    time(
                        settings.policy_news_date_only_available_hour,
                        59,
                    ),
                    tzinfo=SHANGHAI,
                )
            else:
                parsed = parsed.replace(tzinfo=SHANGHAI)
        values.append(parsed)
    return max(values)


async def run_backfill(args: argparse.Namespace) -> dict[str, Any]:
    apply = bool(args.apply)
    initialize_database()
    repository = PolicyNewsRepository()
    service = PolicyNewsAnalysisService(repository=repository)
    started_at = datetime.now().astimezone()
    resume_id, completed_ids = (
        _resume_state(args.resume) if apply else (None, set())
    )
    run_id = resume_id or (
        "policy_news_backfill_"
        + started_at.strftime("%Y%m%dT%H%M%S")
        + "_"
        + uuid4().hex[:8]
    )
    filters = {
        "category": args.category,
        "symbol": args.symbol,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "limit": args.limit,
        "resume": args.resume,
        "skip_model_calls": args.skip_model_calls,
        "enable_model_calls": args.enable_model_calls,
        "max_model_calls": args.max_model_calls,
    }
    before = _counts()
    sentiment_before = _sentiment_fingerprint()
    duplicate_ids, duplicate_pairs, duplicate_report = _duplicate_audit()
    bundles = repository.list_event_bundles(
        data_cutoff=started_at,
        symbol=args.symbol,
        start_time=args.start_time,
        limit=args.limit,
        strict_point_in_time=True,
    )
    if args.end_time is not None:
        bundles = [
            bundle for bundle in bundles if bundle.event_time <= args.end_time
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
    full_text_count = 0
    statuses: Counter[str] = Counter()
    failures: list[dict[str, str]] = []
    analyses_by_symbol: dict[str, list[Any]] = {}
    analyses_by_sector: dict[str, list[Any]] = {}
    budget = min(
        max(0, args.max_model_calls),
        settings.policy_news_backfill_model_call_budget,
    )
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
            extra_flags = (
                [PolicyRiskFlag.DUPLICATE_SUSPECTED]
                if bundle.event_cluster_id in duplicate_ids
                else []
            )
            allow_model = explicit_models and model_calls < budget
            analysis = await service.analyze_event(
                bundle,
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=_backfill_cutoff(bundle),
                allow_model_calls=allow_model,
                persist=False,
                extra_risk_flags=extra_flags,
            )
            model_calls += len(analysis.model_call_ids)
            if analysis.text_completeness.value == "FULL_TEXT":
                full_text_count += 1
            statuses[analysis.event_category.value] += 1
            if (
                args.category
                and analysis.event_category.value != args.category
            ):
                skipped += 1
                item_status = "SKIPPED"
                persisted = analysis
            elif analysis.event_category in {
                PolicyEventCategory.OTHER,
                PolicyEventCategory.NOT_APPLICABLE,
            }:
                skipped += 1
                item_status = "NOT_APPLICABLE"
                persisted = analysis
            else:
                existing = repository.get_analysis(
                    analysis.policy_analysis_id
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
                for symbol in persisted.affected_symbols:
                    analyses_by_symbol.setdefault(symbol, []).append(
                        persisted
                    )
                for sector in persisted.affected_sectors:
                    analyses_by_sector.setdefault(sector, []).append(
                        persisted
                    )
            if apply:
                _record_item(
                    run_id=run_id,
                    event_id=bundle.event_cluster_id,
                    status=item_status,
                    analysis_id=(
                        persisted.policy_analysis_id
                        if item_status in {"SUCCESS", "SKIPPED"}
                        and persisted.event_category
                        not in {
                            PolicyEventCategory.OTHER,
                            PolicyEventCategory.NOT_APPLICABLE,
                        }
                        else None
                    ),
                    category=persisted.event_category.value,
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
                    category=None,
                    error=exc,
                )
        processed += 1

    if apply:
        snapshot_cutoff = max(
            (_backfill_cutoff(bundle) for bundle in bundles),
            default=started_at,
        )
        for symbol in sorted(analyses_by_symbol):
            analyses = repository.list_analyses(
                symbol=symbol,
                data_cutoff=snapshot_cutoff,
            )
            repository.save_symbol_snapshot(
                aggregate_symbol(
                    symbol=symbol,
                    analyses=analyses,
                    analysis_mode=AnalysisMode.RESEARCH,
                    data_cutoff=snapshot_cutoff,
                    generated_at=datetime.now().astimezone(),
                )
            )
        for sector in sorted(analyses_by_sector):
            analyses = repository.list_analyses(
                sector=sector,
                data_cutoff=snapshot_cutoff,
            )
            repository.save_sector_snapshot(
                aggregate_sector(
                    sector=sector,
                    analyses=analyses,
                    analysis_mode=AnalysisMode.RESEARCH,
                    data_cutoff=snapshot_cutoff,
                    generated_at=datetime.now().astimezone(),
                )
            )

    completed_at = datetime.now().astimezone()
    after = _counts()
    sentiment_after = _sentiment_fingerprint()
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
        "sentiment_analysis_count_before": (
            before["sentiment_event_analyses"]
        ),
        "sentiment_analysis_count_after": (
            after["sentiment_event_analyses"]
        ),
        "sentiment_fingerprint_unchanged": (
            sentiment_before == sentiment_after
        ),
        "selected_event_count": len(bundles),
        "processed_count": processed,
        "success_count": success,
        "skipped_count": skipped,
        "failed_count": failed,
        "model_call_count": model_calls,
        "full_text_event_count": full_text_count,
        "policy_analysis_count_before": (
            before["policy_news_event_analyses"]
        ),
        "policy_analysis_count_after": (
            after["policy_news_event_analyses"]
        ),
        "symbol_snapshot_count_before": (
            before["policy_news_symbol_snapshots"]
        ),
        "symbol_snapshot_count_after": (
            after["policy_news_symbol_snapshots"]
        ),
        "sector_snapshot_count_before": (
            before["policy_news_sector_snapshots"]
        ),
        "sector_snapshot_count_after": (
            after["policy_news_sector_snapshots"]
        ),
        "status_counts": dict(sorted(statuses.items())),
        "failed_events": failures,
        "suspected_duplicate_events": duplicate_pairs,
        "suspected_duplicate_source_report": duplicate_report,
        "automatic_duplicate_merge_count": 0,
        "shadow_mode": True,
        "formal_strategy_weight": 0.0,
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
    if args.max_model_calls < 0:
        raise SystemExit("--max-model-calls must be non-negative")
    report = asyncio.run(run_backfill(args))
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if report["failed_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
