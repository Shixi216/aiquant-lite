# Sentiment V3 S3C Shadow Report

## 1. Dataset and isolation

- Run ID: `sv3_s3c_99c3f1bc9a5b7ca408455b45bc7abe45`
- Dataset: `sv3_s3b_replay_222e9321c2719ecc90bfe149cbf394a1`
- Range: 2024-08-01 to 2026-08-13; dates=416; packets=2238764
- Record source: `REPLAY`; external API=0; LLM=0; formal_weight=0.
- DecisionEngine, 60/40, A/B Shadow, Canonical Shadow and FORWARD_SHADOW_V1 are not connected.

## 2. Candidate features

- `capital.dragon_tiger.institution_net_amount` (direction=+1)
- `capital.dragon_tiger.net_amount` (direction=+1)
- `capital.stock.turnover` (direction=-1)
- `stock.gap_and_follow_through` (direction=-1)

Directions are frozen from S3B2 and are not optimized in S3C.

## 3. Composite definitions

- `EQUAL_WEIGHT`: equal mean of daily cross-sectional oriented z-scores; at least 2 features.
- `RANK_AVERAGE`: equal mean of daily oriented percentile ranks; at least 2 features.
- `FEATURE_FAMILY_AVERAGE`: equal average of Dragon-Tiger and Trading-Behavior family rank means; both families required.
- Confidence is data completeness (`observed candidates / 4`), not prediction probability or production confidence.

## 4. Coverage

| Method | Coverage |
|---|---:|
| EQUAL_WEIGHT | 100.000000% |
| RANK_AVERAGE | 100.000000% |
| FEATURE_FAMILY_AVERAGE | 1.208792% |

Mean research confidence: 0.506044.

## 5. Future-return research diagnostics

These are replay diagnostics, not an Alpha conclusion or tradable strategy.

| Method | Label | Horizon | Samples | RankIC | Q5-Q1 |
|---|---|---:|---:|---:|---:|
| EQUAL_WEIGHT | STATISTICAL | 1 | 2237748 | 0.041662 | 0.000652 |
| EQUAL_WEIGHT | STATISTICAL | 3 | 2235703 | 0.048778 | 0.002269 |
| EQUAL_WEIGHT | STATISTICAL | 5 | 2233725 | 0.057760 | 0.003965 |
| EQUAL_WEIGHT | STATISTICAL | 10 | 2207320 | 0.068500 | 0.007143 |
| EQUAL_WEIGHT | STATISTICAL | 20 | 2149075 | 0.080336 | 0.011945 |
| EQUAL_WEIGHT | EXECUTABLE | 1 | 2233249 | 0.026503 | -0.000272 |
| EQUAL_WEIGHT | EXECUTABLE | 3 | 2232318 | 0.034618 | 0.000651 |
| EQUAL_WEIGHT | EXECUTABLE | 5 | 2230426 | 0.047431 | 0.002373 |
| EQUAL_WEIGHT | EXECUTABLE | 10 | 2203978 | 0.061154 | 0.005507 |
| EQUAL_WEIGHT | EXECUTABLE | 20 | 2145860 | 0.074854 | 0.009759 |
| RANK_AVERAGE | STATISTICAL | 1 | 2237748 | 0.040531 | 0.000818 |
| RANK_AVERAGE | STATISTICAL | 3 | 2235703 | 0.046502 | 0.002430 |
| RANK_AVERAGE | STATISTICAL | 5 | 2233725 | 0.055535 | 0.004250 |
| RANK_AVERAGE | STATISTICAL | 10 | 2207320 | 0.065465 | 0.007719 |
| RANK_AVERAGE | STATISTICAL | 20 | 2149075 | 0.077328 | 0.014151 |
| RANK_AVERAGE | EXECUTABLE | 1 | 2233249 | 0.024803 | -0.000031 |
| RANK_AVERAGE | EXECUTABLE | 3 | 2232318 | 0.032276 | 0.000884 |
| RANK_AVERAGE | EXECUTABLE | 5 | 2230426 | 0.045078 | 0.002723 |
| RANK_AVERAGE | EXECUTABLE | 10 | 2203978 | 0.058378 | 0.006199 |
| RANK_AVERAGE | EXECUTABLE | 20 | 2145860 | 0.071839 | 0.012074 |
| FEATURE_FAMILY_AVERAGE | STATISTICAL | 1 | 26955 | 0.107473 | 0.023360 |
| FEATURE_FAMILY_AVERAGE | STATISTICAL | 3 | 26821 | 0.075902 | 0.031014 |
| FEATURE_FAMILY_AVERAGE | STATISTICAL | 5 | 26693 | 0.059825 | 0.031146 |
| FEATURE_FAMILY_AVERAGE | STATISTICAL | 10 | 26243 | 0.048453 | 0.032039 |
| FEATURE_FAMILY_AVERAGE | STATISTICAL | 20 | 25290 | 0.040600 | 0.028668 |
| FEATURE_FAMILY_AVERAGE | EXECUTABLE | 1 | 25903 | 0.010235 | 0.001203 |
| FEATURE_FAMILY_AVERAGE | EXECUTABLE | 3 | 25931 | 0.001679 | 0.000869 |
| FEATURE_FAMILY_AVERAGE | EXECUTABLE | 5 | 25884 | -0.003214 | -0.000161 |
| FEATURE_FAMILY_AVERAGE | EXECUTABLE | 10 | 25528 | -0.006375 | -0.000165 |
| FEATURE_FAMILY_AVERAGE | EXECUTABLE | 20 | 24618 | -0.006358 | -0.002122 |

