# Sentiment V3 S3B1 Evaluation Report

## 1. Dataset

- Evaluation ID: sv3_s3b1_d671555ffc902594a635d7a172d7dae2
- Dataset: sv3_s3b_replay_222e9321c2719ecc90bfe149cbf394a1
- Dataset hash: 222e9321c2719ecc90bfe149cbf394a138a99fb46d2e7d884669ec39b4f92fa5
- Date range: 2024-08-01 to 2026-08-13
- Features: 38
- Labels: 5 Statistical + 5 Executable
- Results: 380
- Excluded LOW_COVERAGE dates: 77
- IC method: daily trade_date + cutoff cross-sectional Pearson
- RankIC method: daily trade_date + cutoff cross-sectional Spearman
- Multiple tests: 190; BH-FDR retained, no result deleted
- Market regime: NOT_AVAILABLE; no regime was invented

## 2. Feature evaluation table

RankIC and spread columns are 1D / 3D / 5D / 10D / 20D Statistical results.

| Feature | Classification | Mean coverage | RankIC | Q5-Q1 spread | Evidence |
|---|---|---:|---|---|---|
| capital.dragon_tiger.has_dragon_tiger | WEAK | 98.93% | -0.01274 / -0.03127 / -0.04094 / -0.04619 / -0.04932 | NA / NA / NA / NA / NA | EVIDENCE_BELOW_PROMISING_RULE |
| capital.dragon_tiger.institution_net_amount | PROMISING | 1.17% | 0.07228 / 0.06029 / 0.05396 / 0.04241 / 0.03621 | 0.01153 / 0.01589 / 0.01499 / 0.01614 / 0.02987 | POSITIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.dragon_tiger.institution_seat_count | WEAK | 1.17% | 0.01769 / 0.02770 / 0.02777 / 0.02179 / 0.01417 | NA / NA / NA / NA / NA | EVIDENCE_BELOW_PROMISING_RULE |
| capital.dragon_tiger.net_amount | PROMISING | 1.17% | 0.19057 / 0.15336 / 0.12378 / 0.09437 / 0.07675 | 0.03185 / 0.04527 / 0.04392 / 0.04504 / 0.04562 | POSITIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.margin.margin_balance | WEAK | 65.18% | -0.00299 / -0.00320 / -0.00497 / -0.01451 / -0.02001 | -0.00006 / -0.00014 / -0.00005 / -0.00006 / 0.00087 | EVIDENCE_BELOW_PROMISING_RULE |
| capital.margin.margin_balance_change | PROMISING | 65.01% | -0.01287 / -0.01211 / -0.01356 / -0.01026 / -0.01330 | -0.00012 / -0.00024 / -0.00022 / 0.00023 / -0.00140 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.margin.margin_balance_change_pct | PROMISING | 65.01% | -0.01324 / -0.01249 / -0.01383 / -0.00912 / -0.01319 | -0.00015 / -0.00037 / -0.00048 / 0.00018 / -0.00187 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.margin.margin_buy | PROMISING | 65.18% | -0.04654 / -0.05353 / -0.06017 / -0.07203 / -0.07937 | -0.00094 / -0.00220 / -0.00304 / -0.00426 / -0.00377 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.margin.margin_net_buy | PROMISING | 65.18% | -0.01298 / -0.01214 / -0.01358 / -0.01046 / -0.01346 | -0.00012 / -0.00023 / -0.00020 / 0.00021 / -0.00140 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.margin.margin_repay | PROMISING | 65.18% | -0.04388 / -0.05094 / -0.05741 / -0.06968 / -0.07756 | -0.00093 / -0.00215 / -0.00288 / -0.00441 / -0.00376 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| capital.stock.turnover | PROMISING | 98.93% | -0.06403 / -0.07658 / -0.08745 / -0.10596 / -0.12240 | -0.00185 / -0.00479 / -0.00732 / -0.01281 / -0.02136 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| market.advance_decline_ratio | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.advance_ratio | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.advancers | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.breadth_strength | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.decline_ratio | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.decliners | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.market_turnover | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.market_turnover_change | INSUFFICIENT_SAMPLE | 98.69% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| market.unchanged | INSUFFICIENT_SAMPLE | 98.93% | NA / NA / NA / NA / NA | NA / NA / NA / NA / NA | FEWER_THAN_3_USABLE_HORIZONS |
| stock.amplitude | PROMISING | 98.93% | -0.06360 / -0.07018 / -0.07207 / -0.06975 / -0.06234 | -0.00073 / -0.00167 / -0.00189 / -0.00120 / 0.00523 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.close_location_value | WEAK | 98.75% | -0.01587 / -0.01115 / -0.01134 / -0.00799 / -0.00997 | 0.00047 / 0.00092 / 0.00018 / 0.00146 / -0.00129 | EVIDENCE_BELOW_PROMISING_RULE |
| stock.gap_and_fade | WEAK | 98.93% | -0.01524 / -0.01827 / -0.01705 / -0.01915 / -0.01715 | 0.00685 / 0.01747 / 0.03032 / 0.02109 / 0.04407 | EVIDENCE_BELOW_PROMISING_RULE |
| stock.gap_and_follow_through | PROMISING | 98.93% | -0.00814 / -0.00694 / -0.01117 / -0.01079 / -0.01382 | -0.01736 / -0.00636 / -0.00748 / -0.01596 / -0.05586 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.high_to_close_drawdown | WEAK | 98.93% | -0.03170 / -0.03523 / -0.03512 / -0.03480 / -0.02728 | -0.00093 / -0.00036 / 0.00088 / 0.00136 / 0.01016 | EVIDENCE_BELOW_PROMISING_RULE |
| stock.intraday_return_pct | PROMISING | 98.93% | -0.02468 / -0.02136 / -0.02250 / -0.01796 / -0.02223 | 0.00044 / -0.00040 / -0.00140 / -0.00021 / -0.00326 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.is_broken_board | WEAK | 98.93% | -0.02776 / -0.02597 / -0.02783 / -0.02807 / -0.02567 | NA / NA / NA / NA / NA | EVIDENCE_BELOW_PROMISING_RULE |
| stock.is_limit_down | WEAK | 98.93% | -0.04307 / -0.03968 / -0.03529 / -0.03069 / -0.02753 | NA / NA / NA / NA / NA | EVIDENCE_BELOW_PROMISING_RULE |
| stock.is_limit_up | TAIL_DEPENDENT | 98.93% | 0.03727 / 0.00756 / -0.00971 / -0.02214 / -0.02972 | NA / NA / NA / NA / NA | CROSS_HORIZON_DIRECTION_REVERSAL, TAIL_5PCT_LOSS_H1, TAIL_5PCT_LOSS_H3 |
| stock.low_to_close_recovery | PROMISING | 98.93% | -0.05234 / -0.05011 / -0.04891 / -0.04553 / -0.04272 | -0.00045 / -0.00097 / -0.00123 / 0.00043 / 0.00163 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.open_gap_pct | PROMISING | 98.93% | 0.02440 / 0.02483 / 0.02262 / 0.02478 / 0.02960 | 0.00244 / 0.00355 / 0.00394 / 0.00594 / 0.00907 | POSITIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.relative_market_return | WEAK | 98.93% | -0.01448 / -0.01133 / -0.01391 / -0.00891 / -0.01140 | 0.00164 / 0.00128 / 0.00037 / 0.00212 / -0.00007 | EVIDENCE_BELOW_PROMISING_RULE |
| stock.relative_market_volume | WEAK | 63.19% | -0.01734 / -0.00699 / -0.00988 / -0.00619 / -0.00595 | -0.00045 / -0.00026 / -0.00112 / -0.00088 / -0.00159 | EVIDENCE_BELOW_PROMISING_RULE |
| stock.return_pct | WEAK | 98.93% | -0.01448 / -0.01133 / -0.01391 / -0.00891 / -0.01140 | 0.00164 / 0.00128 / 0.00037 / 0.00212 / -0.00007 | EVIDENCE_BELOW_PROMISING_RULE |
| stock.turnover_change | PROMISING | 98.68% | -0.01827 / -0.01169 / -0.01289 / -0.00784 / -0.00667 | -0.00050 / -0.00103 / -0.00175 / -0.00111 / -0.00193 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.turnover_rate | PROMISING | 98.93% | -0.06287 / -0.07241 / -0.07698 / -0.07654 / -0.07848 | -0.00110 / -0.00239 / -0.00316 / -0.00349 / -0.00019 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.volume_change | PROMISING | 98.68% | -0.01866 / -0.01180 / -0.01235 / -0.00806 / -0.00634 | -0.00063 / -0.00111 / -0.00174 / -0.00119 / -0.00171 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |
| stock.volume_ratio | PROMISING | 98.89% | -0.02882 / -0.02299 / -0.02613 / -0.01828 / -0.01613 | -0.00078 / -0.00200 / -0.00324 / -0.00213 / -0.00361 | NEGATIVE_DIRECTION, MULTI_HORIZON_CONSISTENT |

