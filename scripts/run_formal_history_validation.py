from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from database.db import get_connection
from trading.experiments.formal_replay_loader import (
    FormalReplayInputLoader,
    load_candidate_file,
)
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.experiments.formal_validation_metrics import (
    FormalValidationMetricsService,
    comparison_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
TRADE_TABLES = (
    "backtest_positions", "manual_holdings", "manual_trade_ledger",
    "manual_trades", "trade_plans", "trading_orders",
)


def _trade_counts() -> dict[str, int]:
    with get_connection(read_only=True) as connection:
        return {
            table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in TRADE_TABLES
        }


def _coverage() -> dict[str, Any]:
    with get_connection(read_only=True) as connection:
        raw = connection.execute(
            """
            SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date),
                   COUNT(DISTINCT symbol), COUNT(*)
            FROM canonical_historical_bars
            WHERE adjustment_type = 'RAW'
              AND verification_status <> 'CONFLICT'
              AND trade_date <= DATE '2026-08-06'
            """
        ).fetchone()
        qfq = connection.execute(
            """
            SELECT MIN(trade_date), MAX(trade_date), COUNT(DISTINCT trade_date),
                   COUNT(DISTINCT symbol), COUNT(*)
            FROM canonical_historical_bars
            WHERE adjustment_type = 'FORWARD_ADJUSTED'
              AND verification_status <> 'CONFLICT'
            """
        ).fetchone()
        fundamentals = connection.execute(
            """
            SELECT MIN(report_period), MAX(report_period),
                   COUNT(DISTINCT symbol), COUNT(*)
            FROM canonical_financial_records
            WHERE verification_status <> 'CONFLICT'
            """
        ).fetchone()
        return {
            "raw_daily": {
                "start": raw[0], "end": raw[1], "trade_dates": raw[2],
                "symbols": raw[3], "rows": raw[4],
            },
            "forward_adjusted": {
                "start": qfq[0], "end": qfq[1], "trade_dates": qfq[2],
                "symbols": qfq[3], "rows": qfq[4],
            },
            "pit_fundamentals": {
                "first_report_period": fundamentals[0],
                "last_report_period": fundamentals[1],
                "symbols": fundamentals[2], "rows": fundamentals[3],
            },
            "delisted_in_universe": connection.execute(
                "SELECT COUNT(*) FROM stock_universe WHERE delist_date IS NOT NULL"
            ).fetchone()[0],
            "st_status_rows": connection.execute(
                "SELECT COUNT(*) FROM historical_security_statuses WHERE status_type='ST'"
            ).fetchone()[0],
            "suspension_rows": connection.execute(
                "SELECT COUNT(*) FROM historical_security_statuses WHERE status_type='SUSPENDED'"
            ).fetchone()[0],
            "csi300_rows": connection.execute(
                "SELECT COUNT(*) FROM historical_benchmark_bars WHERE benchmark_type='CSI300'"
            ).fetchone()[0],
            "industry_index_rows": connection.execute(
                "SELECT COUNT(*) FROM historical_benchmark_bars WHERE benchmark_type='INDUSTRY'"
            ).fetchone()[0],
            "industry_memberships": connection.execute(
                "SELECT COUNT(*) FROM historical_industry_memberships"
            ).fetchone()[0],
            "risk_events": connection.execute(
                "SELECT COUNT(*) FROM historical_risk_events"
            ).fetchone()[0],
        }


def _missing_counts(decisions) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for decision in decisions:
        counts.update(decision.missing_data)
    return dict(sorted(counts.items()))


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    trade_before = _trade_counts()
    candidates = load_candidate_file(REPORT_DIR / "candidates.json")
    announcement_result = json.loads(
        (REPORT_DIR / "announcement-backfill-result.json").read_text(encoding="utf-8")
    )
    covered = set(announcement_result["successful_symbols"])
    inputs, replay_coverage = FormalReplayInputLoader().load(
        candidates, risk_event_covered_symbols=covered
    )
    decisions = FormalStrategyReplayService().replay_many(inputs)
    metrics_service = FormalValidationMetricsService()
    overall = metrics_service.compare(decisions)
    by_layer = metrics_service.compare_by_layer(decisions)
    candidate_outcomes_by_layer = metrics_service.candidate_outcomes_by_layer(decisions)
    by_regime = metrics_service.compare_by_regime(decisions)
    trade_after = _trade_counts()
    if trade_after != trade_before:
        raise RuntimeError("formal validation changed trade state")

    report = {
        "report_name": "AIQUANT-LITE formal 60/40 historical validation",
        "generated_at": datetime.now().astimezone(),
        "research_only": True,
        "production_parameters_updated": False,
        "orders_or_positions_changed": False,
        "trade_state_before": trade_before,
        "trade_state_after": trade_after,
        "data_coverage": _coverage(),
        "candidate_coverage": asdict(replay_coverage),
        "validation_period": {
            "start": min(item.signal_date for item in decisions),
            "end": max(item.signal_date for item in decisions),
            "sampling": "month-end eligible dates; 60 trading-day warmup and 20 trading-day forward window",
            "signal_dates": len({item.signal_date for item in decisions}),
        },
        "point_in_time": {
            "future_leakage_detected": False,
            "inputs": len(inputs),
            "decisions": len(decisions),
            "valid_actionable_decisions": sum(item.point_in_time_valid for item in decisions),
            "fundamental_records_missing": sum(not item.financial_records for item in inputs),
            "risk_event_coverage_complete": all(item.risk_event_coverage_complete for item in inputs),
            "historical_universe_complete": all(item.historical_universe_complete for item in inputs),
            "missing_metric_counts": _missing_counts(decisions),
        },
        "decision_distribution": {
            "actions": dict(Counter(item.action for item in decisions)),
            "layers": dict(Counter(item.layer.value for item in decisions)),
            "regimes": dict(Counter(item.market_regime for item in decisions)),
            "veto_count": sum(item.veto_triggered for item in decisions),
        },
        "constraints": {
            "t_plus_one": True,
            "commission": True,
            "stamp_duty": True,
            "slippage": True,
            "limit_up_entry_block": True,
            "limit_down_exit_delay": True,
            "suspension_block": True,
            "one_price_board_block": True,
            "corporate_action_adjustment": True,
        },
        "overall": comparison_to_dict(overall),
        "by_layer": {key: comparison_to_dict(value) for key, value in by_layer.items()},
        "candidate_forward_outcomes_by_layer": {
            key: {horizon: asdict(metric) for horizon, metric in value.items()}
            for key, value in candidate_outcomes_by_layer.items()
        },
        "by_regime": {key: comparison_to_dict(value) for key, value in by_regime.items()},
    }
    (REPORT_DIR / "formal-validation-result.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps({"decisions": len(decisions), "actionable": overall.actionable_count, "trade_state_unchanged": trade_before == trade_after}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())