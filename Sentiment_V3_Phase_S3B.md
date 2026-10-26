# Sentiment V3 Phase S3B Preflight Audit

## 1. Status

`S3B_PREFLIGHT_BLOCKED` — the single-feature experiment was not started.

The S3A.1 gate proved that adjustment factors and forward labels can be built
correctly for one EOD cross-section. It did not create the multi-date
`ResearchFeaturePacket` history required by S3B. Continuing would require
inventing missing features, silently dropping frozen features, or treating a
single date as a historical experiment. All three violate the frozen protocol.

Current branch: `research/sentiment-v3`.

## 2. Frozen definitions

No frozen definition was modified.

| Definition | SHA256 / version |
| --- | --- |
| `trading/research/sentiment/v3/feature_registry.py` | `779AD72DEC929AC42FC7A65FB5E3E7A94913C44E8F1010FC63B9181030466080` |
| Registry machine content hash | `20f80a62ec073fbe44622cdb6fbcba1c9b02c4abf66823989bc2bff0d9a8b726` |
| `Sentiment_V3_Label_Spec_V1.md` | `49D6BAB04E3548A7A57606B93A3E326797AA3981C7DD1DD296374FDC459EF1D0` |
| `trading/research/sentiment/v3/labels.py` | `705AED453F510EDF4BF4BC63AC6C942748BE4AC11A2CA595F2121A4E29DE9FB2` |
| Label version | `LABEL_SPEC_V1` |
| `Sentiment_V3_Evaluation_Framework_V1.md` | `F2D3D2059B63FD4A5FBC6963A9F16EAEC9EBC49D47FEA89D48D94E67C4859138` |
| `trading/research/sentiment/v3/evaluation.py` | `6573BDC3B3BD194971CFA4B94E64F6D555E579CBC4B123D94AA074652185E3C2` |
| Evaluation version | `EVALUATION_FRAMEWORK_V1` |

Registry counts remain 132 entries, 124 canonical features, 8 aliases, and 39
canonical `HISTORY_SAFE_V1` features. All formal weights remain zero.

## 3. Blocking evidence

The main DuckDB has no persisted V3 research dataset:

| Required table | State |
| --- | --- |
| `sentiment_v3_feature_packets` | missing |
| `sentiment_v3_forward_labels` | missing |
| `sentiment_v3_snapshots` | missing |

Existing similarly named tables cannot substitute for the frozen S3B contract:

| Existing table | Rows / range | Why it is not S3B input |
| --- | --- | --- |
| `forward_return_labels` | 16,480 rows; 766 symbols; 42 signal dates from 2026-06-01 to 2026-07-30; `forward-return-raw-v1` | no V3 `feature_packet_id`, cutoff lineage, or `LABEL_SPEC_V1` identity |
| `factor_outputs` | 24 rows; 11 symbols | old score-oriented factors, not 39 frozen V3 features |
| `capital_flow_symbol_snapshots` | 11 rows; one cutoff | old scored capital snapshot, not V3 capital facts |
| `sentiment_market_snapshots` | 0 rows | no usable history |
| `sentiment_symbol_snapshots` | 2 rows; 2 symbols; one cutoff | old V2-style score snapshot |

S3A and S3A.1 generated 5,513 packets and 55,130 labels for the single signal
date `2026-07-01` as an in-memory/local dry run. The documented purpose was
Label validation only; no Feature evaluation was performed and the packets were
not persisted as a multi-date research dataset. One IC date cannot support IC
time-series statistics, annual slices, time stability, or cross-horizon
repeatability.

The database contains no historical margin-financing or dragon-tiger source
tables, and the repository contains no historical replay/backfill path for
those sources. Therefore these ten frozen features have no valid historical
observations:

- four `capital.dragon_tiger.*` features;
- six `capital.margin.*` features.

`missing != 0` remains mandatory. No missing dragon-tiger observation was
converted to `has_dragon_tiger = false`, and no missing capital amount was
converted to zero.

## 4. Why the experiment stopped

The requested matrix requires 39 canonical features × 10 frozen Label Specs =
390 evaluations over one common multi-date REPLAY period. The available local
state cannot produce that matrix without changing the experiment:

1. evaluating only bar-derived Market/Stock features would no longer be exactly
   39 frozen features;
2. filling unavailable Capital features with zero would violate missing
   semantics;
