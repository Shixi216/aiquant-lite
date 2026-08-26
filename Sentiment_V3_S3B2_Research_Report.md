# Sentiment V3 S3B2 Research Report

## 1. Frozen research input

- Run ID: sv3_s3b2_e9cb6272813d0ec6b5a346d7b286699f
- Source Evaluation: sv3_s3b1_d671555ffc902594a635d7a172d7dae2
- Dataset: sv3_s3b_replay_222e9321c2719ecc90bfe149cbf394a1
- Features: 38
- S3B1 PROMISING input: 17
- Registry, Label Spec and Evaluation Framework: unchanged

## 2. Feature correlation

Pearson/Spearman threshold: abs(value) > 0.80. Market pairs use daily observations; non-market pairs use Packet observations; cross-grain pairs use daily aggregates.

| Left | Right | Pearson | Spearman | N | Grain |
|---|---|---:|---:|---:|---|
| capital.margin.margin_balance | market.market_turnover | 0.828845 | 0.828097 | 416 | DAILY_AGGREGATE |
| capital.margin.margin_balance_change | capital.margin.margin_balance_change_pct | 0.368421 | 0.908335 | 1470415 | PACKET |
| capital.margin.margin_balance_change | capital.margin.margin_net_buy | 0.999684 | 0.999664 | 1470414 | PACKET |
| capital.margin.margin_balance_change_pct | capital.margin.margin_net_buy | 0.368199 | 0.907985 | 1470414 | PACKET |
| capital.margin.margin_buy | capital.margin.margin_repay | 0.955598 | 0.954267 | 1474291 | PACKET |
| capital.margin.margin_buy | capital.stock.turnover | 0.952949 | 0.941265 | 1474292 | PACKET |
| capital.margin.margin_buy | market.market_turnover | 0.963581 | 0.965713 | 416 | DAILY_AGGREGATE |
| capital.margin.margin_repay | capital.stock.turnover | 0.937801 | 0.939500 | 1474291 | PACKET |
| capital.margin.margin_repay | market.market_turnover | 0.977915 | 0.978726 | 416 | DAILY_AGGREGATE |
| capital.stock.turnover | market.market_turnover | 0.999891 | 0.999855 | 416 | DAILY_AGGREGATE |
| market.advance_decline_ratio | market.advance_ratio | 0.202847 | 0.999777 | 416 | DAILY |
| market.advance_decline_ratio | market.advancers | 0.200974 | 0.999517 | 416 | DAILY |
| market.advance_decline_ratio | market.breadth_strength | 0.198649 | 0.999952 | 416 | DAILY |
| market.advance_decline_ratio | market.decline_ratio | -0.194383 | -0.999766 | 416 | DAILY |
| market.advance_decline_ratio | market.decliners | -0.194378 | -0.999472 | 416 | DAILY |
| market.advance_decline_ratio | stock.close_location_value | 0.199572 | 0.856356 | 416 | DAILY_AGGREGATE |
| market.advance_decline_ratio | stock.intraday_return_pct | 0.309482 | 0.898381 | 416 | DAILY_AGGREGATE |
| market.advance_decline_ratio | stock.return_pct | 0.402200 | 0.976683 | 416 | DAILY_AGGREGATE |
| market.advance_ratio | market.advancers | 0.999901 | 0.999846 | 416 | DAILY |
| market.advance_ratio | market.breadth_strength | 0.999728 | 0.999790 | 416 | DAILY |
| market.advance_ratio | market.decline_ratio | -0.998924 | -0.999309 | 416 | DAILY |
| market.advance_ratio | market.decliners | -0.998619 | -0.998914 | 416 | DAILY |
| market.advance_ratio | stock.close_location_value | 0.856244 | 0.857191 | 416 | DAILY_AGGREGATE |
| market.advance_ratio | stock.intraday_return_pct | 0.832992 | 0.898628 | 416 | DAILY_AGGREGATE |
| market.advance_ratio | stock.return_pct | 0.861019 | 0.977196 | 416 | DAILY_AGGREGATE |
| market.advancers | market.breadth_strength | 0.999569 | 0.999538 | 416 | DAILY |
| market.advancers | market.decline_ratio | -0.998704 | -0.998939 | 416 | DAILY |
| market.advancers | market.decliners | -0.998274 | -0.998393 | 416 | DAILY |
| market.advancers | stock.close_location_value | 0.856464 | 0.857624 | 416 | DAILY_AGGREGATE |
| market.advancers | stock.intraday_return_pct | 0.832620 | 0.898576 | 416 | DAILY_AGGREGATE |
| market.advancers | stock.return_pct | 0.860225 | 0.977163 | 416 | DAILY_AGGREGATE |
| market.breadth_strength | market.decline_ratio | -0.999733 | -0.999792 | 416 | DAILY |
| market.breadth_strength | market.decliners | -0.999524 | -0.999524 | 416 | DAILY |
| market.breadth_strength | stock.close_location_value | 0.856132 | 0.856118 | 416 | DAILY_AGGREGATE |
| market.breadth_strength | stock.intraday_return_pct | 0.834946 | 0.898475 | 416 | DAILY_AGGREGATE |
| market.breadth_strength | stock.return_pct | 0.861645 | 0.977025 | 416 | DAILY_AGGREGATE |
| market.decline_ratio | market.decliners | 0.999886 | 0.999843 | 416 | DAILY |
| market.decline_ratio | stock.close_location_value | -0.855563 | -0.854784 | 416 | DAILY_AGGREGATE |
| market.decline_ratio | stock.high_to_close_drawdown | 0.717396 | 0.801275 | 416 | DAILY_AGGREGATE |
| market.decline_ratio | stock.intraday_return_pct | -0.836434 | -0.898180 | 416 | DAILY_AGGREGATE |
| market.decline_ratio | stock.return_pct | -0.861803 | -0.976686 | 416 | DAILY_AGGREGATE |
| market.decliners | stock.close_location_value | -0.854562 | -0.854469 | 416 | DAILY_AGGREGATE |
| market.decliners | stock.high_to_close_drawdown | 0.717886 | 0.802710 | 416 | DAILY_AGGREGATE |
| market.decliners | stock.intraday_return_pct | -0.835980 | -0.897818 | 416 | DAILY_AGGREGATE |
| market.decliners | stock.return_pct | -0.861591 | -0.976708 | 416 | DAILY_AGGREGATE |
| market.market_turnover_change | stock.turnover_change | 0.919981 | 0.874697 | 415 | DAILY_AGGREGATE |
| market.market_turnover_change | stock.volume_change | 0.904791 | 0.864069 | 415 | DAILY_AGGREGATE |
| stock.close_location_value | stock.intraday_return_pct | 0.695434 | 0.800665 | 2234040 | PACKET |
| stock.close_location_value | stock.low_to_close_recovery | 0.608243 | 0.827856 | 2234040 | PACKET |
| stock.intraday_return_pct | stock.return_pct | 0.679973 | 0.894873 | 2238764 | PACKET |
| stock.relative_market_return | stock.return_pct | 0.915452 | 0.778584 | 2238764 | PACKET |
| stock.relative_market_volume | stock.turnover_change | 0.996597 | 0.922461 | 1431091 | PACKET |
| stock.relative_market_volume | stock.volume_change | 0.998065 | 0.927088 | 1431091 | PACKET |
| stock.turnover_change | stock.volume_change | 0.998617 | 0.997213 | 2233243 | PACKET |

