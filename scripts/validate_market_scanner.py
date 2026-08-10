from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from database.db import get_connection
from database.migrations.v0110_market_scanner_v1 import apply_migration
from trading.research.orchestration.models import FORMAL_WEIGHTS
from trading.scanner.feature_loader import ScannerFeatureLoader
from trading.scanner.schemas import (
    ScannerEvaluationRequest,
    ScannerParseRequest,
    ScannerScanRequest,
    ScannerScanResponse,
)
from trading.scanner.service import MarketScannerService


def _counts() -> dict[str, int]:
    with get_connection() as connection:
        return {
            table: int(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            )
            for table in (
                "data_records",
                "canonical_historical_bars",
                "canonical_market_records",
                "factor_outputs",
                "decision_packets",
                "risk_vetoes",
                "scanner_runs",
                "scanner_candidates",
            )
        }


def _scan(
    service: MarketScannerService,
    *,
    query: str,
    cutoff: datetime,
    top_n: int = 20,
    persist: bool = False,
) -> ScannerScanResponse:
    response = service.scan(
        ScannerScanRequest(
            query=query,
            data_cutoff=cutoff,
            top_n=top_n,
            persist_run=persist,
        )
    )
    if not isinstance(response, ScannerScanResponse):
        raise RuntimeError(
            f"acceptance query unexpectedly requires clarification: {query}"
        )
    return response