3. reconstructing only `2026-07-01` would create one IC date and no time
   stability evidence;
4. using legacy `forward_return_labels` would change the frozen Label contract;
5. changing `READY_FOR_HISTORY` flags now would modify Registry V1 during the
   experiment.

This is a feature-history availability gate failure, not an adjustment-factor
or Label Builder failure. The S3A.1 statement `S3B data gate: PASS` applied to
adj-factor/Label coverage only.

## 5. Frozen 39-feature readiness matrix

No empirical metric or Alpha classification is reported. `BLOCKED_NO_REPLAY`
means that a valid multi-date `ResearchFeaturePacket` series is absent.
`BLOCKED_NO_HISTORICAL_SOURCE` additionally means the underlying historical
source is absent.

| feature_name | module | methodology_class | unit | algorithm_version | research_status | preflight_status |
| --- | --- | --- | --- | --- | --- | --- |
| capital.dragon_tiger.has_dragon_tiger | capital.dragon_tiger | EVENT_CAPITAL | BOOLEAN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.dragon_tiger.institution_net_amount | capital.dragon_tiger | EVENT_CAPITAL | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.dragon_tiger.institution_seat_count | capital.dragon_tiger | EVENT_CAPITAL | COUNT | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.dragon_tiger.net_amount | capital.dragon_tiger | EVENT_CAPITAL | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.margin.margin_balance | capital.margin | OBSERVABLE_FACT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.margin.margin_balance_change | capital.margin | OBSERVABLE_FACT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.margin.margin_balance_change_pct | capital.margin | OBSERVABLE_FACT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.margin.margin_buy | capital.margin | OBSERVABLE_FACT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.margin.margin_net_buy | capital.margin | OBSERVABLE_FACT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.margin.margin_repay | capital.margin | OBSERVABLE_FACT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_HISTORICAL_SOURCE |
| capital.stock.turnover | capital.stock | OBSERVABLE_FACT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.advance_decline_ratio | market | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.advance_ratio | market | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.advancers | market | DERIVED_TRANSPARENT | COUNT | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.breadth_strength | market | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.coverage_ratio | market | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.decline_ratio | market | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.decliners | market | DERIVED_TRANSPARENT | COUNT | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.market_turnover | market | DERIVED_TRANSPARENT | CNY | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.market_turnover_change | market | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| market.unchanged | market | DERIVED_TRANSPARENT | COUNT | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.amplitude | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.close_location_value | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.gap_and_fade | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.gap_and_follow_through | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.high_to_close_drawdown | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.intraday_return_pct | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.is_broken_board | stock | OBSERVABLE_FACT | BOOLEAN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.is_limit_down | stock | OBSERVABLE_FACT | BOOLEAN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.is_limit_up | stock | OBSERVABLE_FACT | BOOLEAN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.low_to_close_recovery | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.open_gap_pct | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.relative_market_return | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.relative_market_volume | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.return_pct | stock | DERIVED_TRANSPARENT | RETURN | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.turnover_change | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.turnover_rate | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.volume_change | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |
| stock.volume_ratio | stock | DERIVED_TRANSPARENT | RATIO | sentiment-v3-feature-assembly-s2e-1.0.0 | EOD_ONLY, READY_FOR_HISTORY | BLOCKED_NO_REPLAY |

## 6. Required remediation before S3B

This is a separate data-preparation phase, not part of the frozen S3B run:

1. define and persist a multi-date REPLAY `ResearchFeaturePacket` dataset using
   the frozen Registry V1 and PIT/finality rules;
2. audit/backfill historical margin-financing and dragon-tiger facts, including
   conservative `available_time`, or formally create Registry V2 if those
   features cannot be history-safe;
3. persist matching `LABEL_SPEC_V1` labels keyed by packet ID;
4. validate common date coverage, right-edge truncation, record-source
   isolation, and all 39 feature availability;
5. rerun this preflight and only then create the 390-result matrix.

## 7. Isolation

- real Feature evaluations: 0;
- IC / RankIC / quantile / TopN runs: 0;
- Feature combinations or machine learning: 0;
- weight optimization or A-baseline comparison: 0;
- external API calls and LLM calls: 0;
- strategy, DecisionEngine, A/B Shadow, Canonical Shadow, and
  `FORWARD_SHADOW_V1`: unchanged;
- orders, fills, TradePlan, and holdings changes: 0;
- Git write operations: 0.

