# Sentiment V3 Phase S3B.0

## Outcome

A PIT-safe historical REPLAY dataset now spans 493 trading dates. The final S3B_RESEARCH_SET_V1 contains 38/39 HISTORY_SAFE_V1 canonical features. No IC, RankIC, ranking, Alpha conclusion, or weight research was run.

- Dataset ID: sv3_s3b_replay_222e9321c2719ecc90bfe149cbf394a1
- Dataset content hash: 222e9321c2719ecc90bfe149cbf394a138a99fb46d2e7d884669ec39b4f92fa5
- Range: 2024-08-01 to 2026-08-13
- Packets and physical wide Label rows: 2625560
- Symbols: 5539
- Logical Statistical Labels: 13127800
- Logical Executable Labels: 13127800
- Research DB size: 7,038,840,832 bytes

## Frozen definitions

- Registry SHA256: 779AD72DEC929AC42FC7A65FB5E3E7A94913C44E8F1010FC63B9181030466080
- Registry content hash: 20f80a62ec073fbe44622cdb6fbcba1c9b02c4abf66823989bc2bff0d9a8b726
- Label Spec SHA256: 49D6BAB04E3548A7A57606B93A3E326797AA3981C7DD1DD296374FDC459EF1D0
- Evaluation SHA256: F2D3D2059B63FD4A5FBC6963A9F16EAEC9EBC49D47FEA89D48D94E67C4859138

Registry V1, Label Spec V1, Evaluation Framework V1 and ADJ_FACTOR_RETURN_V1 were not changed.

## Availability and source audit

The 39-row audit is in Sentiment_V3_Historical_Replay_Availability_V1.md. REPLAY_READY=38, BACKFILLABLE after backfill=0, FORWARD_ONLY_DUE_TO_SOURCE=1. market.coverage_ratio is excluded because its frozen definition needs historical strong-stock-pool coverage.

Only the existing Tushare Provider was used.

| Source | Dates | Rows | Provider seconds | From | To |
|---|---:|---:|---:|---|---|
| DAILY_BASIC | 493 | 2672721 | 575.514 | 2024-08-01 | 2026-08-13 |
| DAILY_REFERENCE | 493 | 2672721 | 645.727 | 2024-08-01 | 2026-08-13 |
| DRAGON_TIGER | 493 | 35714 | 583.883 | 2024-08-01 | 2026-08-13 |
| MARGIN | 493 | 2052873 | 814.890 | 2024-08-01 | 2026-08-13 |
| STK_LIMIT | 493 | 3571522 | 642.022 | 2024-08-01 | 2026-08-13 |

Margin used margin and margin_detail. Dragon Tiger used top_list and top_inst. Margin T availability stays inferred at T+1 09:00. Dragon Tiger T availability stays inferred at T 18:00. has_dragon_tiger distinguishes False from provider failure.

## Cutoff and PIT

PREOPEN_REPLAY_CUTOFF_V1 uses T+1 trading-session 09:10 Asia/Shanghai. It is after Margin availability and before the frozen NEXT_OPEN entry at 09:30. Features consume only T or earlier market facts. No T+1 quote enters Feature assembly.

All 2625560 Packets passed the full-table PIT check. A separate 50-date x 8-symbol audit checked 400 rows with zero leaks. All rows are record_source=REPLAY. No current Sector membership or Forward-only field is present.

## Replay materialization

- Market snapshots: 493.
- Stock snapshots and Packets: 2625560.
- Checkpoint/resume: supported per trade date.
- Rerun: idempotent.
- Writes: date chunks, columnar operations and one DuckDB writer.
- Packet content hashes are deterministic and cover Feature values and quality.
- Dataset hash binds frozen definitions, research set, Packet hash aggregate and Label hash aggregate.
- No per-Packet/module N+1.

### Packets by year

| Year | Packets |
|---:|---:|
| 2024 | 544724 |
| 2025 | 1305029 |
| 2026 | 775807 |

### Packet quality

| Quality | Packets |
|---|---:|
| COMPLETE | 1427504 |
| PARTIAL | 1198056 |

PARTIAL remains explicit. Missing is never changed to zero.

### Raw-source tail limitation

| Trade date | Observed | Eligible | Quote coverage |
|---|---:|---:|---:|
| 2026-08-07 | 59 | 5459 | 1.080784% |
| 2026-08-10 | 51 | 5459 | 0.934237% |
| 2026-08-11 | 39 | 5462 | 0.714024% |
| 2026-08-12 | 31 | 5463 | 0.567454% |
| 2026-08-13 | 21 | 5463 | 0.384404% |

The final five dates are severe LOW_COVERAGE dates. They remain in the auditable dataset, but the next S3B run must predeclare their handling and must not treat them as full-market cross sections.

## Feature coverage