## 3. IC / RankIC research ordering

Ordering is for audit browsing only; it is not a strategy ranking or weight.

### 3.1 Pearson IC ordering

| Order | Feature | Classification | IC 1/3/5/10/20 |
|---:|---|---|---|
| 1 | capital.dragon_tiger.net_amount | PROMISING | 0.13665 / 0.10570 / 0.08409 / 0.06881 / 0.05915 |
| 2 | stock.is_limit_up | TAIL_DEPENDENT | 0.07706 / 0.04285 / 0.01951 / 0.00258 / -0.00587 |
| 3 | capital.dragon_tiger.institution_net_amount | PROMISING | 0.06002 / 0.04709 / 0.03885 / 0.03214 / 0.03545 |
| 4 | stock.turnover_rate | PROMISING | -0.03501 / -0.05107 / -0.05740 / -0.05801 / -0.05325 |
| 5 | stock.is_limit_down | WEAK | -0.05740 / -0.05171 / -0.04384 / -0.03280 / -0.02439 |
| 6 | stock.open_gap_pct | PROMISING | 0.03696 / 0.02904 / 0.01977 / 0.01339 / 0.01308 |
| 7 | stock.amplitude | PROMISING | -0.01884 / -0.03120 / -0.03664 / -0.03500 / -0.02267 |
| 8 | capital.stock.turnover | PROMISING | -0.01484 / -0.02098 / -0.02475 / -0.03074 / -0.03182 |
| 9 | capital.dragon_tiger.has_dragon_tiger | WEAK | -0.00044 / -0.01965 / -0.02929 / -0.03027 / -0.02912 |
| 10 | stock.low_to_close_recovery | PROMISING | -0.00376 / -0.02124 / -0.02971 / -0.02937 / -0.02440 |
| 11 | stock.volume_ratio | PROMISING | -0.02066 / -0.02365 / -0.02696 / -0.02238 / -0.02005 |
| 12 | stock.high_to_close_drawdown | WEAK | -0.02647 / -0.02644 / -0.02426 / -0.02007 / -0.00816 |
| 13 | stock.is_broken_board | WEAK | -0.02585 / -0.02210 / -0.02115 / -0.01805 / -0.01484 |
| 14 | stock.relative_market_return | WEAK | 0.02538 / 0.01068 / -0.00118 / -0.00298 / -0.00747 |
| 15 | stock.return_pct | WEAK | 0.02538 / 0.01068 / -0.00118 / -0.00298 / -0.00747 |
| 16 | stock.gap_and_fade | WEAK | -0.01128 / -0.01890 / -0.02134 / -0.02283 / -0.01703 |
| 17 | capital.margin.margin_buy | PROMISING | -0.00724 / -0.01075 / -0.01306 / -0.01800 / -0.01781 |
| 18 | capital.margin.margin_repay | PROMISING | -0.00818 / -0.01088 / -0.01291 / -0.01744 / -0.01658 |
| 19 | stock.turnover_change | PROMISING | -0.01207 / -0.01470 / -0.01675 / -0.01492 / -0.01282 |
| 20 | stock.volume_change | PROMISING | -0.01379 / -0.01540 / -0.01661 / -0.01478 / -0.01239 |
| 21 | stock.intraday_return_pct | PROMISING | 0.00908 / -0.00481 / -0.01287 / -0.01158 / -0.01563 |
| 22 | capital.dragon_tiger.institution_seat_count | WEAK | 0.01402 / 0.01478 / 0.01344 / 0.01034 / 0.01128 |
| 23 | stock.relative_market_volume | WEAK | -0.01356 / -0.01346 / -0.01428 / -0.01249 / -0.01023 |
| 24 | capital.margin.margin_balance_change_pct | PROMISING | -0.00299 / -0.00721 / -0.01022 / -0.00753 / -0.01029 |
| 25 | stock.close_location_value | WEAK | 0.00227 / -0.00345 / -0.00704 / -0.00640 / -0.00914 |
| 26 | capital.margin.margin_balance | WEAK | -0.00079 / -0.00090 / -0.00177 / -0.00654 / -0.00908 |
| 27 | stock.gap_and_follow_through | PROMISING | 0.00743 / 0.00137 / -0.00412 / -0.00822 / -0.00700 |
| 28 | capital.margin.margin_balance_change | PROMISING | 0.00310 / 0.00207 / 0.00035 / 0.00119 / -0.00289 |
| 29 | capital.margin.margin_net_buy | PROMISING | 0.00297 / 0.00244 / 0.00079 / 0.00142 / -0.00270 |
| 30 | market.advance_decline_ratio | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 31 | market.advance_ratio | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 32 | market.advancers | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 33 | market.breadth_strength | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 34 | market.decline_ratio | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 35 | market.decliners | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 36 | market.market_turnover | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 37 | market.market_turnover_change | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 38 | market.unchanged | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |

### 3.2 RankIC ordering

| Order | Feature | Classification | RankIC 1/3/5/10/20 |
|---:|---|---|---|
| 1 | capital.dragon_tiger.net_amount | PROMISING | 0.19057 / 0.15336 / 0.12378 / 0.09437 / 0.07675 |
| 2 | capital.stock.turnover | PROMISING | -0.06403 / -0.07658 / -0.08745 / -0.10596 / -0.12240 |
| 3 | capital.margin.margin_buy | PROMISING | -0.04654 / -0.05353 / -0.06017 / -0.07203 / -0.07937 |
| 4 | stock.turnover_rate | PROMISING | -0.06287 / -0.07241 / -0.07698 / -0.07654 / -0.07848 |
| 5 | capital.margin.margin_repay | PROMISING | -0.04388 / -0.05094 / -0.05741 / -0.06968 / -0.07756 |
| 6 | capital.dragon_tiger.institution_net_amount | PROMISING | 0.07228 / 0.06029 / 0.05396 / 0.04241 / 0.03621 |
| 7 | stock.amplitude | PROMISING | -0.06360 / -0.07018 / -0.07207 / -0.06975 / -0.06234 |
| 8 | stock.low_to_close_recovery | PROMISING | -0.05234 / -0.05011 / -0.04891 / -0.04553 / -0.04272 |
| 9 | capital.dragon_tiger.has_dragon_tiger | WEAK | -0.01274 / -0.03127 / -0.04094 / -0.04619 / -0.04932 |
| 10 | stock.is_limit_down | WEAK | -0.04307 / -0.03968 / -0.03529 / -0.03069 / -0.02753 |
| 11 | stock.is_limit_up | TAIL_DEPENDENT | 0.03727 / 0.00756 / -0.00971 / -0.02214 / -0.02972 |
| 12 | stock.high_to_close_drawdown | WEAK | -0.03170 / -0.03523 / -0.03512 / -0.03480 / -0.02728 |
| 13 | stock.open_gap_pct | PROMISING | 0.02440 / 0.02483 / 0.02262 / 0.02478 / 0.02960 |
| 14 | stock.volume_ratio | PROMISING | -0.02882 / -0.02299 / -0.02613 / -0.01828 / -0.01613 |
| 15 | stock.is_broken_board | WEAK | -0.02776 / -0.02597 / -0.02783 / -0.02807 / -0.02567 |
| 16 | capital.dragon_tiger.institution_seat_count | WEAK | 0.01769 / 0.02770 / 0.02777 / 0.02179 / 0.01417 |
| 17 | stock.intraday_return_pct | PROMISING | -0.02468 / -0.02136 / -0.02250 / -0.01796 / -0.02223 |
| 18 | capital.margin.margin_balance | WEAK | -0.00299 / -0.00320 / -0.00497 / -0.01451 / -0.02001 |
| 19 | stock.gap_and_fade | WEAK | -0.01524 / -0.01827 / -0.01705 / -0.01915 / -0.01715 |
| 20 | stock.volume_change | PROMISING | -0.01866 / -0.01180 / -0.01235 / -0.00806 / -0.00634 |
| 21 | stock.turnover_change | PROMISING | -0.01827 / -0.01169 / -0.01289 / -0.00784 / -0.00667 |
| 22 | stock.relative_market_volume | WEAK | -0.01734 / -0.00699 / -0.00988 / -0.00619 / -0.00595 |
| 23 | stock.close_location_value | WEAK | -0.01587 / -0.01115 / -0.01134 / -0.00799 / -0.00997 |
| 24 | stock.relative_market_return | WEAK | -0.01448 / -0.01133 / -0.01391 / -0.00891 / -0.01140 |
| 25 | stock.return_pct | WEAK | -0.01448 / -0.01133 / -0.01391 / -0.00891 / -0.01140 |
| 26 | capital.margin.margin_balance_change_pct | PROMISING | -0.01324 / -0.01249 / -0.01383 / -0.00912 / -0.01319 |
| 27 | stock.gap_and_follow_through | PROMISING | -0.00814 / -0.00694 / -0.01117 / -0.01079 / -0.01382 |
| 28 | capital.margin.margin_net_buy | PROMISING | -0.01298 / -0.01214 / -0.01358 / -0.01046 / -0.01346 |
| 29 | capital.margin.margin_balance_change | PROMISING | -0.01287 / -0.01211 / -0.01356 / -0.01026 / -0.01330 |
| 30 | market.advance_decline_ratio | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 31 | market.advance_ratio | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 32 | market.advancers | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 33 | market.breadth_strength | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 34 | market.decline_ratio | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 35 | market.decliners | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 36 | market.market_turnover | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 37 | market.market_turnover_change | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |
| 38 | market.unchanged | INSUFFICIENT_SAMPLE | NA / NA / NA / NA / NA |