No feature was removed; correlation only creates research groups.

## 3. Feature groups

- CORRELATION_CLUSTER_1 [CORRELATION]: capital.margin.margin_balance, capital.margin.margin_buy, capital.margin.margin_repay, capital.stock.turnover, market.market_turnover
- CORRELATION_CLUSTER_2 [CORRELATION]: capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_net_buy
- CORRELATION_CLUSTER_3 [CORRELATION]: market.advance_decline_ratio, market.advance_ratio, market.advancers, market.breadth_strength, market.decline_ratio, market.decliners, stock.close_location_value, stock.high_to_close_drawdown, stock.intraday_return_pct, stock.low_to_close_recovery, stock.relative_market_return, stock.return_pct
- CORRELATION_CLUSTER_4 [CORRELATION]: market.market_turnover_change, stock.relative_market_volume, stock.turnover_change, stock.volume_change
- DRAGON_TIGER_EVENT_CAPITAL [SEMANTIC]: capital.dragon_tiger.has_dragon_tiger, capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.institution_seat_count, capital.dragon_tiger.net_amount
- MARGIN_CAPITAL [SEMANTIC]: capital.margin.margin_balance, capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay
- MARKET_BREADTH [SEMANTIC]: market.advance_decline_ratio, market.advance_ratio, market.advancers, market.breadth_strength, market.decline_ratio, market.decliners, market.unchanged
- MARKET_LIQUIDITY [SEMANTIC]: market.market_turnover, market.market_turnover_change
- SPECULATION_STATUS [SEMANTIC]: stock.is_broken_board, stock.is_limit_down, stock.is_limit_up
- STOCK_LIQUIDITY [SEMANTIC]: capital.stock.turnover, stock.relative_market_volume, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio
- STOCK_PRICE_REACTION [SEMANTIC]: stock.amplitude, stock.close_location_value, stock.gap_and_fade, stock.gap_and_follow_through, stock.high_to_close_drawdown, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct, stock.relative_market_return, stock.return_pct

## 4. BH-FDR

- Declared tests: 190
- q <= 0.05: 118
- adjusted_p_value and q_value are the same BH-adjusted value; no row was deleted.
- Raw p-values inherit S3B1's documented normal approximation of the daily RankIC mean.