| Feature | Observed | Missing | Global coverage | Valid dates |
|---|---:|---:|---:|---:|
| capital.dragon_tiger.has_dragon_tiger | 2625560 | 0 | 100.000000% | 493 |
| capital.dragon_tiger.institution_net_amount | 31548 | 2594012 | 1.201572% | 493 |
| capital.dragon_tiger.institution_seat_count | 31548 | 2594012 | 1.201572% | 493 |
| capital.dragon_tiger.net_amount | 31547 | 2594013 | 1.201534% | 493 |
| capital.margin.margin_balance | 1722960 | 902600 | 65.622572% | 493 |
| capital.margin.margin_balance_change | 1719052 | 906508 | 65.473728% | 492 |
| capital.margin.margin_balance_change_pct | 1719052 | 906508 | 65.473728% | 492 |
| capital.margin.margin_buy | 1722960 | 902600 | 65.622572% | 493 |
| capital.margin.margin_net_buy | 1722959 | 902601 | 65.622534% | 493 |
| capital.margin.margin_repay | 1722959 | 902601 | 65.622534% | 493 |
| capital.stock.turnover | 2625560 | 0 | 100.000000% | 493 |
| market.advance_decline_ratio | 2625560 | 0 | 100.000000% | 493 |
| market.advance_ratio | 2625560 | 0 | 100.000000% | 493 |
| market.advancers | 2625560 | 0 | 100.000000% | 493 |
| market.breadth_strength | 2625560 | 0 | 100.000000% | 493 |
| market.decline_ratio | 2625560 | 0 | 100.000000% | 493 |
| market.decliners | 2625560 | 0 | 100.000000% | 493 |
| market.market_turnover | 2625560 | 0 | 100.000000% | 493 |
| market.market_turnover_change | 2620227 | 5333 | 99.796881% | 492 |
| market.unchanged | 2625560 | 0 | 100.000000% | 493 |
| stock.amplitude | 2625560 | 0 | 100.000000% | 493 |
| stock.close_location_value | 2620139 | 5421 | 99.793530% | 493 |
| stock.gap_and_fade | 2625560 | 0 | 100.000000% | 493 |
| stock.gap_and_follow_through | 2625560 | 0 | 100.000000% | 493 |
| stock.high_to_close_drawdown | 2625560 | 0 | 100.000000% | 493 |
| stock.intraday_return_pct | 2625560 | 0 | 100.000000% | 493 |
| stock.is_broken_board | 2625560 | 0 | 100.000000% | 493 |
| stock.is_limit_down | 2625560 | 0 | 100.000000% | 493 |
| stock.is_limit_up | 2625560 | 0 | 100.000000% | 493 |
| stock.low_to_close_recovery | 2625560 | 0 | 100.000000% | 493 |
| stock.open_gap_pct | 2625560 | 0 | 100.000000% | 493 |
| stock.relative_market_return | 2625560 | 0 | 100.000000% | 493 |
| stock.relative_market_volume | 1699261 | 926299 | 64.719945% | 318 |
| stock.return_pct | 2625560 | 0 | 100.000000% | 493 |
| stock.turnover_change | 2619962 | 5598 | 99.786788% | 492 |
| stock.turnover_rate | 2625560 | 0 | 100.000000% | 493 |
| stock.volume_change | 2619822 | 5738 | 99.781456% | 490 |
| stock.volume_ratio | 2624585 | 975 | 99.962865% | 493 |

Margin global coverage is lower because non-margin-eligible rows are not zero. Dragon Tiger amount fields are event-conditional. relative_market_volume has only 318 valid dates because the frozen calculation requires complete market-volume context. No SH/SZ/BJ, ST, inactive, delisted or newly listed group was filtered.

## Labels

Five Statistical and five Executable frozen Label specs are stored in a wide row per Packet.

| Kind | Horizon | Valid | Valid rate | Insufficient window | Invalid | Partial |
|---|---:|---:|---:|---:|---:|---:|
| STAT | 1 | 0 | 0.000000% | 21 | 6596 | 0 |
| EXEC | 1 | 0 | 0.000000% | 52 | 17412 | 0 |
| STAT | 3 | 0 | 0.000000% | 91 | 18663 | 1042 |
| EXEC | 3 | 0 | 0.000000% | 91 | 23855 | 2 |
| STAT | 5 | 0 | 0.000000% | 201 | 30396 | 2279 |
| EXEC | 5 | 0 | 0.000000% | 201 | 35823 | 883 |
| STAT | 10 | 0 | 0.000000% | 27317 | 31924 | 6201 |
| EXEC | 10 | 0 | 0.000000% | 27317 | 37831 | 4194 |
| STAT | 20 | 0 | 0.000000% | 81759 | 32680 | 15738 |
| EXEC | 20 | 0 | 0.000000% | 81759 | 39110 | 13119 |

Right-edge rows remain INSUFFICIENT_FORWARD_WINDOW. No old forward-return-raw-v1 and no raw-price fallback are used.

## Isolation

Research data is isolated in database/sentiment_v3_replay.duckdb. FORWARD_SHADOW_V1 REAL rows were not reset or rewritten. No 60/40, DecisionEngine, A/B Shadow, Canonical Shadow, stock_report, order, trade, TradePlan or holding path was changed.

## Performance

- Margin backfill provider time: 814.890s.
- Dragon Tiger backfill provider time: 583.883s.
- Market Replay calculation: 20.695s.
- Stock/Packet stage: 16.008s.
- Initial full Packet process including preparation: at most 1086.5s.
- Label checkpoint build: 4659.048s for the final 328 pending dates. Timing for the earlier interrupted 165-date process was not persisted and is not fabricated.
- Final Dataset metadata: under 0.1s.
- Quality-aware Packet rehash: about 19.4s.

## Gate

The structural gate is satisfied: multi-date dataset exists, more than 200 dates, every included Feature is REPLAY_READY, PIT leakage is zero, current membership and Forward-only features are excluded, REAL Forward Shadow is unchanged, Label lineage is complete, and tests are green.

Caveat: use only eligible/valid cross sections and predeclare handling of the five LOW_COVERAGE tail dates. This phase did not run any empirical Feature evaluation.

## Verification

- S3B.0 tests added: 16.
- Full pytest: 1573 passed, 0 failed, 0 skipped; one third-party Starlette deprecation warning.
- Ruff on all changed Python files: 0 errors.
