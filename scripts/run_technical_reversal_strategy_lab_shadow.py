"""新技术反转规则的独立 Strategy Lab 影子验证，不覆盖既有结果库。"""
# ruff: noqa: E402
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading.research.strategy_lab.dataset_builder import StrategyLabDatasetBuilder
from trading.research.strategy_lab.evaluation import PRIMARY_COST_BP
from trading.research.strategy_lab.runner import StrategyLabRunner


def _legacy_metrics(path: Path) -> list[dict]:
    if not path.exists():
        return []
    connection = duckdb.connect(str(path), read_only=True)
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info('candidate_metrics')").fetchall()
        }
        wanted = [
            name for name in (
                "candidate_id", "cost_bp", "rank_ic", "sharpe",
                "annualized_return", "max_drawdown", "turnover",
            )
            if name in columns
        ]
        rows = connection.execute(
            f"SELECT {','.join(wanted)} FROM candidate_metrics m "
            "JOIN candidate_definitions d USING(candidate_id) "
            "WHERE d.family='LEGACY' AND m.cost_bp=?",
            [PRIMARY_COST_BP],
        ).fetchall()
        return [dict(zip(wanted, row)) for row in rows]
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--results-db",
        default="evaluation/Strategy_Lab_Results_technical_v20260902.duckdb",
    )
    parser.add_argument(
        "--output",
        default="evaluation/technical_reversal_strategy_lab_shadow_20260902.json",
    )
    args = parser.parse_args()
    results_db = (ROOT / args.results_db).resolve()
    output = (ROOT / args.output).resolve()
    results_db.parent.mkdir(parents=True, exist_ok=True)
    build = StrategyLabDatasetBuilder(results_db=results_db).build()
    run = StrategyLabRunner(results_db=results_db).run()
    payload = {
        "status": "SHADOW_ONLY_NO_FORMAL_SWITCH",
        "research_only": True,
        "formal_weight": 0,
        "creates_trade": False,
        "technical_version": "technical_signal_exact_v2026_09_02",
        "build": asdict(build),
        "run": run,
        "legacy_old": _legacy_metrics(ROOT / "Strategy_Lab_Results.duckdb"),
        "legacy_new": _legacy_metrics(results_db),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