| Test | Raw p | Adjusted p | q | Rejected |
|---|---:|---:|---:|---|
| capital.dragon_tiger.institution_net_amount::1D | 2.26841e-30 | 1.8739e-29 | 1.8739e-29 | True |
| capital.dragon_tiger.institution_net_amount::3D | 2.84039e-20 | 1.79892e-19 | 1.79892e-19 | True |
| capital.dragon_tiger.institution_net_amount::5D | 4.518e-16 | 2.32005e-15 | 2.32005e-15 | True |
| capital.dragon_tiger.institution_net_amount::10D | 4.26487e-10 | 1.42162e-09 | 1.42162e-09 | True |
| capital.dragon_tiger.institution_net_amount::20D | 1.70042e-07 | 4.61543e-07 | 4.61543e-07 | True |
| capital.dragon_tiger.net_amount::1D | 2.55683e-208 | 4.85798e-206 | 4.85798e-206 | True |
| capital.dragon_tiger.net_amount::3D | 3.27839e-109 | 1.55724e-107 | 1.55724e-107 | True |
| capital.dragon_tiger.net_amount::5D | 1.13519e-61 | 1.26874e-60 | 1.26874e-60 | True |
| capital.dragon_tiger.net_amount::10D | 1.92266e-34 | 1.66048e-33 | 1.66048e-33 | True |
| capital.dragon_tiger.net_amount::20D | 4.79243e-23 | 3.64224e-22 | 3.64224e-22 | True |
| capital.margin.margin_balance_change::1D | 5.63778e-07 | 1.39114e-06 | 1.39114e-06 | True |
| capital.margin.margin_balance_change::3D | 6.25582e-07 | 1.52385e-06 | 1.52385e-06 | True |
| capital.margin.margin_balance_change::5D | 2.09312e-08 | 6.31259e-08 | 6.31259e-08 | True |
| capital.margin.margin_balance_change::10D | 0.000137696 | 0.000272524 | 0.000272524 | True |
| capital.margin.margin_balance_change::20D | 9.81928e-08 | 2.74362e-07 | 2.74362e-07 | True |
| capital.margin.margin_balance_change_pct::1D | 8.58266e-08 | 2.43389e-07 | 2.43389e-07 | True |
| capital.margin.margin_balance_change_pct::3D | 1.87792e-07 | 5.02542e-07 | 5.02542e-07 | True |
| capital.margin.margin_balance_change_pct::5D | 1.40301e-08 | 4.37004e-08 | 4.37004e-08 | True |
| capital.margin.margin_balance_change_pct::10D | 0.000520794 | 0.000979711 | 0.000979711 | True |
| capital.margin.margin_balance_change_pct::20D | 1.98958e-07 | 5.25027e-07 | 5.25027e-07 | True |
| capital.margin.margin_buy::1D | 6.95831e-09 | 2.20347e-08 | 2.20347e-08 | True |
| capital.margin.margin_buy::3D | 4.9873e-12 | 1.85801e-11 | 1.85801e-11 | True |
| capital.margin.margin_buy::5D | 1.31655e-14 | 5.95581e-14 | 5.95581e-14 | True |
| capital.margin.margin_buy::10D | 5.92244e-20 | 3.62988e-19 | 3.62988e-19 | True |
| capital.margin.margin_buy::20D | 2.64558e-22 | 1.79522e-21 | 1.79522e-21 | True |
| capital.margin.margin_net_buy::1D | 4.3381e-07 | 1.11384e-06 | 1.11384e-06 | True |
| capital.margin.margin_net_buy::3D | 5.44391e-07 | 1.36098e-06 | 1.36098e-06 | True |
| capital.margin.margin_net_buy::5D | 1.93375e-08 | 5.92602e-08 | 5.92602e-08 | True |
| capital.margin.margin_net_buy::10D | 0.000102275 | 0.000206725 | 0.000206725 | True |
| capital.margin.margin_net_buy::20D | 7.02706e-08 | 2.02294e-07 | 2.02294e-07 | True |
| capital.margin.margin_repay::1D | 5.70961e-08 | 1.66896e-07 | 1.66896e-07 | True |
| capital.margin.margin_repay::3D | 4.96929e-11 | 1.71666e-10 | 1.71666e-10 | True |
| capital.margin.margin_repay::5D | 2.2106e-13 | 9.33365e-13 | 9.33365e-13 | True |
| capital.margin.margin_repay::10D | 7.76845e-19 | 4.61252e-18 | 4.61252e-18 | True |
| capital.margin.margin_repay::20D | 1.6417e-21 | 1.0756e-20 | 1.0756e-20 | True |
| capital.stock.turnover::1D | 2.78155e-15 | 1.32123e-14 | 1.32123e-14 | True |
| capital.stock.turnover::3D | 8.04055e-23 | 5.65816e-22 | 5.65816e-22 | True |
| capital.stock.turnover::5D | 4.79475e-29 | 3.79584e-28 | 3.79584e-28 | True |
| capital.stock.turnover::10D | 2.46612e-42 | 2.23125e-41 | 2.23125e-41 | True |
| capital.stock.turnover::20D | 7.62458e-53 | 7.62458e-52 | 7.62458e-52 | True |
| stock.amplitude::1D | 1.87976e-12 | 7.28887e-12 | 7.28887e-12 | True |
| stock.amplitude::3D | 2.41434e-16 | 1.31064e-15 | 1.31064e-15 | True |
| stock.amplitude::5D | 1.20837e-16 | 6.75263e-16 | 6.75263e-16 | True |
| stock.amplitude::10D | 1.96193e-15 | 9.5581e-15 | 9.5581e-15 | True |
| stock.amplitude::20D | 1.81206e-12 | 7.17274e-12 | 7.17274e-12 | True |
| stock.gap_and_follow_through::1D | 0.0693947 | 0.103008 | 0.103008 | False |
| stock.gap_and_follow_through::3D | 0.115456 | 0.163706 | 0.163706 | False |
| stock.gap_and_follow_through::5D | 0.00987672 | 0.0163181 | 0.0163181 | True |
| stock.gap_and_follow_through::10D | 0.0156887 | 0.025697 | 0.025697 | True |
| stock.gap_and_follow_through::20D | 0.000475389 | 0.000912364 | 0.000912364 | True |
| stock.intraday_return_pct::1D | 0.000960086 | 0.001754 | 0.001754 | True |
| stock.intraday_return_pct::3D | 0.00328867 | 0.00568042 | 0.00568042 | True |
| stock.intraday_return_pct::5D | 0.00156288 | 0.00282807 | 0.00282807 | True |
| stock.intraday_return_pct::10D | 0.00959602 | 0.0159934 | 0.0159934 | True |
| stock.intraday_return_pct::20D | 0.000483385 | 0.000918432 | 0.000918432 | True |
| stock.low_to_close_recovery::1D | 9.63449e-14 | 4.2571e-13 | 4.2571e-13 | True |
| stock.low_to_close_recovery::3D | 3.48404e-13 | 1.43906e-12 | 1.43906e-12 | True |
| stock.low_to_close_recovery::5D | 1.67453e-13 | 7.23093e-13 | 7.23093e-13 | True |
| stock.low_to_close_recovery::10D | 2.15213e-12 | 8.17808e-12 | 8.17808e-12 | True |
| stock.low_to_close_recovery::20D | 4.17633e-11 | 1.46945e-10 | 1.46945e-10 | True |
| stock.open_gap_pct::1D | 2.6098e-06 | 5.97424e-06 | 5.97424e-06 | True |
| stock.open_gap_pct::3D | 3.92772e-07 | 1.02228e-06 | 1.02228e-06 | True |
| stock.open_gap_pct::5D | 1.31607e-06 | 3.04942e-06 | 3.04942e-06 | True |
| stock.open_gap_pct::10D | 1.21715e-07 | 3.35158e-07 | 3.35158e-07 | True |
| stock.open_gap_pct::20D | 9.91402e-10 | 3.2477e-09 | 3.2477e-09 | True |
| stock.turnover_change::1D | 2.57354e-05 | 5.38487e-05 | 5.38487e-05 | True |
| stock.turnover_change::3D | 0.00420421 | 0.0071964 | 0.0071964 | True |
| stock.turnover_change::5D | 0.000933319 | 0.00172166 | 0.00172166 | True |
| stock.turnover_change::10D | 0.033182 | 0.0525382 | 0.0525382 | False |
| stock.turnover_change::20D | 0.0682695 | 0.102136 | 0.102136 | False |
| stock.turnover_rate::1D | 1.09277e-10 | 3.70761e-10 | 3.70761e-10 | True |
| stock.turnover_rate::3D | 3.59657e-15 | 1.6667e-14 | 1.6667e-14 | True |
| stock.turnover_rate::5D | 6.51991e-17 | 3.75389e-16 | 3.75389e-16 | True |
| stock.turnover_rate::10D | 4.25432e-16 | 2.24533e-15 | 2.24533e-15 | True |
| stock.turnover_rate::20D | 1.26579e-15 | 6.32895e-15 | 6.32895e-15 | True |
| stock.volume_change::1D | 6.88247e-06 | 1.50307e-05 | 1.50307e-05 | True |
| stock.volume_change::3D | 0.00245153 | 0.00427332 | 0.00427332 | True |
| stock.volume_change::5D | 0.000907014 | 0.00168954 | 0.00168954 | True |
| stock.volume_change::10D | 0.0244335 | 0.0396783 | 0.0396783 | True |
| stock.volume_change::20D | 0.0785795 | 0.114847 | 0.114847 | False |
| stock.volume_ratio::1D | 3.54059e-11 | 1.26927e-10 | 1.26927e-10 | True |
| stock.volume_ratio::3D | 2.68888e-08 | 7.98262e-08 | 7.98262e-08 | True |
| stock.volume_ratio::5D | 1.54876e-11 | 5.65892e-11 | 5.65892e-11 | True |
| stock.volume_ratio::10D | 5.10414e-07 | 1.29305e-06 | 1.29305e-06 | True |
| stock.volume_ratio::20D | 3.12802e-06 | 7.07529e-06 | 7.07529e-06 | True |

