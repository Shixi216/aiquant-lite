# Sentiment V3 Phase S3A.1

## 1. Scope and freeze

This phase audited and repaired `historical_adjustment_factors` only. It did not
change Feature Registry V1, Label Spec V1, `ADJ_FACTOR_RETURN_V1`, evaluator
definitions, formal strategy, A/B Shadow, Canonical Shadow, or REAL forward
packets. No Feature IC/RankIC, ranking, weight, or Alpha conclusion was run.

Research RAW scope is the actual canonical history present in DuckDB:
`2024-08-01` through `2026-08-13`. `ADJ_FACTOR_ELIGIBLE_V1` means one latest,
non-CONFLICT, canonical `RAW` A-share bar identified by a normalized
`NNNNNN.SH/SZ/BJ` symbol and `trade_date`. Missing factor is never treated as 1.

## 2. Root cause

The database really lacked factors. It was not a Label Builder join failure.

- Existing source: Tushare Pro `adj_factor`.
- Existing code: `TushareProvider.get_adjustment_factors(symbol, start, end)`
  and `FormalHistoryDataBackfillService`.
- Existing backfill accepted only explicit `adjustment_symbols`; it had been run
  for two candidate subsets (158 + 339 symbols), not the historical RAW universe.
- Existing factor batches were internally complete for those subsets, so there
  is no evidence that pagination, rate limiting, or a swallowed Provider error
  caused the 497-symbol boundary.
- Symbols were already normalized, `trade_date` was a real DuckDB `DATE`, factor
  rows had no duplicate symbol-date, and S3A joined exact symbol/date correctly.
- The selection created clear survivorship bias: BJ, all 53 historical delisted
  names, recent listings, and almost all ST names were absent.
- Generic historical backfill checkpoint tables tracked RAW bars only; factor
  work had no market-wide checkpoint/resume path.

## 3. Original coverage

Original factor table: 242,293 rows, 497 symbols, `2024-08-01` to `2026-08-06`.
Canonical eligible RAW: 2,645,073 symbol-date rows, 5,593 symbols.

