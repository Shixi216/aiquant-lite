from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture(scope="session", autouse=True)
def qt_session_runtime():
    """Retain one QApplication across desktop modules and subsequent API tests."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(scope="module", autouse=True)
def desktop_market_database(request, tmp_path_factory):
    """Desktop workflow tests use synthetic inputs, never a developer database."""
    selected = {
        "test_stage12b_agent_workspace",
        "test_stage12_installation_audit",
        "test_stage12_model_chain_blockers",
    }
    if request.module.__name__.rsplit(".", 1)[-1] not in selected:
        yield None
        return

    from config.settings import settings
    from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
    from database.db import get_connection, initialize_database, insert_market_record

    database = tmp_path_factory.mktemp("desktop-market") / "synthetic.duckdb"
    patch = pytest.MonkeyPatch()
    patch.setattr(settings, "opc_database_path", database)
    try:
        initialize_database()
        cutoff = datetime.now(ZoneInfo("Asia/Shanghai")) - timedelta(hours=1)
        rows = []
        history_rows = []
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            for symbol in ("300750.SZ", "002594.SZ", "600000.SH"):
                for index in range(35):
                    event_time = cutoff - timedelta(days=34 - index)
                    price = 10.0 + index * 0.05
                    payload = {
                        "trade_date": event_time.strftime("%Y%m%d"),
                        "open": price, "high": price + 0.1, "low": price - 0.1,
                        "close": price, "volume": 1_000_000,
                    }
                    record_id = f"synthetic-{symbol}-{index}"
                    content_hash = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
                    record = MarketRecord(
                        record_id=record_id, symbol=symbol, data_type=DataType.DAILY_BAR,
                        event_time=event_time, fetched_at=cutoff,
                        source_name="synthetic-desktop-test", source_level=SourceLevel.STRUCTURED,
                        verified=True, content_hash=content_hash, data=payload,
                    )
                    insert_market_record(connection, record)
                    rows.append([
                        f"canonical-{record_id}", symbol, "daily_bar", event_time,
                        event_time, event_time, "synthetic-desktop-test",
                        json.dumps([record_id]), json.dumps(payload), content_hash,
                    ])
                    history_rows.append([
                        f"history-{record_id}", symbol, event_time.date(), event_time,
                        event_time, event_time, event_time, price, price + 0.1,
                        price - 0.1, price, 1_000_000, price * 1_000_000,
                        json.dumps([record_id]), content_hash,
                    ])
            connection.executemany(
                "INSERT INTO canonical_market_records "
                "(canonical_record_id, symbol, data_type, event_time, data_cutoff, generated_at, "
                "primary_source, source_record_ids_json, verification_source_ids_json, "
                "verification_status, field_differences_json, payload_json, confidence, "
                "content_hash, algorithm_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', 'VERIFIED', '{}', ?, 1, ?, 'synthetic-v1')",
                rows,
            )
            connection.executemany(
                "INSERT INTO canonical_historical_bars "
                "(bar_id, symbol, trade_date, event_time, data_available_time, data_cutoff, "
                "generated_at, adjustment_type, open, high, low, close, volume, amount, "
                "volume_unit, amount_unit, primary_source, source_record_ids_json, "
                "verification_source_ids_json, verification_status, confidence, content_hash, "
                "algorithm_version, raw_payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'RAW', ?, ?, ?, ?, ?, ?, 'SHARES', 'CNY', "
                "'synthetic-desktop-test', ?, '[]', 'VERIFIED', 1, ?, 'synthetic-v1', '{}')",
                history_rows,
            )
            connection.execute("COMMIT")
        yield database
    finally:
        patch.undo()


def pytest_addoption(parser):
    parser.addoption(
        "--run-local-state", action="store_true", default=False,
        help="Run checks against an independently prepared local research database",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-local-state"):
        return
    skip = pytest.mark.skip(reason="requires prepared local data and --run-local-state")
    for item in items:
        if item.get_closest_marker("local_state"):
            item.add_marker(skip)