## 5. Time stability

| Feature | Status | Stable horizons | Period-dependent | Failed |
|---|---|---|---|---|
| capital.dragon_tiger.institution_net_amount | STABLE | [1, 3, 5, 20] | [10] | [] |
| capital.dragon_tiger.net_amount | STABLE | [1, 3, 5, 10, 20] | [] | [] |
| capital.margin.margin_balance_change | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| capital.margin.margin_balance_change_pct | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| capital.margin.margin_buy | PERIOD_DEPENDENT | [10, 20] | [1, 3, 5] | [] |
| capital.margin.margin_net_buy | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| capital.margin.margin_repay | PERIOD_DEPENDENT | [10, 20] | [1, 3, 5] | [] |
| capital.stock.turnover | STABLE | [3, 5, 10, 20] | [1] | [] |
| stock.amplitude | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| stock.gap_and_follow_through | STABLE | [3, 5, 10, 20] | [1] | [] |
| stock.intraday_return_pct | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| stock.low_to_close_recovery | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| stock.open_gap_pct | PERIOD_DEPENDENT | [10, 20] | [1, 3, 5] | [] |
| stock.turnover_change | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| stock.turnover_rate | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| stock.volume_change | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |
| stock.volume_ratio | PERIOD_DEPENDENT | [] | [1, 3, 5, 10, 20] | [] |

### 5.1 Annual detail

