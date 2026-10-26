# Sentiment V3 Phase S3A

## 1. 范围与冻结

- 分支：`research/sentiment-v3`；
- Feature Registry V1 未修改：124 canonical / 39 HISTORY_SAFE_V1；
- S3A 只建立 Label 与 Evaluation 尺子；
- `formal_weight = 0`；
- Alpha conclusion：`NONE`；
- 不接 stock_report、DecisionEngine、A/B Shadow、Canonical Shadow；
- 不产生 TradePlan、订单、成交或持仓变化。

## 2. 旧实现审计

审计发现 7 条独立收益/成交计算路径：

1. `trading/experiments/forward_returns.py`：RAW next-open → horizon-close；
2. `trading/research/capital_flow/evaluation.py`：close → future close；
3. `trading/experiments/parameter_sensitivity.py`：RAW next-open 策略收益；
4. `trading/experiments/formal_validation_metrics.py`：adj_factor + A股约束；
5. `trading/experiments/formal_timeline_replay.py`：adj_factor close-to-close；
6. `trading/experiments/timeline_quality_validation.py`：adj_factor + 可成交约束；
7. `trading/simulation/backtest/engine.py`：通用 next-open 策略模拟。

另有 `scripts/sentiment_v2_backtest.py`，它把当前时点计算的 V2 情绪用于所谓历史比较，
具有历史时序风险；S3A 不复用该路径。

主要重复/缺口：RAW 与复权口径并存、1/3/5/20 horizon 不一致、停牌被压缩后可能
改变实际 horizon、涨跌停/退市语义不统一、成本假设分散。没有发现可直接满足 S3A
全部契约的共享 Label。

S3A 安全复用：

- `canonical_historical_bars`；
- `historical_adjustment_factors`；
- `historical_security_statuses`；
- `trading_calendar_days`；
- 正式历史验证采用的 `raw_price * adj_factor` 收益连续性公式；
- 既有一价板和涨跌停判断原语。

旧 RAW `ForwardReturnLabelService` 保持不变，避免改变现有实验结果。

## 3. S3A 架构

```text
ResearchFeaturePacket
        +
LabelSpec V1
        +
local calendar / bars / adj_factor / security status
        ↓
ForwardLabelBuilder (one batch load)
        ↓
ForwardReturnLabel + coverage/survivorship report
        ↓
ResearchEvaluationDataset
        ↓
ResearchEvaluator (TEST/SYNTHETIC math validation only)
```

Label 存储通过 `SentimentV3EvaluationRepository` 扩展现有 Sentiment V3 Repository，
使用同一 DuckDB，不另建数据库系统。

## 4. PIT 与 Bias

- Executable Entry 严格晚于 Feature cutoff；
- Statistical T-close 只是统计参考，不声称可成交；
- calendar/price/factor/status 只从本地批量读取；
- 价格版本按 `build_as_of` 过滤；
- Entry/Exit 不自动滚到后续可成交日；
- Invalid Label 不静默删除；
- coverage 按 horizon 报告；
- suspension/delisting/new listing/insufficient window 分别记录；
- 5/10/20 日 Label 标记 overlap；
- 不使用当前行业 membership 做历史分层。

## 5. 本地真实 dry run

数据：本地 DuckDB，信号日 2026-07-01，`REPLAY` Packet，仅检查 Label；0 外部 API，
0 LLM，未评估 Feature。

| 场景 | Packet | Label | 有效率 | 批量读取 | 纯计算 | 总耗时 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 小样本 | 10 | 100 | 100% | 579.55ms | 12.58ms | 592.34ms |
| 扩展样本 | 100 | 1,000 | 100% | 420.58ms | 103.38ms | 525.10ms |
| 全市场单日 | 5,513 | 55,130 | 9.00% | 3,878.39ms | 4,675.96ms | 8,639.73ms |

临时 DuckDB：1,000 Label 批量保存 237.92ms，读取 103.83ms。

全市场有效率低不是算法填 0，而是当前本地 `adj_factor` 仅覆盖 497 只股票；RAW 日线
覆盖 5,593 只。缺 factor 的 Label 明确为 `ADJUSTMENT_UNAVAILABLE`。该数据缺口必须
在 S3B 大规模历史研究前补齐，不能退化为未复权收益。

批量调用固定 3 次查询：calendar、bar+factor、security status；无 symbol×horizon N+1。

## 6. Evaluation

实现能力：coverage、missing rate、每日横截面 Pearson IC、Spearman RankIC、Q5/Q10、
Top/Bottom10/20、win rate、mean、median、MAE、MFE、Profit Factor、tail exclusion。

未启用：真实 Feature 评估、Portfolio、max drawdown、Regime、市值分层、行业中性化、
permutation、显著性结论、权重优化。

## 7. CLI

```text
python -m scripts.sentiment_v3_evaluation validate-labels
python -m scripts.sentiment_v3_evaluation evaluation-config
python -m scripts.sentiment_v3_evaluation build-labels ...
python -m scripts.sentiment_v3_evaluation label-stats ...
python -m scripts.sentiment_v3_evaluation validate-evaluator
```

CLI 默认本地 Research 行为，没有 best-factor/weight optimizer 命令。

## 8. 测试与限制

- S3A 新增专项测试：34；
- 专项结果：34 passed；
- Ruff：新增文件 0 error；
- 全量测试结果见最终验收记录。

已知限制：

1. adj_factor 全市场覆盖不足；
2. 日线只能保守推断涨跌停打开后的成交性；
3. 退市末端价格缺失时只标 Invalid，不虚构清算价；
4. V1 不配置成本/滑点；
5. 没有历史行业 membership，因此不做历史行业分组；
6. 尚未开始 S3B，不能形成 Alpha 结论。

