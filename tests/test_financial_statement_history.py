from __future__ import annotations

import pandas as pd

from data_hub.services.financial_statement_service import (
    FinancialStatementService,
    STATEMENTS,
)


def frame(statement_type: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "end_date": "20241231",
                "ann_date": "20250320",
                "update_flag": "0",
                "marker": f"{statement_type}-old",
            },
            {
                "ts_code": "600000.SH",
                "end_date": "20241231",
                "f_ann_date": "20250321",
                "update_flag": "1",
                "marker": f"{statement_type}-latest",
            },
            {
                "ts_code": "600000.SH",
                "end_date": "20250331",
                "ann_date": "20250425",
                "update_flag": "0",
                "marker": f"{statement_type}-q1",
            },
        ]
    )


def test_history_builder_keeps_all_common_periods() -> None:
    records = FinancialStatementService._build_history_records(
        symbol="600000.SH",
        frames={name: frame(name) for name in STATEMENTS},
    )
    assert len(records) == 6
    assert {record.data["report_period"] for record in records} == {
        "20241231",
        "20250331",
    }
    annual = [
        record for record in records
        if record.data["report_period"] == "20241231"
    ]
    assert all(record.data["marker"].endswith("latest") for record in annual)
    assert all(record.data["data_available_time"] for record in records)


def test_history_builder_uses_only_periods_shared_by_all_statements() -> None:
    frames = {name: frame(name) for name in STATEMENTS}
    frames["cashflow"] = frames["cashflow"].query(
        "end_date == '20241231'"
    )
    records = FinancialStatementService._build_history_records(
        symbol="600000.SH",
        frames=frames,
    )
    assert len(records) == 3
    assert {record.data["report_period"] for record in records} == {
        "20241231"
    }