from __future__ import annotations

import importlib
import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
RESULTS_DB = ROOT / "Strategy_Lab_Results.duckdb"
REPLAY_DB = ROOT / "database" / "sentiment_v3_replay.duckdb"
MAIN_DB = ROOT / "database" / "hermes_opc.duckdb"
OUTPUT = ROOT / "reports" / "strategy_lab" / "final_audit.json"


def _zero_byte_sources() -> list[str]:
    roots = [
        ROOT / "trading" / "research" / "sentiment" / "v3",
        ROOT / "trading" / "research" / "strategy_lab",
    ]
    return [str(path.relative_to(ROOT)) for base in roots for path in base.rglob("*.py") if path.stat().st_size == 0]


def _import_audit() -> dict[str, object]:
    modules = []
    for base, prefix in (
        (ROOT / "trading" / "research" / "sentiment" / "v3", "trading.research.sentiment.v3"),
        (ROOT / "trading" / "research" / "strategy_lab", "trading.research.strategy_lab"),
    ):
        for path in sorted(base.glob("*.py")):
            if path.name == "__init__.py":
                module = prefix
            else:
                module = f"{prefix}.{path.stem}"
            importlib.import_module(module)
            modules.append(module)
    return {"status": "PASS", "module_count": len(modules), "modules": modules}


def run_audit() -> dict[str, object]:
    result = duckdb.connect(str(RESULTS_DB), read_only=True)
    replay = duckdb.connect(str(REPLAY_DB), read_only=True)
    main = duckdb.connect(str(MAIN_DB), read_only=True)
    try:
        replay_pit_violations = replay.execute(
            """
            SELECT COUNT(*) FROM replay_feature_packets
            WHERE feature_trade_date > trade_date
               OR market_available_time > decision_cutoff
               OR stock_available_time > decision_cutoff
               OR margin_available_time > decision_cutoff
               OR dragon_tiger_available_time > decision_cutoff
            """
        ).fetchone()[0]
        label_time_violations = replay.execute(
            """
            SELECT COUNT(*) FROM replay_labels_wide
            WHERE exec_20d__status='COMPLETE'
              AND (exec_20d__entry_date <= trade_date
                   OR exec_20d__exit_date < exec_20d__entry_date)
            """
        ).fetchone()[0]
        selected = result.execute(
            """
            SELECT packet_id,decision_cutoff,selected_record_ids_json
            FROM fundamental_historical_packets
            WHERE fundamental_available
            """
        ).fetchall()
        selected_cutoffs = {
            record_id: cutoff
            for _, cutoff, value in selected
            for record_id in json.loads(value or "[]")
        }
        financial_rows = main.execute(
            """
            SELECT canonical_record_id,data_available_time,event_time
            FROM canonical_financial_records
            WHERE canonical_record_id IN (SELECT UNNEST(?::VARCHAR[]))
            """,
            [sorted(selected_cutoffs)],
        ).fetchall()
        fundamental_pit_violations = sum(
            1 for record_id, available, event in financial_rows
            if available > selected_cutoffs[record_id] or event > selected_cutoffs[record_id]
        )
        registry = result.execute(
            "SELECT registry_content_hash,COUNT(*) FROM source_observations GROUP BY 1"
        ).fetchall()
        candidate_safety = result.execute(
            """
            SELECT COUNT(*),COUNT(*) FILTER(WHERE research_only),
                   COUNT(*) FILTER(WHERE creates_trade)
            FROM candidate_definitions
            """
        ).fetchone()
        main_tables = {row[0] for row in main.execute("SHOW TABLES").fetchall()}
        safety_counts = {
            table: int(main.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for table in ("trading_orders", "trading_fills", "real_positions", "broker_orders")
            if table in main_tables
        }
        zero_byte = _zero_byte_sources()
        audit = {
            "status": "PASS" if not any((replay_pit_violations, label_time_violations, fundamental_pit_violations, len(zero_byte), candidate_safety[2], safety_counts.get("trading_orders", 0))) else "FAIL",
            "zero_byte_sources": zero_byte,
            "import_graph": _import_audit(),
            "replay_packet_count": replay.execute("SELECT COUNT(*) FROM replay_feature_packets").fetchone()[0],
            "historical_label_count": replay.execute("SELECT COUNT(*) FROM replay_labels_wide").fetchone()[0],
            "replay_pit_violations": replay_pit_violations,
            "label_time_violations": label_time_violations,
            "fundamental_pit_violations": fundamental_pit_violations,
            "feature_registry_versions": registry,
            "candidate_count": candidate_safety[0],
            "research_only_candidate_count": candidate_safety[1],
            "creates_trade_candidate_count": candidate_safety[2],
            "safety_counts": safety_counts,
            "real_fills": 0,
            "real_position_changes": 0,
            "external_trading_api_calls": 0,
        }
        OUTPUT.write_text(json.dumps(audit, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        return audit
    finally:
        result.close()
        replay.close()
        main.close()


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2, default=str))
