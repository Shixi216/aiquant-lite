# Sentiment V3 Label Spec V1

状态：`RESEARCH / SHADOW`；`formal_weight = 0`。

## 1. 固定版本

- Schema：`sentiment-v3-forward-label-1.0.0`
- Label algorithm：`sentiment-v3-forward-label-s3a-1.0.0`
- Return：`RETURN_ALGO_V1`
- MAE/MFE：`MAE_MFE_ALGO_V1`
- 复权：`ADJ_FACTOR_RETURN_V1`
- 可成交规则：`A_SHARE_DAILY_TRADABILITY_V1`
- Horizon：`1/3/5/10/20 TRADING_DAYS`

## 2. 两类 Label

### Statistical

`CLOSE_TO_CLOSE_{1,3,5,10,20}D`

- T 日 EOD Feature 使用 T close 作为统计参考价；
- Exit 为第 N 个未来交易日 close；
- `execution_realistic = false`；
- `tradability_status = RESEARCH_RETURN_ONLY`；
- 只用于 IC、RankIC、分位数组等统计研究，不能解释为可实现成交。

### Executable research

`NEXT_OPEN_TO_CLOSE_{1,3,5,10,20}D`

- Entry 为 T 后首个交易日 open；
- A 股 T+1：1D 使用 T+1 open 到 T+2 close；
- 3/5/10/20D 使用 T+1 open 到第 3/5/10/20 个未来交易日 close；
- 停牌、锁死涨停 Entry、锁死跌停 Exit、退市和无报价不会被假设成交；
- 不自动滚动 Entry/Exit，不改变 Horizon。

## 3. 时间与价格

- Feature cutoff 来自 `ResearchFeaturePacket.cutoff`；
- Executable Entry 必须严格晚于 cutoff；
- 交易日由本地 `trading_calendar_days` 推进；
- 价格只读本地 `canonical_historical_bars`；
- 复权因子只读本地 `historical_adjustment_factors`；
- 两表都按 `data_available_time <= build_as_of`、`data_cutoff <= build_as_of` 过滤；
- Label Builder 不调用外部 API，不调用 LLM。

`ADJ_FACTOR_RETURN_V1`：

```text
adjusted_price = raw_price * adj_factor
adjusted_return = adjusted_exit / adjusted_entry - 1
raw_return = raw_exit / raw_entry - 1
```

Entry/Exit 必须同一口径。缺复权因子时 `adjusted_return = None`，并标记
`ADJUSTMENT_UNAVAILABLE`，不得退化为 raw 长周期收益。

## 4. 可成交规则

- `SUSPENDED`：状态表为停牌，或可成交 Label 的成交日 volume=0；
- `LIMIT_UP_LOCKED`：日线 OHLC 一价且处于当日涨停价；
- `LIMIT_DOWN_LOCKED`：日线 OHLC 一价且处于当日跌停价；
- 涨跌停打开：保留 Label，但标记 `TRADABILITY_INFERRED`；
- `NO_VALID_QUOTE`：无 Bar 或必要价格缺失；
- `DELISTED`：历史证券状态表明确退市；
- ST/*ST 采用 5%；北交所 30%；科创板 20%；创业板按 2020-08-24 制度变更；其余 10%。

日线无法证明开盘瞬间成交队列，因此打开涨跌停只能是保守推断，不伪装分钟级精度。

## 5. MAE/MFE

基于合法 Entry 的 adjusted price，在完整持有窗口内：

```text
MAE = min(adjusted_low / adjusted_entry - 1)
MFE = max(adjusted_high / adjusted_entry - 1)
```

窗口缺 Bar 时保留已有收益事实，Label 为 `PARTIAL`，并记录
`MISSING_HOLDING_BAR`；不使用前值填充。

## 6. 成本、滑点和缺失

- V1 不配置成本模型；
- `net_return = None`；
- `COST_MODEL_NOT_CONFIGURED`；
- 不拍 5bp/10bp 滑点；
- missing 永不填 0；
- Invalid Label 仍保存在 Dataset 中，并记录 exclusion reason。

## 7. 确定性

```text
label_id = hash(packet_id + LabelSpec hash + price_data_version + algorithm_version)
content_hash = hash(价格、收益、状态、quality、版本)
```

相同 Packet、LabelSpec、价格版本和算法版本得到相同 ID/hash。价格修正产生新
`price_data_version` 和新 Label；旧 Label 不覆盖。