## 6. Annual stability (Executable)

| Year | Method | Horizon | RankIC | Q5-Q1 |
|---|---|---:|---:|---:|
| 2024 | EQUAL_WEIGHT | 1 | 0.020985 | -0.001093 |
| 2025 | EQUAL_WEIGHT | 1 | 0.031694 | -0.000266 |
| 2026 | EQUAL_WEIGHT | 1 | 0.022610 | 0.000258 |
| 2024 | EQUAL_WEIGHT | 3 | 0.038767 | 0.001635 |
| 2025 | EQUAL_WEIGHT | 3 | 0.038171 | 0.000381 |
| 2026 | EQUAL_WEIGHT | 3 | 0.026749 | 0.000396 |
| 2024 | EQUAL_WEIGHT | 5 | 0.060627 | 0.005078 |
| 2025 | EQUAL_WEIGHT | 5 | 0.052583 | 0.002280 |
| 2026 | EQUAL_WEIGHT | 5 | 0.031307 | 0.000733 |
| 2024 | EQUAL_WEIGHT | 10 | 0.071418 | 0.008140 |
| 2025 | EQUAL_WEIGHT | 10 | 0.072841 | 0.007491 |
| 2026 | EQUAL_WEIGHT | 10 | 0.036764 | 0.000764 |
| 2024 | EQUAL_WEIGHT | 20 | 0.084364 | 0.016740 |
| 2025 | EQUAL_WEIGHT | 20 | 0.093796 | 0.014728 |
| 2026 | EQUAL_WEIGHT | 20 | 0.037570 | -0.003285 |
| 2024 | FEATURE_FAMILY_AVERAGE | 1 | 0.023830 | 0.005700 |
| 2025 | FEATURE_FAMILY_AVERAGE | 1 | 0.001340 | -0.001555 |
| 2026 | FEATURE_FAMILY_AVERAGE | 1 | 0.014184 | 0.002243 |
| 2024 | FEATURE_FAMILY_AVERAGE | 3 | 0.005698 | 0.005762 |
| 2025 | FEATURE_FAMILY_AVERAGE | 3 | -0.008456 | -0.001255 |
| 2026 | FEATURE_FAMILY_AVERAGE | 3 | 0.013712 | 0.000732 |
| 2024 | FEATURE_FAMILY_AVERAGE | 5 | 0.008426 | 0.009769 |
| 2025 | FEATURE_FAMILY_AVERAGE | 5 | -0.014662 | -0.004976 |
| 2026 | FEATURE_FAMILY_AVERAGE | 5 | 0.005716 | 0.000290 |
| 2024 | FEATURE_FAMILY_AVERAGE | 10 | -0.007941 | 0.005205 |
| 2025 | FEATURE_FAMILY_AVERAGE | 10 | -0.017682 | -0.005613 |
| 2026 | FEATURE_FAMILY_AVERAGE | 10 | 0.011561 | 0.004328 |
| 2024 | FEATURE_FAMILY_AVERAGE | 20 | -0.009050 | 0.002092 |
| 2025 | FEATURE_FAMILY_AVERAGE | 20 | -0.011407 | -0.004656 |
| 2026 | FEATURE_FAMILY_AVERAGE | 20 | 0.003694 | -0.001122 |
| 2024 | RANK_AVERAGE | 1 | 0.020161 | -0.000503 |
| 2025 | RANK_AVERAGE | 1 | 0.029188 | -0.000036 |
| 2026 | RANK_AVERAGE | 1 | 0.021502 | 0.000288 |
| 2024 | RANK_AVERAGE | 3 | 0.036805 | 0.001976 |
| 2025 | RANK_AVERAGE | 3 | 0.034494 | 0.000579 |
| 2026 | RANK_AVERAGE | 3 | 0.026090 | 0.000610 |
| 2024 | RANK_AVERAGE | 5 | 0.059336 | 0.005855 |
| 2025 | RANK_AVERAGE | 5 | 0.048188 | 0.002495 |
| 2026 | RANK_AVERAGE | 5 | 0.031212 | 0.000996 |
| 2024 | RANK_AVERAGE | 10 | 0.073857 | 0.010383 |
| 2025 | RANK_AVERAGE | 10 | 0.067042 | 0.007850 |
| 2026 | RANK_AVERAGE | 10 | 0.034971 | 0.000902 |
| 2024 | RANK_AVERAGE | 20 | 0.087994 | 0.023805 |
| 2025 | RANK_AVERAGE | 20 | 0.087208 | 0.015483 |
| 2026 | RANK_AVERAGE | 20 | 0.035449 | -0.001922 |

