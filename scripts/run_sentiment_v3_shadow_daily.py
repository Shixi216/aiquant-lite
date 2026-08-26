from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone

import duckdb

from trading.research.sentiment.v3.live_shadow import LiveShadowExecutor, ShadowWindow
from trading.research.sentiment.v3.shadow_drift_monitor import ShadowDriftMonitor

def main():
    parser = argparse.ArgumentParser(description="Run Sentiment V3 Live Shadow Daily")
    parser.add_argument("--window", choices=["PREOPEN", "POSTCLOSE"], required=True)
    parser.add_argument("--date", type=str, help="YYYY-MM-DD", default=date.today().isoformat())
    args = parser.parse_args()

    trade_date = date.fromisoformat(args.date)
    window = ShadowWindow(args.window)
    cutoff = datetime.now(timezone(timedelta(hours=8)))

    conn = duckdb.connect(":memory:")
    
    # Mock data initialization for the script run
    batch_data = [
        {"symbol": "000001.SZ", "feature_values": {"capital.dragon_tiger.net_amount": 1000, "capital.stock.turnover": 0.05, "stock.gap_and_follow_through": 0.01}},
        {"symbol": "000002.SZ", "feature_values": {"capital.dragon_tiger.net_amount": -1000, "capital.stock.turnover": 0.15, "stock.gap_and_follow_through": -0.01}}
    ]

    from database.migrations.v0121_sentiment_v3_live_shadow import apply_migration
    apply_migration(conn)

    executor = LiveShadowExecutor(conn)
    summary = executor.run_batch(trade_date, cutoff, window, batch_data)

    monitor = ShadowDriftMonitor(conn)
    reports = monitor.generate_daily_report(trade_date, window)
    monitor.persist_reports(reports)

    assert summary.formal_weight == 0
    assert summary.research_only is True
    assert summary.creates_trade is False

    print(f"Shadow summary: {summary}")
    print(f"Drift reports generated: {len(reports)}")

if __name__ == '__main__':
    main()