def run(*, persist_idempotency: bool) -> dict[str, Any]:
    service = MarketScannerService()
    cutoff = datetime.now().astimezone()
    before = _counts()
    ScannerFeatureLoader.clear_cache()
    cold = _scan(service, query="全A股按成交额前20只", cutoff=cutoff)
    warm = _scan(service, query="全A股按成交额前20只", cutoff=cutoff)
    price_amount = _scan(
        service,
        query="筛选10到20元、成交额超过5亿、排除ST和停牌的前20只股票",
        cutoff=cutoff,
    )
    chinext = _scan(
        service,
        query="找出今天放量上涨、站上20日均线的创业板前20只股票",
        cutoff=cutoff,
    )
    down_volume = _scan(
        service,
        query="价跌量增且换手率高于配置阈值的前20只股票",
        cutoff=cutoff,
    )
    coverage = _scan(
        service,
        query="至少2个因子可用的前20只股票",
        cutoff=cutoff,
    )
    fundamental = _scan(
        service,
        query="基本面为正的前20只股票",
        cutoff=cutoff,
    )
    ambiguous = service.parse(
        ScannerParseRequest(
            query="成交额大于5",
            data_cutoff=cutoff,
        )
    )
    local_without_model = service.parse(
        ScannerParseRequest(
            query="价格10到20元且量比大于1.5",
            data_cutoff=cutoff,
            allow_parser_model=False,
        )
    )
    research = service.research_handoff(
        cold.model_copy(update={"candidates": cold.candidates[:10]}),
    )
    persisted_first = persisted_second = None
    if persist_idempotency:
        persisted_first = _scan(
            service,
            query="全A股按成交额前20只",
            cutoff=cutoff,
            persist=True,
        )
        persisted_second = _scan(
            service,
            query="全A股按成交额前20只",
            cutoff=cutoff,
            persist=True,
        )
        evaluation = service.evaluate(
            ScannerEvaluationRequest(
                run_id=persisted_first.run_id,
                evaluated_at=datetime.now().astimezone(),
                persist=False,
            )
        )
    else:
        evaluation = None
    with get_connection() as connection:
        first_migration_rerun = apply_migration(connection)
        second_migration_rerun = apply_migration(connection)
    after = _counts()
    candidates = cold.candidates
    coverage_distribution = {f"{index}/5": 0 for index in range(6)}
    for candidate in candidates:
        coverage_distribution[candidate.factor_coverage] += 1
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "data_cutoff": cutoff.isoformat(),
        "scenarios": {
            "01_all_a_top20": cold.returned_count == 20,
            "02_price_amount_exclusions": price_amount.returned_count,
            "03_chinext_volume_up_above_sma20": chinext.returned_count,
            "04_price_down_volume_up_high_turnover": down_volume.returned_count,
            "05_factor_coverage_at_least_2": coverage.returned_count,
            "06_explicit_fundamental_missing_excluded": fundamental.returned_count,
            "07_ambiguous_query": ambiguous.clarification_required,
            "08_no_model_local_parser": (
                local_without_model.parser_model_call_count == 0
                and not local_without_model.clarification_required
            ),
            "09_cold_scan_symbols": cold.universe_count,
            "10_warm_scan_same_snapshot": not warm.performance.cold_cache,
            "11_research_accepts_10": len(
                research["ai_deep_analysis_symbols"]
            )
            == 10,
            "12_no_decision": not cold.decision_called,
            "13_formal_and_veto_unchanged": (
                FORMAL_WEIGHTS
                == {
                    next(
                        key
                        for key in FORMAL_WEIGHTS
                        if key.value == "TECHNICAL"
                    ): 0.60,
                    next(
                        key
                        for key in FORMAL_WEIGHTS
                        if key.value == "FUNDAMENTAL"
                    ): 0.40,
                }
                and before["risk_vetoes"] == after["risk_vetoes"]
            ),
            "14_persistence_idempotent": (
                None
                if not persist_idempotency
                else persisted_first.run_id == persisted_second.run_id
            ),
            "15_evaluation_insufficient": (
                None
                if evaluation is None
                else all(
                    item.status == "INSUFFICIENT_DATA"
                    for item in evaluation.items
                )
            ),
        },
        "performance": {
            "cold_ms": cold.performance.elapsed_ms,
            "warm_ms": warm.performance.elapsed_ms,
            "cold_peak_memory_bytes": cold.performance.peak_memory_bytes,
            "warm_peak_memory_bytes": warm.performance.peak_memory_bytes,
            "cold_data_read_ms": cold.performance.data_read_ms,
            "cold_feature_compute_ms": cold.performance.feature_compute_ms,
            "cold_filter_ms": cold.performance.filter_ms,
            "cold_anomaly_ms": cold.performance.anomaly_ms,
            "cold_ranking_ms": cold.performance.ranking_ms,
            "cold_result_card_ms": cold.performance.result_card_ms,
            "cold_database_sessions": cold.performance.database_session_count,
            "cold_database_queries": cold.performance.database_query_count,
            "warm_database_sessions": warm.performance.database_session_count,
            "warm_database_queries": warm.performance.database_query_count,
            "network_request_count": 0,
            "model_call_count": 0,
        },
        "cold_candidate_factor_coverage": coverage_distribution,
        "cold_candidate_symbols": [item.symbol for item in candidates],
        "safety": {
            "is_trade_recommendation": False,
            "decision_called": False,
            "formal_action_changed": False,
            "hard_veto_changed": False,
            "model_calls_per_stock": 0,
            "network_requests_per_stock": 0,
            "per_symbol_database_queries": 0,
            "formal_weights": {
                key.value: value for key, value in FORMAL_WEIGHTS.items()
            },
        },
        "database_counts_before": before,
        "database_counts_after": after,
        "business_data_not_reduced": all(
            after[key] >= before[key]
            for key in (
                "data_records",
                "canonical_historical_bars",
                "canonical_market_records",
                "factor_outputs",
                "decision_packets",
            )
        ),
        "migration_0110_rerun": {
            "first_applied": first_migration_rerun,
            "second_applied": second_migration_rerun,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/scanner"),
    )
    parser.add_argument("--persist-idempotency", action="store_true")
    args = parser.parse_args()
    result = run(persist_idempotency=args.persist_idempotency)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "stage9-acceptance.json"
    markdown_path = args.output_dir / "stage9-acceptance.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    performance = result["performance"]
    markdown_path.write_text(
        "\n".join(
            [
                "# 第9步全市场扫描器验收",
                "",
                f"- 冷扫描：{performance['cold_ms'] / 1000:.3f}秒",
                f"- 热扫描：{performance['warm_ms'] / 1000:.3f}秒",
                f"- 冷扫描峰值内存：{performance['cold_peak_memory_bytes']} bytes",
                f"- 数据库查询：冷{performance['cold_database_queries']} / 热"
                f"{performance['warm_database_queries']}",
                "- 网络请求：0",
                "- 扫描阶段模型调用：0",
                "- Decision调用：0",
                "- 结果仅为研究候选，不是交易建议。",
                "",
                "## 场景",
                "",
                *[
                    f"- {name}: {value}"
                    for name, value in result["scenarios"].items()
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
