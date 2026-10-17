# Sentiment V3 Feature Registry V1

冻结版本：`Sentiment_V3_Feature_Registry_V1`

机器可读定义位于：

```text
trading/research/sentiment/v3/feature_registry.py
```

S3 第一轮研究必须使用该冻结定义。字段、算法或 PIT 语义变化时必须升级 Registry 版本，不能静默覆盖 V1。

## Registry 契约

每个 Feature 记录：

- `feature_name`
- `module`
- `data_type`
- `description`
- `source_snapshot_type`
- `source_field`
- `methodology_class`
- `unit`
- `algorithm_version`
- `pit_requirement`
- `finality_requirement`
- `availability_requirement`
- `historical_support`
- `candidate_role`
- `canonical_feature`
- `formal_weight=0`

默认 `candidate_role=UNKNOWN`。Registry 不声明 Alpha、风险效果或生产用途。

## 数量

| 项目 | 数量 |
| --- | ---: |
| 注册定义 | 132 |
| Canonical Feature | 124 |
| 显式 alias | 8 |
| Market | 16 |
| Speculation | 15 |
| Sector | 16 |
| Stock | 20 |
| Capital | 33 |
| News Sentiment | 9 |
| Attention Proxy | 3 |
| Event | 8 |
| Reaction | 12 |
| HISTORY_SAFE_V1 | 39 |
| FORWARD_SHADOW_V1 | 124 |

状态计数允许重叠：

| Research Status | 数量 |
| --- | ---: |
| READY_FOR_HISTORY | 43 |
| FORWARD_ONLY | 89 |
| PIT_UNSAFE_FOR_HISTORY | 26 |
| CURRENT_MEMBERSHIP_ONLY | 26 |
| EOD_ONLY | 112 |
| INSUFFICIENT_HISTORY | 20 |
| VENDOR_DERIVED | 16 |
| NOT_IMPLEMENTED | 0 |

## 冻结研究集合

`HISTORY_SAFE_V1` 只包含 canonical Feature，并要求：

- 存在 `READY_FOR_HISTORY`；
- 不含 `FORWARD_ONLY`；
- 不含 `PIT_UNSAFE_FOR_HISTORY`；
- 不含 `CURRENT_MEMBERSHIP_ONLY`；
- 不含 `NOT_IMPLEMENTED`。

`FORWARD_SHADOW_V1` 包含全部 124 个已实现 canonical Feature，允许 Current Membership、Vendor Derived 和只能未来积累的文本/Event/Reaction 字段，但仍要求实际 Snapshot `available_time <= cutoff`。

## 冗余审计

以下 8 个名称只作为来源 alias，不作为第二个独立因子进入 Dataset：

| Alias | Canonical Feature |
| --- | --- |
| `capital.market.market_turnover` | `market.market_turnover` |
| `capital.market.market_turnover_change` | `market.market_turnover_change` |
| `capital.sector.turnover_change` | `sector.turnover_change` |
| `capital.stock.turnover_change` | `stock.turnover_change` |
| `capital.stock.turnover_rate` | `stock.turnover_rate` |
| `speculation.broken_board_count` | `market.broken_board_count` |
| `speculation.broken_board_rate` | `market.broken_board_rate` |
| `speculation.strong_stock_count` | `market.strong_stock_count` |

Packet 和 Dataset 默认只装配 canonical Feature。Alias 只用于 lineage、兼容和来源解释。

## 重要边界

- Feature 可计算不等于可用于历史研究。
- 可用于历史研究不等于具有预测价值。
- Vendor Derived 不等于机构真实资金。
- News Attention 不等于 News Sentiment。
- Event 方向不等于 Reaction 方向。
- 所有正式权重保持 0。
- Registry 不含 future return、label、action、VETO 或持仓字段。
