# Sentiment V3 Historical Replay Availability V1

- Manifest: HistoricalReplayAvailabilityManifestV1
- Manifest hash: 4889e83eb32021e104c376a76016ec1580a6de5bd96ffd6f9a8e27f14b939768
- Registry SHA256: 779AD72DEC929AC42FC7A65FB5E3E7A94913C44E8F1010FC63B9181030466080
- Raw range: 2024-08-01 to 2026-08-13 (493 dates)
- S3B_RESEARCH_SET_V1: 38/39 canonical features

| Feature | Module | Sources | Raw fields | Date range | Deterministic | PIT safe | Status | Blocking reason |
|---|---|---|---|---|---:|---:|---|---|
| capital.dragon_tiger.has_dragon_tiger | capital.dragon_tiger | Tushare top_list/top_inst | has_dragon_tiger, successful date coverage | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.dragon_tiger.institution_net_amount | capital.dragon_tiger | Tushare top_list/top_inst | institution_net_amount, successful date coverage | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.dragon_tiger.institution_seat_count | capital.dragon_tiger | Tushare top_list/top_inst | institution_seat_count, successful date coverage | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.dragon_tiger.net_amount | capital.dragon_tiger | Tushare top_list/top_inst | net_amount, successful date coverage | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.margin.margin_balance | capital.margin | Tushare margin_detail | margin_balance, previous trading-day margin_detail | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.margin.margin_balance_change | capital.margin | Tushare margin_detail | margin_balance_change, previous trading-day margin_detail | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.margin.margin_balance_change_pct | capital.margin | Tushare margin_detail | margin_balance_change_pct, previous trading-day margin_detail | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.margin.margin_buy | capital.margin | Tushare margin_detail | margin_buy, previous trading-day margin_detail | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.margin.margin_net_buy | capital.margin | Tushare margin_detail | margin_net_buy, previous trading-day margin_detail | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.margin.margin_repay | capital.margin | Tushare margin_detail | margin_repay, previous trading-day margin_detail | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| capital.stock.turnover | capital.stock | canonical_historical_bars | amount | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.advance_decline_ratio | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.advance_ratio | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.advancers | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.breadth_strength | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.coverage_ratio | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | - | True | True | FORWARD_ONLY_DUE_TO_SOURCE | historical strong-stock pool is unavailable for frozen meta coverage |
| market.decline_ratio | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.decliners | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.market_turnover | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.market_turnover_change | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| market.unchanged | market | canonical_historical_bars, Tushare daily, stock_basic, security_status | close, previous_close, amount, historical universe | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.amplitude | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.close_location_value | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.gap_and_fade | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.gap_and_follow_through | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.high_to_close_drawdown | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.intraday_return_pct | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.is_broken_board | stock | canonical_historical_bars, Tushare stk_limit | open, high, low, close, up_limit, down_limit | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.is_limit_down | stock | canonical_historical_bars, Tushare stk_limit | open, high, low, close, up_limit, down_limit | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.is_limit_up | stock | canonical_historical_bars, Tushare stk_limit | open, high, low, close, up_limit, down_limit | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.low_to_close_recovery | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.open_gap_pct | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.relative_market_return | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.relative_market_volume | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.return_pct | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.turnover_change | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.turnover_rate | stock | Tushare daily_basic | turnover_rate | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.volume_change | stock | canonical_historical_bars, Tushare daily, historical_security_statuses | open, high, low, close, volume, amount, previous bar | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |
| stock.volume_ratio | stock | Tushare daily_basic | volume_ratio | 2024-08-01..2026-08-13 | True | True | REPLAY_READY | - |

## Conclusion

38 features are REPLAY_READY. market.coverage_ratio is FORWARD_ONLY_DUE_TO_SOURCE because the frozen definition requires historical strong-stock-pool coverage. Quote coverage is not substituted. Margin and Dragon Tiger were backfilled through the existing Tushare Provider.
