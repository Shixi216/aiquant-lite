from __future__ import annotations

from contextlib import nullcontext

import duckdb
import pandas as pd

from data_hub.services import financial_statement_service as module
from database.migrations.v0100_unified_data import apply_migration as apply_v0100
from database.migrations.v0102_fundamental_point_in_time import apply_migration as apply_v0102


def test_financial_history_persistence_is_bulk_and_idempotent(monkeypatch) -> None:
    connection = duckdb.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE data_records (
            record_id VARCHAR PRIMARY KEY,
            task_id VARCHAR,
            symbol VARCHAR NOT NULL,
            data_type VARCHAR NOT NULL,
            event_time TIMESTAMPTZ NOT NULL,
            fetched_at TIMESTAMPTZ NOT NULL,
            source_name VARCHAR NOT NULL,
            source_url VARCHAR,
            source_level VARCHAR NOT NULL,
            verified BOOLEAN NOT NULL,
            content_hash VARCHAR,
            payload_json JSON NOT NULL
        )
        """
    )
    apply_v0100(connection)
    apply_v0102(connection)
    monkeypatch.setattr(module, "initialize_database", lambda: None)
    monkeypatch.setattr(
        module,
        "get_connection",
        lambda: nullcontext(connection),
    )
    rows = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "end_date": "20241231",
                "ann_date": "20250320",
                "update_flag": "1",
            },
            {
                "ts_code": "600000.SH",
                "end_date": "20250331",
                "ann_date": "20250425",
                "update_flag": "0",
            },
        ]
    )
    records = [
        module.FinancialStatementService._build_record(
            symbol="600000.SH",
            statement_type="income",
            report_period=period,
            row=module.FinancialStatementService._select_row(rows, period),
        )
        for period in ("20241231", "20250331")
    ]
    module.FinancialStatementService._persist(records)
    module.FinancialStatementService._persist(records)
    assert connection.execute("SELECT COUNT(*) FROM data_records").fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM canonical_financial_records"
    ).fetchone()[0] == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM canonical_financial_records WHERE data_available_time IS NOT NULL"
    ).fetchone()[0] == 2