| Feature | Year | IC 1/3/5/10/20 | RankIC 1/3/5/10/20 | Q5-Q1 1/3/5/10/20 |
|---|---:|---|---|---|
| capital.dragon_tiger.institution_net_amount | 2024 | 0.062399/0.045001/0.041814/0.034085/0.034750 | 0.066623/0.051207/0.052665/0.039293/0.026121 | 0.009367/0.013868/0.012401/0.007313/0.016537 |
| capital.dragon_tiger.institution_net_amount | 2025 | 0.051883/0.042952/0.034096/0.021051/0.028759 | 0.072901/0.067781/0.060690/0.044062/0.038225 | 0.012429/0.016814/0.015693/0.010080/0.022825 |
| capital.dragon_tiger.institution_net_amount | 2026 | 0.070236/0.054447/0.043782/NA/0.038259 | 0.075105/0.055405/0.045075/NA/0.035875 | 0.013676/0.018245/0.017521/0.028628/0.030592 |
| capital.dragon_tiger.net_amount | 2024 | 0.156990/0.117013/0.087964/0.066785/0.067610 | 0.219533/0.171605/0.122878/0.087837/0.078659 | 0.039299/0.056622/0.051183/0.052517/0.051513 |
| capital.dragon_tiger.net_amount | 2025 | 0.134530/0.096278/0.080948/0.060954/0.052191 | 0.182279/0.140268/0.119674/0.089128/0.070985 | 0.030906/0.041557/0.042744/0.041678/0.040524 |
| capital.dragon_tiger.net_amount | 2026 | 0.126373/0.111917/0.086108/0.071313/0.055506 | 0.183560/0.160345/0.130332/0.096685/0.079796 | 0.028619/0.042986/0.040956/0.044498/0.047971 |
| capital.margin.margin_balance_change | 2024 | -0.001974/-0.000412/-0.006576/-0.007022/-0.010731 | -0.021716/-0.014236/-0.019589/-0.018806/-0.018654 | -0.000915/-0.000733/-0.001179/-0.002385/-0.003373 |
| capital.margin.margin_balance_change | 2025 | 0.002315/-0.002317/-0.001634/-0.003475/-0.006063 | -0.012534/-0.016382/-0.017353/-0.016407/-0.016449 | -0.000074/-0.000846/-0.001190/-0.001594/-0.002610 |
| capital.margin.margin_balance_change | 2026 | 0.007527/0.010027/0.007725/0.013269/0.005806 | -0.007624/-0.004538/-0.004151/0.004196/-0.002567 | 0.000281/0.000860/0.001666/0.004374/0.001700 |
| capital.margin.margin_balance_change_pct | 2024 | -0.015085/-0.019371/-0.024550/-0.022012/-0.020871 | -0.023713/-0.014433/-0.019552/-0.017664/-0.017521 | -0.001176/-0.001039/-0.001746/-0.002081/-0.003778 |
| capital.margin.margin_balance_change_pct | 2025 | -0.003858/-0.009795/-0.013319/-0.011824/-0.014225 | -0.013711/-0.018973/-0.020344/-0.018540/-0.020749 | -0.000176/-0.001202/-0.001750/-0.002173/-0.004007 |
| capital.margin.margin_balance_change_pct | 2026 | 0.006134/0.004413/0.003570/0.008081/0.003924 | -0.005751/-0.001853/-0.000680/0.010070/0.004134 | 0.000492/0.001150/0.002002/0.004852/0.002579 |
| capital.margin.margin_buy | 2024 | -0.021263/-0.029410/-0.033586/-0.037420/-0.033814 | -0.058659/-0.068842/-0.083017/-0.107458/-0.118835 | -0.002765/-0.006179/-0.008884/-0.013214/-0.016112 |
| capital.margin.margin_buy | 2025 | -0.010332/-0.013713/-0.016604/-0.023320/-0.033448 | -0.052792/-0.062287/-0.069880/-0.086427/-0.105529 | -0.000927/-0.002476/-0.003980/-0.007709/-0.014682 |
| capital.margin.margin_buy | 2026 | 0.006432/0.005798/0.005540/0.002450/0.016298 | -0.029518/-0.030792/-0.031109/-0.027925/-0.010873 | 0.000125/0.000527/0.001692/0.006056/0.020764 |
| capital.margin.margin_net_buy | 2024 | -0.002012/-0.000538/-0.006528/-0.007103/-0.011048 | -0.022111/-0.014490/-0.019896/-0.019714/-0.019311 | -0.000917/-0.000713/-0.001199/-0.002455/-0.003465 |
| capital.margin.margin_net_buy | 2025 | 0.001858/-0.002028/-0.001286/-0.003258/-0.006185 | -0.012560/-0.016394/-0.017328/-0.016448/-0.016442 | -0.000069/-0.000833/-0.001167/-0.001595/-0.002609 |
| capital.margin.margin_net_buy | 2026 | 0.007844/0.010863/0.008594/0.013784/0.006892 | -0.007578/-0.004440/-0.004011/0.004271/-0.002596 | 0.000284/0.000881/0.001695/0.004372/0.001772 |
| capital.margin.margin_repay | 2024 | -0.020174/-0.029353/-0.032654/-0.036673/-0.032359 | -0.052683/-0.066810/-0.079574/-0.104423/-0.116540 | -0.002448/-0.006095/-0.008606/-0.012810/-0.015492 |
| capital.margin.margin_repay | 2025 | -0.011310/-0.012794/-0.015908/-0.022087/-0.031832 | -0.051548/-0.058990/-0.066777/-0.083679/-0.102825 | -0.000985/-0.002328/-0.003652/-0.007580/-0.014423 |
| capital.margin.margin_repay | 2026 | 0.004241/0.004016/0.004382/0.001913/0.016955 | -0.027004/-0.028856/-0.029297/-0.026608/-0.011197 | 0.000041/0.000444/0.001579/0.005182/0.020010 |
| capital.stock.turnover | 2024 | -0.032320/-0.047448/-0.054432/-0.060796/-0.063327 | -0.072265/-0.094009/-0.112526/-0.142444/-0.159960 | -0.003690/-0.009856/-0.015056/-0.026230/-0.048967 |
| capital.stock.turnover | 2025 | -0.017896/-0.023083/-0.027389/-0.035305/-0.046217 | -0.074270/-0.087643/-0.099189/-0.122098/-0.148975 | -0.002002/-0.005071/-0.008091/-0.015265/-0.027322 |
| capital.stock.turnover | 2026 | 0.001048/-0.000541/-0.001449/-0.004397/0.012698 | -0.043802/-0.049114/-0.053985/-0.058643/-0.054712 | -0.000439/-0.001096/-0.001206/-0.000244/0.007895 |
| stock.amplitude | 2024 | -0.033900/-0.054768/-0.060730/-0.044871/-0.020579 | -0.069691/-0.088658/-0.092160/-0.068000/-0.029657 | -0.000967/-0.002423/-0.001747/0.004706/0.021539 |
| stock.amplitude | 2025 | -0.014891/-0.024652/-0.029808/-0.032569/-0.027106 | -0.062666/-0.060162/-0.062051/-0.066736/-0.062821 | -0.000190/0.000108/0.000023/-0.000376/0.002958 |
| stock.amplitude | 2026 | -0.014659/-0.025193/-0.030711/-0.032051/-0.017269 | -0.060958/-0.072556/-0.073397/-0.075266/-0.084618 | -0.001339/-0.003733/-0.004730/-0.006340/-0.002851 |
| stock.gap_and_follow_through | 2024 | 0.017529/0.005350/-0.005812/-0.004559/-0.000334 | 0.007254/0.003837/-0.007201/-0.000783/-0.001547 | -0.037657/0.007102/0.020905/-0.003624/-0.033485 |
| stock.gap_and_follow_through | 2025 | 0.009756/0.006622/0.001408/-0.008427/-0.009407 | -0.010397/-0.005280/-0.009023/-0.013032/-0.015186 | 0.003275/-0.017010/-0.031927/-0.022364/-0.027241 |
| stock.gap_and_follow_through | 2026 | -0.002574/-0.008839/-0.011017/-0.010316/-0.007950 | -0.014991/-0.016419/-0.016875/-0.014117/-0.020355 | -0.009753/-0.017164/-0.026529/-0.021769/-0.090444 |
| stock.intraday_return_pct | 2024 | 0.022492/-0.013673/-0.020220/-0.031545/-0.027832 | 0.001362/-0.017502/-0.021712/-0.041420/-0.038194 | 0.003139/0.000500/-0.000571/-0.003703/-0.006873 |
| stock.intraday_return_pct | 2025 | 0.000124/-0.011551/-0.020696/-0.018009/-0.021451 | -0.040880/-0.032752/-0.032198/-0.022393/-0.023916 | -0.000873/-0.002016/-0.003376/-0.002526/-0.004939 |
| stock.intraday_return_pct | 2026 | 0.013248/0.010773/0.003296/0.010837/0.001989 | -0.018324/-0.007409/-0.008966/0.003875/-0.008366 | 0.000569/0.001306/0.000883/0.005469/0.001815 |
| stock.low_to_close_recovery | 2024 | -0.003514/-0.042366/-0.049680/-0.049050/-0.034699 | -0.039971/-0.062559/-0.061525/-0.062302/-0.041656 | 0.000896/-0.002102/-0.001856/0.001917/0.003714 |
| stock.low_to_close_recovery | 2025 | -0.003822/-0.018098/-0.028616/-0.030447/-0.030217 | -0.059048/-0.048483/-0.048678/-0.044346/-0.044721 | -0.000472/-0.000053/-0.000544/-0.000168/0.000225 |
| stock.low_to_close_recovery | 2026 | -0.003822/-0.011913/-0.018173/-0.014873/-0.008108 | -0.050738/-0.044295/-0.040960/-0.036240/-0.040357 | -0.001302/-0.001636/-0.001989/-0.000077/0.001207 |
| stock.open_gap_pct | 2024 | 0.030015/0.026742/0.014320/0.010631/0.003787 | 0.015365/0.021571/0.013122/0.020153/0.015411 | 0.001848/0.003008/0.002673/0.005466/0.005759 |
| stock.open_gap_pct | 2025 | 0.039801/0.028855/0.023682/0.017138/0.015214 | 0.027716/0.024923/0.028517/0.030948/0.034884 | 0.002407/0.003362/0.004658/0.006455/0.009267 |
| stock.open_gap_pct | 2026 | 0.037397/0.030830/0.017683/0.009779/0.016295 | 0.025527/0.026843/0.020306/0.018890/0.031401 | 0.002847/0.004098/0.003567/0.005124/0.010028 |
| stock.turnover_change | 2024 | -0.021825/-0.025321/-0.027225/-0.023807/-0.017204 | -0.036539/-0.021885/-0.026714/-0.022159/-0.022875 | -0.002231/-0.003279/-0.004712/-0.004508/-0.007469 |
| stock.turnover_change | 2025 | -0.011862/-0.012184/-0.013481/-0.011254/-0.010985 | -0.014636/-0.012796/-0.009847/-0.004094/-0.003783 | -0.000078/-0.000848/-0.001029/-0.000540/-0.001164 |
| stock.turnover_change | 2026 | -0.006051/-0.011453/-0.014692/-0.014452/-0.012611 | -0.011667/-0.003461/-0.008326/-0.003962/0.000128 | -0.000001/0.000140/-0.000897/0.000303/0.000776 |
| stock.turnover_rate | 2024 | -0.052285/-0.082405/-0.084407/-0.069352/-0.043269 | -0.067607/-0.092580/-0.093872/-0.066771/-0.017074 | -0.001388/-0.003133/-0.002422/0.005113/0.026793 |
| stock.turnover_rate | 2025 | -0.034276/-0.046205/-0.053796/-0.058169/-0.057160 | -0.062808/-0.064008/-0.069190/-0.073205/-0.078907 | -0.000655/-0.000884/-0.001778/-0.002701/-0.002825 |
| stock.turnover_rate | 2026 | -0.024742/-0.037547/-0.044894/-0.050337/-0.054208 | -0.059862/-0.071344/-0.077160/-0.087786/-0.121046 | -0.001549/-0.004076/-0.005621/-0.010406/-0.015333 |
| stock.volume_change | 2024 | -0.023736/-0.025599/-0.026749/-0.023078/-0.016407 | -0.036043/-0.019569/-0.023572/-0.019391/-0.020454 | -0.002303/-0.002783/-0.004102/-0.003681/-0.006457 |
| stock.volume_change | 2025 | -0.013144/-0.012501/-0.013151/-0.010660/-0.010372 | -0.014472/-0.012207/-0.009125/-0.003757/-0.003458 | -0.000163/-0.000859/-0.000995/-0.000414/-0.000874 |
| stock.volume_change | 2026 | -0.008261/-0.012983/-0.015048/-0.015365/-0.012716 | -0.013451/-0.006174/-0.009722/-0.006936/-0.000992 | -0.000222/-0.000388/-0.001304/-0.000685/0.000316 |
| stock.volume_ratio | 2024 | -0.035837/-0.041128/-0.042377/-0.034274/-0.031044 | -0.043728/-0.032677/-0.037383/-0.032028/-0.039736 | -0.002149/-0.003794/-0.005594/-0.004791/-0.010880 |
| stock.volume_ratio | 2025 | -0.018244/-0.021068/-0.024045/-0.021771/-0.018936 | -0.028487/-0.025263/-0.026203/-0.018709/-0.014411 | -0.000446/-0.001806/-0.002814/-0.003051/-0.004005 |
| stock.volume_ratio | 2026 | -0.014189/-0.015901/-0.021045/-0.015441/-0.014051 | -0.019506/-0.013325/-0.018622/-0.008623/-0.002188 | -0.000366/-0.001124/-0.002340/0.000982/0.001933 |

