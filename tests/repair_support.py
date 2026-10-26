"""Deterministic, explicitly synthetic data for isolated integration tests."""
from datetime import timedelta


def seed_scanner_database(connection, cutoff, *, size=20):
    available = cutoff - timedelta(days=1)
    connection.execute(
        "INSERT INTO stock_universe_versions VALUES ('repair-test-v1', 'synthetic-universe', ?, ?, ?, ?, ?, '[\"TEST\"]', '{}')",
        [available, available, available, size, size],
    )
    symbols = [f"{600000 + i:06d}.SH" for i in range(size - 1)] + ["603980.SH"]
    connection.executemany(
        """INSERT INTO stock_universe (
            symbol, exchange, market, board, security_type, company_name, short_name,
            list_date, listing_status, is_st, is_suspended, currency, price_limit_type,
            primary_source, source_record_ids_json, verification_status,
            source_differences_json, data_available_time, updated_at, universe_version
        ) VALUES (?, 'SH', 'CN_A', 'MAIN', 'STOCK', 'SYNTHETIC', 'SYNTHETIC',
            '2000-01-01', 'ACTIVE', FALSE, FALSE, 'CNY', 'STANDARD', 'TEST', '[]',
            'SINGLE_SOURCE', '{}', ?, ?, 'repair-test-v1')""",
        [[symbol, available, available] for symbol in symbols],
    )
    # Raw facts only; the real factor loader computes technical evidence.
    days = [cutoff.date() - timedelta(days=n) for n in range(1, 100)
            if (cutoff.date() - timedelta(days=n)).weekday() < 5][:61]
    rows = []
    for symbol in symbols:
        for i, day in enumerate(sorted(days)):
            close = 10 + i / 100
            rows.append([f"test-bar-{symbol}-{day}", symbol, day, available, available,
                         available, available, close, close + .1, close - .1, close,
                         f"synthetic-{symbol}-{day}"])
    connection.executemany(
        """INSERT INTO canonical_historical_bars (
            bar_id, symbol, trade_date, event_time, data_available_time, data_cutoff,
            generated_at, adjustment_type, open, high, low, close, volume, amount,
            volume_unit, amount_unit, primary_source, source_record_ids_json,
            verification_source_ids_json, verification_status, confidence,
            content_hash, algorithm_version, raw_payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RAW', ?, ?, ?, ?, 1000000, 10000000,
            'SHARES', 'CNY', 'TEST', '[]', '[]', 'VERIFIED', 1, ?, 'synthetic-test', '{}')""",
        rows,
    )
    connection.execute(
        """INSERT INTO market_snapshot_runs (
            snapshot_id, mode, provider, data_cutoff, expected_universe_size,
            received_symbol_count, valid_symbol_count, missing_symbol_count,
            coverage_ratio, completeness_status, started_at, completed_at,
            snapshot_time, content_hash, request_count, elapsed_seconds,
            peak_memory_bytes, database_growth_bytes
        ) VALUES ('synthetic-snapshot', 'APPLY', 'TEST', ?, ?, ?, ?, 0, 1,
            'COMPLETE', ?, ?, ?, 'synthetic-snapshot-hash', 0, 0, 0, 0)""",
        [available, size, size, size, available, available, available],
    )
    connection.executemany(
        """INSERT INTO market_snapshot_items (
            snapshot_id, symbol, price, previous_close, open, high, low, volume,
            amount, change, change_pct, turnover_rate, snapshot_time, source,
            item_status, is_suspended, is_abnormal, payload_json
        ) VALUES ('synthetic-snapshot', ?, 10, 10, 10, 11, 9, 1000000,
            200000000, 0, 0, .08, ?, 'TEST', 'VALID', FALSE, FALSE,
            '{"volume_unit":"SHARES","amount_unit":"CNY","change_pct_unit":"DECIMAL","turnover_rate_unit":"DECIMAL"}')""",
        [[symbol, available] for symbol in symbols],
    )
    return symbols