Monthly metrics are persisted in `shadow_time_metrics`; no favorable subperiod is selected.

## 7. Risk reminder audit

Risk rule `S3C_RISK_REMINDER_MEDIAN_V1` requires public Dragon-Tiger outflow, above-median turnover and below-median gap follow-through.
Total reminder rows: 6665.

| Horizon | Reminders | Valid labels | Reminder mean | Others mean | Difference |
|---:|---:|---:|---:|---:|---:|
| 1 | 6665 | 6488 | -0.001200 | 0.002266 | -0.003466 |
| 3 | 6665 | 6498 | -0.007696 | 0.003350 | -0.011046 |
| 5 | 6665 | 6508 | -0.015586 | 0.005929 | -0.021514 |
| 10 | 6665 | 6436 | -0.019501 | 0.011610 | -0.031112 |
| 20 | 6665 | 6203 | -0.016769 | 0.026725 | -0.043494 |

## 8. Legacy Decision augmentation

- Status: `NOT_AVAILABLE_NO_PIT_SAFE_LEGACY_DECISION`.
- The main database has no PIT-safe historical Legacy Decision/confidence/VETO dataset covering this Replay range.
- Therefore action difference, formal confidence change and pre-VETO hit rate are `NOT_AVAILABLE`; they are not reconstructed after the fact.
- The sidecar API guarantees `shadow_action == legacy_action` whenever a valid Legacy reference is supplied.

## 9. Market stage

`NOT_AVAILABLE`: S3B2 found no frozen PIT-safe market-regime definition. S3C does not invent one.

## 10. Conclusion boundary

This run establishes a deterministic research composite and旁路 shadow dataset only. It does not prove Alpha, create BUY/SELL/HOLD, change confidence, or authorize production use.