| Slice | RAW symbols | matched symbols | symbol coverage | RAW rows | matched rows | row coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Overall | 5,593 | 497 | 8.886108% | 2,645,073 | 241,824 | 9.142432% |
| 2024 | 5,388 | 496 | 9.205642% | 545,708 | 50,514 | 9.256599% |
| 2025 | 5,500 | 497 | 9.036364% | 1,313,898 | 120,409 | 9.164258% |
| 2026 | 5,558 | 497 | 8.942065% | 785,467 | 70,901 | 9.026605% |
| SH | 2,339 | 210 | 8.978196% | 1,113,439 | 102,246 | 9.182901% |
| SZ | 2,919 | 287 | 9.832134% | 1,395,891 | 139,578 | 9.999205% |
| BJ | 335 | 0 | 0% | 135,743 | 0 | 0% |
| Active | 5,534 | 497 | 8.980846% | 2,631,551 | 241,824 | 9.189410% |
| Delisted | 53 | 0 | 0% | 13,250 | 0 | 0% |
| Current ST/*ST | 226 | 5 | 2.212389% | 104,471 | 2,431 | 2.326962% |
| Listed within 1 year | 74 | 0 | 0% | 9,172 | 0 | 0% |
| Listed within 2 years | 90 | 0 | 0% | 32,638 | 0 | 0% |

Historical ST effective-date slice was 298 symbols / 52,124 rows; only 7 symbols
and 730 rows matched factors.

## 4. Repair

The repair uses the existing Tushare client and existing `adj_factor` API, but
queries by `trade_date`. A real capability probe returned 5,552 rows for
`2026-08-06` in 180.43 ms, including SH, SZ, and BJ. This converts an estimated
5,593 per-symbol request plan into 493 bounded trading-date requests.

Implementation properties:

- derives dates and missing symbol-date pairs from canonical RAW bars;
- never selects only currently active securities;
- filters returned rows to the actual missing RAW pairs;
- validates normalized A-share symbol, exact date, and `adj_factor > 0`;
- uses stable factor IDs/content hashes and `ON CONFLICT DO NOTHING`;
- writes through one maintenance process, with no concurrent Provider requests;
- checkpoints every trade date and resumes only PENDING/FAILED items;
- batches DuckDB index maintenance while preserving per-date checkpoint state;
- records `ADJ_FACTOR_BACKFILL_V1`, source, counts, latency, sanitized errors,
  timestamps, and content hash in research audit tables;
- a repeat after completion performs zero Provider calls and zero factor writes;
- later new listings are covered by rerunning the same missing-pair scan after
  their RAW bars arrive; no daemon or high-frequency schedule was added.

One desktop tool process reached its approximately 50-minute execution ceiling.
Four committed batches remained valid; the in-flight batch rolled back. The
same run resumed from its durable item ledger. Fifty uncommitted trade dates had
to be requested again. Durable result: 493 successful dates, 0 Provider errors.
Actual backfill attempts were 543; including the initial capability probe, this
phase made 544 Tushare `adj_factor` calls. No other external Provider or LLM was
used.

Backfill inserted 2,403,249 new factor rows and updated none. Final table:
2,645,542 rows / 5,593 symbols. The 469 rows above eligible RAW count are valid
pre-existing factor dates without a matching canonical RAW quote; they are not
used by the Label join.

Wall-clock maintenance ran from 12:09 to 15:09 China time. Durable successful
Provider request latency summed to 98.53 seconds; normalization was small
relative to DuckDB unique-index maintenance, which dominated elapsed time.

## 5. Final coverage and quality

Every eligible RAW pair now has a positive factor:

| Slice | symbol coverage | row coverage |
| --- | ---: | ---: |
| Overall | 100% (5,593/5,593) | 100% (2,645,073/2,645,073) |
| 2024 / 2025 / 2026 | 100% | 100% |
| SH / SZ / BJ | 100% | 100% |
| Active / inactive-delisted | 100% | 100% |
| Current ST/*ST | 100% | 100% |
| Historical ST effective-date slice | 100% | 100% |
| Listed within 1 year / 2 years | 100% | 100% |

Quality checks:

- `NULL` factor: 0;
- factor `<= 0`: 0;
- duplicate symbol-date: 0;
- min/max factor: 1.0 / 10055.64;
- the maximum is a positive cumulative Tushare factor for `600653.SH`, not a
  missing-value substitute; it is retained and reported rather than clipped;
- no `COALESCE(adj_factor, 1)` or RAW fallback exists.

Corporate-action continuity samples demonstrate why RAW fallback is invalid:

| Symbol/date | RAW return | adjusted return | factor change |
| --- | ---: | ---: | ---: |
| 002594.SZ / 2025-07-29 | -66.94% | +0.37% | +203.58% |
| 603007.SH / 2024-12-26 | -43.53% | +4.95% | +85.87% |
| 920029.BJ / 2026-05-29 | -35.16% | -1.04% | +52.61% |
| 301613.SZ / 2025-05-20 | -36.20% | -3.14% | +51.82% |
| 688191.SH / 2025-06-06 | -32.74% | +2.02% | +51.67% |

Ten sampled delisted symbols had exact RAW/factor date matches; examples include
`000004.SZ` 433/433 dates, `000040.SZ` 121/121, and `000413.SZ` 10/10.

## 6. Label coverage regression

Frozen S3A definitions were used unchanged on the same `2026-07-01` EOD REPLAY
universe. The build used local DuckDB only: 5,513 packets, 55,130 labels, three
batch queries, 0 external APIs, 0 LLM, and no Feature evaluation.

| Label slice | valid | total | valid rate |
| --- | ---: | ---: | ---: |
| Overall | 54,654 | 55,130 | 99.136586% |
| Statistical | 27,540 | 27,565 | 99.909305% |
| Executable | 27,114 | 27,565 | 98.363867% |
| 1 trading day | 10,941 | 11,026 | 99.229095% |
| 3 trading days | 10,938 | 11,026 | 99.201886% |
| 5 trading days | 10,933 | 11,026 | 99.156539% |
| 10 trading days | 10,927 | 11,026 | 99.102122% |
| 20 trading days | 10,915 | 11,026 | 98.993289% |

Invalid reasons:

| Reason | Count | Share of all labels |
| --- | ---: | ---: |
| ADJUSTMENT_UNAVAILABLE | 0 | 0% |
| SUSPENDED_ENTRY | 370 | 0.671141% |
| SUSPENDED_EXIT | 3 | 0.005441% |
| LIMIT_UP_LOCKED_ENTRY | 55 | 0.099764% |
| LIMIT_DOWN_LOCKED_EXIT | 5 | 0.009070% |
| MISSING_PRICE / NO_VALID_QUOTE | 43 | 0.077997% |
| DELISTED | 0 | 0% |
| INSUFFICIENT_FORWARD_WINDOW | 0 | 0% |
| OTHER | 0 | 0% |

Batch loading took 3,848.46 ms, deterministic Label calculation 5,050.12 ms,
and total builder time 9,117.47 ms. There is no symbol-by-horizon N+1.

## 7. Survivorship and S3B gate

The original missing group was structurally biased toward BJ, inactive/delisted,
ST, unknown-master, and recent listings. Final eligible coverage is 100% in each
audited group. Label invalidity is now dominated by real tradability/quote
semantics, not adjustment data.

S3B data gate: **PASS**. This only permits the next research phase; it is not an
Alpha conclusion. S3B was not started in this phase.

