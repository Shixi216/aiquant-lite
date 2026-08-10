from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from trading.scanner.feature_loader import ScannerFeatureLoader
from trading.scanner.schemas import (
    ScannerEvaluationRequest,
    ScannerParseRequest,
    ScannerScanRequest,
    ScannerScanResponse,
)
from trading.scanner.service import MarketScannerService
from trading.schemas import AnalysisMode


def _json(value: Any) -> str:
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json(indent=2)
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hermes-OPC deterministic whole-market scanner"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    parse = sub.add_parser("parse", aliases=["parse_scanner_query"])
    parse.add_argument("query")
    scan = sub.add_parser("scan", aliases=["run_market_scanner"])
    scan.add_argument("--query", required=True)
    scan.add_argument("--top-n", type=int, default=20, choices=(10, 20, 30))
    scan.add_argument("--persist-run", action="store_true")
    scan.add_argument("--force-refresh", action="store_true", help="强制批量刷新快照后再扫描")
    show = sub.add_parser("show", aliases=["show_scanner_run"])
    show.add_argument("run_id")
    export = sub.add_parser("export", aliases=["export_scanner_candidates"])
    export.add_argument("run_id")
    export.add_argument("--output", type=Path, required=True)
    evaluate = sub.add_parser("evaluate", aliases=["evaluate_scanner_run"])
    evaluate.add_argument("run_id")
    evaluate.add_argument("--persist", action="store_true")
    benchmark = sub.add_parser(
        "benchmark",
        aliases=["benchmark_market_scanner"],
    )
    benchmark.add_argument("--query", default="全A股前20只")
    benchmark.add_argument("--top-n", type=int, default=20, choices=(10, 20, 30))
    return parser


def _scan_request(args: argparse.Namespace) -> ScannerScanRequest:
    return ScannerScanRequest(
        query=args.query,
        analysis_mode=AnalysisMode.SCREENING,
        data_cutoff=datetime.now().astimezone(),
        top_n=args.top_n,
        persist_run=getattr(args, "persist_run", False),
    )


def main() -> None:
    args = _parser().parse_args()
    service = MarketScannerService()
    command = args.command
    if command in {"parse", "parse_scanner_query"}:
        result = service.parse(
            ScannerParseRequest(
                query=args.query,
                data_cutoff=datetime.now().astimezone(),
            )
        )
        print(_json(result))
        return
    if command in {"scan", "run_market_scanner"}:
        # 规则1+2+5：扫描前强制检查数据新鲜度，过期自动批量刷新，失败停止推荐
        from trading.scanner.freshness_guard import ScannerFreshnessGuard

        guard = ScannerFreshnessGuard()
        cutoff = datetime.now().astimezone()
        check = guard.check(cutoff, force_refresh=getattr(args, "force_refresh", False))
        if not check.fresh:
            # 规则5：刷新失败 → 只返回"数据陈旧"，不推荐
            print(_json({
                "error": "DATA_STALE",
                "reason": check.reason,
                "refresh_failed": check.refresh_failed,
                "refresh_error": check.refresh_error,
                "latest_trade_date": check.latest_trade_date,
                "snapshot_id": check.snapshot_id,
                "data_cutoff": cutoff.isoformat(),
                "message": "数据陈旧，已停止推荐。请等待快照刷新完成或稍后重试。",
            }))
            return
        result = service.scan(_scan_request(args))
        print(_json(result))
        return
    if command in {"show", "show_scanner_run"}:
        result = service.run_detail(args.run_id)
        if result is None:
            raise SystemExit("scanner run not found")
        print(_json(result))
        return
    if command in {"export", "export_scanner_candidates"}:
        rows = service.run_candidates(args.run_id)
        if args.output.suffix.lower() == ".csv":
            with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(
                    stream,
                    fieldnames=[
                        "rank",
                        "symbol",
                        "short_name",
                        "scanner_score",
                        "factor_coverage",
                        "research_status",
                        "is_trade_recommendation",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    {key: row.get(key) for key in writer.fieldnames}
                    for row in rows
                )
        else:
            args.output.write_text(_json(rows), encoding="utf-8")
        print(str(args.output.resolve()))
        return
    if command in {"evaluate", "evaluate_scanner_run"}:
        print(
            _json(
                service.evaluate(
                    ScannerEvaluationRequest(
                        run_id=args.run_id,
                        persist=args.persist,
                    )
                )
            )
        )
        return
    if command in {"benchmark", "benchmark_market_scanner"}:
        ScannerFeatureLoader.clear_cache()
        request = _scan_request(args)
        cold = service.scan(request)
        warm = service.scan(request)
        if not isinstance(cold, ScannerScanResponse) or not isinstance(
            warm,
            ScannerScanResponse,
        ):
            raise SystemExit("benchmark query requires clarification")
        print(
            _json(
                {
                    "symbols": cold.universe_count,
                    "cold_ms": cold.performance.elapsed_ms,
                    "warm_ms": warm.performance.elapsed_ms,
                    "cold_peak_memory_bytes": cold.performance.peak_memory_bytes,
                    "warm_peak_memory_bytes": warm.performance.peak_memory_bytes,
                    "cold_database_queries": (
                        cold.performance.database_query_count
                    ),
                    "warm_database_queries": (
                        warm.performance.database_query_count
                    ),
                    "network_request_count": 0,
                    "model_call_count": 0,
                    "is_trade_recommendation": False,
                }
            )
        )


if __name__ == "__main__":
    main()
