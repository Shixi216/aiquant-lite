# Sentiment V3 Evaluation Framework V1

版本：`EVALUATION_FRAMEWORK_V1`。本阶段只验证数学，不评估 39 个真实 Feature。

## 1. Dataset

`ResearchEvaluationDataset` 通过以下稳定键连接：

```text
packet_id + symbol + trade_date + cutoff + record_source
```

仅 `symbol + calendar date` 不足以 Join。Dataset 保存：

- original row count；
- valid label count；
- excluded label count；
- exclusion reasons；
- packet IDs、label IDs、LabelSpec hashes；
- Feature Registry version、Label schema/algorithm version；
- deterministic dataset ID/content hash。

Invalid Label 保留为行，不选择性删除。`REAL/TEST/SYNTHETIC/REPLAY` 不得混合。

## 2. Evaluation Config

- 最小横截面：20，只是 `EVALUATION_CONFIG`；
- IC：同一 `trade_date + cutoff` 的横截面 Pearson；
- RankIC：同一 `trade_date + cutoff` 的横截面 Spearman；
- 禁止把所有日期和股票堆起来算一个总 IC；
- 日 IC 序列形成后才计算 mean/median/std；
- Q5、Q10；
- Top/Bottom 10、20；
- mean、median、win rate；
- MAE/MFE mean、median；
- tail exclusion：去除 return 最大的 1%/5%/10% 后重算；
- Profit Factor：`sum(positive) / abs(sum(negative))`，分母为 0 返回 None；
- Horizon > 1 标记 `overlapping_labels = true`。

## 3. Drawdown 与 Portfolio

单日横截面不是净值曲线，V1 不对其计算 max drawdown。

Portfolio evaluation、按日 Top20 组合、成本后净值均留到后续阶段。本阶段没有形成
Portfolio，也没有输出 BUY/SELL/HOLD。

## 4. 预留接口

- `RegimeProvider`：S3C 再定义 BULL/BEAR/SIDEWAYS/HIGH_VOL/LOW_VOL；
- `NullTestProvider`：未来 permutation/random baseline；
- 市值分层和行业中性化未启用；当前行业 membership 对历史仍不安全。

## 5. Result Contract

`FeatureEvaluationResult` 预留：

- feature/label/date range；
- original/valid samples、coverage；
- daily IC/RankIC；
- IC/RankIC mean/median/std；
- quantile、TopN、MAE/MFE、tail stats；
- quality flags；
- `alpha_conclusion = NONE`；
- `formal_weight = 0`。

S3A 只使用 TEST/SYNTHETIC 验证数学。没有运行 HISTORY_SAFE_V1 的 39 个 Feature。

