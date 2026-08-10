from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from config.settings import PROJECT_ROOT
from database.db import get_connection, initialize_database
from trading.research.capital_flow.repository import CapitalFlowRepository
from trading.research.capital_flow.sector_features import aggregate_sector
from trading.research.capital_flow.service import CapitalFlowAnalysisService
from trading.schemas import AnalysisMode


REPORT_VERSION = "capital-flow-backfill-v1"


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamps must include a timezone")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill canonical daily bars into deterministic shadow "
            "capital-flow snapshots. The default is dry-run."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
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
    parser.add_argument("--market-snapshot", action="store_true")
    parser.add_argument("--report-path", type=Path)
    return parser


def _counts() -> dict[str, int]:
    tables = (
        "data_records",
        "canonical_market_records",
        "canonical_financial_records",
        "sentiment_event_analyses",
        "policy_news_event_analyses",
        "capital_flow_symbol_snapshots",
        "capital_flow_sector_snapshots",
        "capital_flow_market_snapshots",
    )
    with get_connection() as connection:
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in tables
        }


def _report_paths(
    requested: Path | None,
    *,
    run_id: str,
) -> tuple[Path, Path]:
    if requested is None:
        json_path = PROJECT_ROOT / "reports" / "capital-flow" / f"{run_id}.json"
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
        "# Hermes-OPC 资金面 v1 历史回填审计",
        "",
        f"- 运行 ID：`{report['backfill_run_id']}`",
        f"- 模式：`{report['mode']}`",
        f"- 状态：`{report['status']}`",
        f"- 选择股票数：{report['selected_symbol_count']}",
        f"- 处理成功：{report['success_count']}",
        f"- 新增快照：{report['created_snapshot_count']}",
        f"- 幂等跳过：{report['skipped_count']}",
        f"- 数据缺失：{report['missing_count']}",
        f"- 失败：{report['failed_count']}",
        f"- 回填后股票快照：{report['counts_after']['capital_flow_symbol_snapshots']}",
        f"- 市场样本股票数：{report['sample_universe_size']}",
        "",
        "## 边界",
        "",
        "- 所有资金面记录固定 `shadow_mode=true`，正式策略权重为 0。",
        "- 当前样本不是全 A 股，市场快照固定标记 PARTIAL_UNIVERSE。",
        "- 当前融资融券覆盖为 0；缺失值没有填 0。",
        "- 没有使用所谓“主力资金”估算字段。",
        "- 回填不调用 LLM，不表示预测能力或投资收益。",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _resume_state(resume: str | None) -> tuple[str | None, set[str]]:
    if resume is None:
        return None, set()
    with get_connection() as connection:
        if resume == "latest":
            row = connection.execute(
                """
                SELECT backfill_run_id
                FROM capital_flow_backfill_runs
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
                SELECT symbol
                FROM capital_flow_backfill_items
                WHERE backfill_run_id = ?
                  AND status IN ('SUCCESS', 'SKIPPED', 'MISSING')
                """,
                [run_id],
            ).fetchall()
        }
    return run_id, completed