Annual IC/RankIC were re-aggregated from persisted daily cross-sections; annual Q5-Q1 uses the immutable S3B1 result lineage.

## 6. Forward-window stability

| Feature | Short 1/3D | Medium 5/10D | Long 20D | RankIC 1/3/5/10/20 |
|---|---|---|---|---|
| capital.dragon_tiger.institution_net_amount | EFFECTIVE_POSITIVE | EFFECTIVE_POSITIVE | EFFECTIVE_POSITIVE | 0.072283/0.060288/0.053963/0.042414/0.036206 |
| capital.dragon_tiger.net_amount | EFFECTIVE_POSITIVE | EFFECTIVE_POSITIVE | EFFECTIVE_POSITIVE | 0.190572/0.153364/0.123785/0.094368/0.076745 |
| capital.margin.margin_balance_change | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.012873/-0.012108/-0.013559/-0.010257/-0.013303 |
| capital.margin.margin_balance_change_pct | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.013238/-0.012493/-0.013829/-0.009119/-0.013192 |
| capital.margin.margin_buy | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.046536/-0.053528/-0.060170/-0.072031/-0.079373 |
| capital.margin.margin_net_buy | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.012976/-0.012141/-0.013582/-0.010465/-0.013464 |
| capital.margin.margin_repay | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.043882/-0.050938/-0.057411/-0.069684/-0.077564 |
| capital.stock.turnover | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.064032/-0.076579/-0.087449/-0.105962/-0.122399 |
| stock.amplitude | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.063602/-0.070182/-0.072075/-0.069751/-0.062345 |
| stock.gap_and_follow_through | NOT_EFFECTIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.008143/-0.006939/-0.011167/-0.010790/-0.013825 |
| stock.intraday_return_pct | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.024678/-0.021362/-0.022497/-0.017957/-0.022228 |
| stock.low_to_close_recovery | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.052336/-0.050112/-0.048910/-0.045533/-0.042718 |
| stock.open_gap_pct | EFFECTIVE_POSITIVE | EFFECTIVE_POSITIVE | EFFECTIVE_POSITIVE | 0.024398/0.024832/0.022615/0.024780/0.029604 |
| stock.turnover_change | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | NOT_EFFECTIVE | -0.018269/-0.011687/-0.012892/-0.007838/-0.006670 |
| stock.turnover_rate | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.062874/-0.072415/-0.076978/-0.076541/-0.078480 |
| stock.volume_change | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | NOT_EFFECTIVE | -0.018665/-0.011802/-0.012346/-0.008061/-0.006341 |
| stock.volume_ratio | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | EFFECTIVE_NEGATIVE | -0.028818/-0.022986/-0.026126/-0.018277/-0.016133 |