## 4. Classification counts

- PROMISING: 17
- WEAK: 11
- UNSTABLE: 0
- TAIL_DEPENDENT: 1
- NO_SIGNAL: 0
- INSUFFICIENT_SAMPLE: 9

## 5. Stable research candidates

capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount, capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay, capital.stock.turnover, stock.amplitude, stock.gap_and_follow_through, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio

## 6. Failed or insufficient features

market.advance_decline_ratio (INSUFFICIENT_SAMPLE), market.advance_ratio (INSUFFICIENT_SAMPLE), market.advancers (INSUFFICIENT_SAMPLE), market.breadth_strength (INSUFFICIENT_SAMPLE), market.decline_ratio (INSUFFICIENT_SAMPLE), market.decliners (INSUFFICIENT_SAMPLE), market.market_turnover (INSUFFICIENT_SAMPLE), market.market_turnover_change (INSUFFICIENT_SAMPLE), market.unchanged (INSUFFICIENT_SAMPLE)

## 7. Risk notes

- PROMISING is a research classification, not an Alpha or production conclusion.
- Market-level daily features are constant inside each daily stock cross-section; strict IC therefore reports zero variance instead of substituting a time-series correlation.
- CURRENT_MEMBERSHIP_ONLY and PIT-unsafe features are absent.
- Vendor-derived data must not be interpreted as verified institution flow.
- Statistical and Executable results remain separate.
- Statistical-label tail tests remove each date's top 1%, 5%, and 10% winners and recompute daily IC/RankIC and Q5 spread.
- FDR uses a documented normal approximation to the daily RankIC mean because SciPy is not a project dependency.

## 8. Isolation and performance

- Evaluation time: 6746.541s
- Result DB write time: 326.306s
- External API calls: 0
- LLM calls: 0
- formal_weight: 0
- DecisionEngine, 60/40, A/B Shadow and Canonical Shadow unchanged.

## 9. Conclusion

No formal Alpha conclusion, strategy, prediction, BUY/SELL/HOLD, or production weight is produced.