def _record_run_start(
    run_id: str,
    *,
    filters: dict[str, Any],
    started_at: datetime,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO capital_flow_backfill_runs (
                backfill_run_id, mode, filters_json, status,
                processed_count, success_count, skipped_count,
                missing_count, failed_count, started_at
            )
            VALUES (?, 'APPLY', ?, 'RUNNING', 0, 0, 0, 0, 0, ?)
            ON CONFLICT DO NOTHING
            """,
            [run_id, json.dumps(filters, default=str), started_at],
        )


def _record_item(
    *,
    run_id: str,
    symbol: str,
    data_cutoff: datetime | None,
    status: str,
    snapshot_id: str | None,
    error: Exception | None = None,
) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO capital_flow_backfill_items (
                    backfill_run_id, symbol, data_cutoff, status,
                    snapshot_id, error_type, error_message, processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (backfill_run_id, symbol) DO UPDATE SET
                    data_cutoff = excluded.data_cutoff,
                    status = excluded.status,
                    snapshot_id = excluded.snapshot_id,
                    error_type = excluded.error_type,
                    error_message = excluded.error_message,
                    processed_at = excluded.processed_at
                """,
                [
                    run_id,
                    symbol,
                    data_cutoff,
                    status,
                    snapshot_id,
                    type(error).__name__ if error else None,
                    str(error)[:500] if error else None,
                    datetime.now().astimezone(),
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def run(args: argparse.Namespace) -> dict[str, Any]:
    initialize_database()
    repository = CapitalFlowRepository()
    service = CapitalFlowAnalysisService(repository=repository)
    mode = "APPLY" if args.apply else "DRY_RUN"
    started_at = datetime.now().astimezone()
    resume_run_id, completed_symbols = _resume_state(args.resume)
    run_id = resume_run_id or f"cfb_{uuid4().hex}"
    filters = {
        "symbol": args.symbol,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "limit": args.limit,
        "market_snapshot": args.market_snapshot,
        "resume": args.resume,
    }
    counts_before = _counts()
    provisional_cutoff = args.end_time or started_at
    bars_by_symbol = repository.market_bars(data_cutoff=provisional_cutoff)
    cutoff = args.end_time or max(
        (
            bar.data_cutoff
            for bars in bars_by_symbol.values()
            for bar in bars
        ),
        default=started_at,
    )
    if cutoff != provisional_cutoff:
        bars_by_symbol = repository.market_bars(data_cutoff=cutoff)
    selected_symbols = sorted(bars_by_symbol)
    if args.symbol:
        selected_symbols = [
            symbol for symbol in selected_symbols if symbol == args.symbol
        ]
    if args.start_time:
        selected_symbols = [
            symbol
            for symbol in selected_symbols
            if max(bar.event_time for bar in bars_by_symbol[symbol])
            >= args.start_time
        ]
    if args.limit is not None:
        selected_symbols = selected_symbols[: max(args.limit, 0)]
    selected_symbols = [
        symbol for symbol in selected_symbols if symbol not in completed_symbols
    ]
    snapshots, market = service.calculate_batch(
        bars_by_symbol=bars_by_symbol,
        analysis_mode=AnalysisMode.RESEARCH,
        data_cutoff=cutoff,
        symbols=selected_symbols,
    )
    existing_ids: set[str] = set()
    if snapshots:
        with get_connection() as connection:
            placeholders = ",".join("?" for _ in snapshots)
            existing_ids = {
                row[0]
                for row in connection.execute(
                    f"""
                    SELECT snapshot_id
                    FROM capital_flow_symbol_snapshots
                    WHERE snapshot_id IN ({placeholders})
                    """,
                    [snapshot.snapshot_id for snapshot in snapshots],
                ).fetchall()
            }
    success = skipped = missing = failed = created = 0
    sector_created = 0
    failures: list[dict[str, str]] = []
    if mode == "APPLY":
        _record_run_start(run_id, filters=filters, started_at=started_at)
        for snapshot in snapshots:
            try:
                repository.save_symbol_snapshot(snapshot)
                if snapshot.snapshot_id in existing_ids:
                    skipped += 1
                    status = "SKIPPED"
                else:
                    success += 1
                    created += 1
                    status = "SUCCESS"
                _record_item(
                    run_id=run_id,
                    symbol=snapshot.symbol,
                    data_cutoff=snapshot.data_cutoff,
                    status=status,
                    snapshot_id=snapshot.snapshot_id,
                )
            except Exception as exc:
                failed += 1
                failures.append(
                    {
                        "symbol": snapshot.symbol,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                _record_item(
                    run_id=run_id,
                    symbol=snapshot.symbol,
                    data_cutoff=snapshot.data_cutoff,
                    status="FAILED",
                    snapshot_id=None,
                    error=exc,
                )
        sectors = sorted(
            {
                bars[-1].sector
                for bars in bars_by_symbol.values()
                if bars and bars[-1].sector
            }
        )
        for sector in sectors:
            sector_snapshot = aggregate_sector(
                sector=sector,
                bars_by_symbol=bars_by_symbol,
                market_total_amount=market.market_total_amount,
                expected_symbols={
                    symbol
                    for symbol, bars in bars_by_symbol.items()
                    if bars and bars[-1].sector == sector
                },
                analysis_mode=AnalysisMode.RESEARCH,
                data_cutoff=cutoff,
            )
            if sector_snapshot is not None:
                before = _counts()["capital_flow_sector_snapshots"]
                repository.save_sector_snapshot(sector_snapshot)
                after = _counts()["capital_flow_sector_snapshots"]
                sector_created += max(0, after - before)
        if args.market_snapshot:
            repository.save_market_snapshot(market)
    else:
        success = len(snapshots)
    missing = len(selected_symbols) - len(snapshots)
    counts_after = _counts()
    status = "FAILED" if failed else "COMPLETED"
    completed_at = datetime.now().astimezone()
    json_path, markdown_path = _report_paths(args.report_path, run_id=run_id)
    report = {
        "report_version": REPORT_VERSION,
        "backfill_run_id": run_id,
        "mode": mode,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "filters": filters,
        "counts_before": counts_before,
        "counts_after": counts_after,
        "selected_symbol_count": len(selected_symbols),
        "processed_count": len(snapshots),
        "success_count": success,
        "created_snapshot_count": created,
        "created_sector_snapshot_count": sector_created,
        "skipped_count": skipped,
        "missing_count": missing,
        "failed_count": failed,
        "sample_universe_size": market.sample_universe_size,
        "partial_universe": market.partial_universe,
        "financing_symbol_coverage": 0,
        "estimated_flow_used": False,
        "model_call_count": 0,
        "market_snapshot_requested": args.market_snapshot,
        "market_snapshot_id": (
            market.snapshot_id if args.market_snapshot else None
        ),
        "failures": failures,
        "json_report_path": str(json_path),
        "markdown_report_path": str(markdown_path),
    }
    _write_report(report, json_path=json_path, markdown_path=markdown_path)
    if mode == "APPLY":
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE capital_flow_backfill_runs
                SET status = ?, processed_count = ?, success_count = ?,
                    skipped_count = ?, missing_count = ?, failed_count = ?,
                    completed_at = ?, report_path = ?
                WHERE backfill_run_id = ?
                """,
                [
                    status,
                    len(snapshots),
                    success,
                    skipped,
                    missing,
                    failed,
                    completed_at,
                    str(json_path),
                    run_id,
                ],
            )
    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run(args)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if report["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
