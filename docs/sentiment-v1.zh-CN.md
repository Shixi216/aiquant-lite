# 情绪面 v1：影子分析与三种模式隔离

Hermes-OPC 0.10.0 的情绪面 v1 是一个可审计的研究模块，不是行情预测器，也不是交易执行模块。它复用已有的原始数据和事件聚类层：

```text
data_records
  -> event_clusters（同一事件只计一次）
  -> sentiment_event_analyses（结构化理解 + 本地评分）
  -> sentiment_symbol_snapshots / sentiment_market_snapshots
  -> SENTIMENT FactorOutput（固定 shadow_mode=true）
```

## 数据边界

当前只使用公司正式公告、交易所或监管文本、财经新闻，以及本地已有的日线行情。股吧、微博、短视频评论、群聊传闻和未知来源消息不在 v1 数据范围内。

现有历史库的真实覆盖有限：

- 90 个事件簇，其中 69 个公告、21 个财经新闻；
- 事件只关联 2 只股票；
- 现有事件簇均为单来源，15 组“疑似重复”只保留审计标记，不自动合并；
- 市场广度样本只有 11 只股票，因此结果固定标记 `PARTIAL_UNIVERSE`，不能解释为全 A 股统计。

## 模型与本地代码的分工

- LongCat：财经新闻的结构化事件提取；
- Qwen：公告和监管文本的结构化提取；
- DeepSeek：仅在重大风险、低置信度、高强度或结论冲突时升级复核；
- MiMo：v1 不作为普通文本情绪模型。

模型只能返回严格 JSON，包含事件类型、方向、强度、置信度、影响期限、事实类型、关联标的和简短摘要。模型不能返回买卖、仓位、止损、止盈或目标价。JSON 最多修复一次；密钥缺失、调用失败或校验失败时，系统使用本地确定性规则降级。

最终事件分数由本地代码计算：

```text
event_score =
  direction
  * intensity
  * model_confidence
  * source_quality_weight
  * freshness_weight
  * verification_weight
  * symbol_relevance_weight
```

单事件贡献有上限。冲突或撤回事件的方向分强制为 0。转载数量只进入传播热度，不重复增加方向分。

## 三种运行模式

### SCREENING

- 不调用 LLM；
- 不逐只抓取新闻或公告；
- 只读取已有个股快照，并可用本地行情计算市场广度；
- 数据缺失也会返回，附带 `missing_fields` 和风险标志；
- 不生成 FactorOutput 或 DecisionPacket。

### RESEARCH

- 可显式允许 LongCat 或 Qwen 调用；
- 新文本必须先进入 `data_records` 和 `event_clusters`；
- 生成并持久化个股快照与 SENTIMENT FactorOutput；
- FactorOutput 固定为影子模式，不改变正式决策。

### DECISION

- 同时检查事件时间和数据首次可用截止时间；
- 不允许使用 `data_cutoff` 之后发布或抓取的事件；
- 可把影子 SENTIMENT FactorOutput 关联到 DecisionPacket，供展示、审计和后续实验；
- 正式动作仍只由技术面 60% + 基本面 40% 以及不可绕过的硬风险规则决定。

## API

- `POST /v1/sentiment/analyze`
- `GET /v1/sentiment/symbols/{symbol}`
- `GET /v1/sentiment/market`
- `GET /v1/sentiment/events/{event_cluster_id}`
- `POST /v1/sentiment/evaluate`

每个入口都明确返回运行模式、时间截止、影子状态、缺失字段、风险标志和证据 ID。

## CLI

```powershell
uv run python -m scripts.sentiment_cli event <event_cluster_id> --data-cutoff 2026-07-29T12:00:00+08:00
uv run python -m scripts.sentiment_cli symbol 600172.SH --mode RESEARCH --data-cutoff 2026-07-29T12:00:00+08:00
uv run python -m scripts.sentiment_cli market --mode RESEARCH --data-cutoff 2026-07-29T12:00:00+08:00
uv run python -m scripts.sentiment_cli evaluate <snapshot_id> --data-cutoff 2026-08-10T18:00:00+08:00
```

历史回填默认是 dry-run；真实写入必须显式使用 `--apply`。默认不调用模型：

```powershell
uv run python -m scripts.backfill_sentiment --dry-run
uv run python -m scripts.backfill_sentiment --apply --skip-model-calls
```

模型调用只允许通过额外的 `--enable-model-calls` 显式开启，并受集中预算限制。历史未来收益只用于事后评价，不会回流到当时的情绪分数。