## 7. Market regime

- Status: NOT_AVAILABLE
- No frozen PIT-safe regime rule exists. Up/down/high-vol/low-vol states were not invented.

## 8. Equal-weight research baskets

Scores are equal-weight daily cross-sectional percentiles, preserve original feature direction, and use only S3B1 PROMISING components.

| Basket | Components | Horizon | Samples | Dates | IC | RankIC | Q5-Q1 | Comparison |
|---|---|---:|---:|---:|---:|---:|---:|---|
| CAPITAL_DRAGON_TIGER_BASKET | capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount | 1D | 26954 | 416 | 0.163670 | 0.172775 | 0.028157 | ABOVE_COMPONENT_MEAN |
| CAPITAL_DRAGON_TIGER_BASKET | capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount | 3D | 26868 | 416 | 0.127159 | 0.139420 | 0.038964 | ABOVE_COMPONENT_MEAN |
| CAPITAL_DRAGON_TIGER_BASKET | capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount | 5D | 26806 | 416 | 0.102840 | 0.115114 | 0.039645 | ABOVE_COMPONENT_MEAN |
| CAPITAL_DRAGON_TIGER_BASKET | capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount | 10D | 26504 | 412 | 0.080809 | 0.088285 | 0.040650 | ABOVE_COMPONENT_MEAN |
| CAPITAL_DRAGON_TIGER_BASKET | capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount | 20D | 25741 | 403 | 0.064438 | 0.072223 | 0.046184 | ABOVE_COMPONENT_MEAN |
| CAPITAL_MARGIN_BASKET | capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay | 1D | 1473898 | 416 | -0.010845 | -0.032881 | -0.000810 | ABOVE_COMPONENT_MEAN |
| CAPITAL_MARGIN_BASKET | capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay | 3D | 1473333 | 416 | -0.015431 | -0.035852 | -0.001896 | ABOVE_COMPONENT_MEAN |
| CAPITAL_MARGIN_BASKET | capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay | 5D | 1472888 | 416 | -0.019003 | -0.040344 | -0.002496 | ABOVE_COMPONENT_MEAN |
| CAPITAL_MARGIN_BASKET | capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay | 10D | 1457224 | 416 | -0.019728 | -0.042934 | -0.003292 | ABOVE_COMPONENT_MEAN |
| CAPITAL_MARGIN_BASKET | capital.margin.margin_balance_change, capital.margin.margin_balance_change_pct, capital.margin.margin_buy, capital.margin.margin_net_buy, capital.margin.margin_repay | 20D | 1423248 | 406 | -0.021314 | -0.049776 | -0.004462 | ABOVE_COMPONENT_MEAN |
| LIQUIDITY_BASKET | capital.stock.turnover, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio | 1D | 2237569 | 416 | -0.018167 | -0.051938 | -0.001203 | ABOVE_COMPONENT_MEAN |
| LIQUIDITY_BASKET | capital.stock.turnover, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio | 3D | 2236458 | 416 | -0.023971 | -0.052005 | -0.002925 | ABOVE_COMPONENT_MEAN |
| LIQUIDITY_BASKET | capital.stock.turnover, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio | 5D | 2235593 | 416 | -0.028661 | -0.057084 | -0.004463 | ABOVE_COMPONENT_MEAN |
| LIQUIDITY_BASKET | capital.stock.turnover, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio | 10D | 2212635 | 416 | -0.028471 | -0.057373 | -0.005427 | ABOVE_COMPONENT_MEAN |
| LIQUIDITY_BASKET | capital.stock.turnover, stock.turnover_change, stock.turnover_rate, stock.volume_change, stock.volume_ratio | 20D | 2162953 | 407 | -0.028668 | -0.060866 | -0.008030 | ABOVE_COMPONENT_MEAN |
| PRICE_REACTION_BASKET | stock.amplitude, stock.gap_and_follow_through, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct | 1D | 2237748 | 416 | 0.002420 | -0.048668 | 0.000218 | ABOVE_COMPONENT_MEAN |
| PRICE_REACTION_BASKET | stock.amplitude, stock.gap_and_follow_through, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct | 3D | 2236637 | 416 | -0.004342 | -0.047870 | -0.000437 | ABOVE_COMPONENT_MEAN |
| PRICE_REACTION_BASKET | stock.amplitude, stock.gap_and_follow_through, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct | 5D | 2235772 | 416 | -0.008547 | -0.049111 | -0.000921 | ABOVE_COMPONENT_MEAN |
| PRICE_REACTION_BASKET | stock.amplitude, stock.gap_and_follow_through, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct | 10D | 2212812 | 416 | -0.006791 | -0.045315 | 0.000461 | ABOVE_COMPONENT_MEAN |
| PRICE_REACTION_BASKET | stock.amplitude, stock.gap_and_follow_through, stock.intraday_return_pct, stock.low_to_close_recovery, stock.open_gap_pct | 20D | 2163129 | 407 | -0.001863 | -0.041621 | 0.002710 | ABOVE_COMPONENT_MEAN |

Unavailable: MARKET_SENTIMENT_BASKET and SPECULATION_BASKET; neither has two S3B1 PROMISING components.

## 9. Next research candidates

capital.dragon_tiger.institution_net_amount, capital.dragon_tiger.net_amount, capital.stock.turnover, stock.gap_and_follow_through

This is only an S3C research shortlist, not Alpha, ranking, weight, or deployment approval.

## 10. Performance and isolation

- contract_seconds: 0.238s
- correlation_seconds: 24.301s
- fdr_seconds: 0.003s
- stability_seconds: 0.039s
- basket_seconds: 97.082s
- total_seconds: 121.664s
- External API calls: 0
- LLM calls: 0
- formal_weight: 0
- DecisionEngine, 60/40, A/B Shadow and Canonical Shadow were not used or modified.
- No BUY/SELL/HOLD, order, TradePlan, position change, strategy, forecast, or Alpha conclusion was produced.
