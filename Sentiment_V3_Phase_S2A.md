# AIQUANT-LITE Sentiment V3 Phase S2A

> 状态：RESEARCH / SHADOW  
> 正式权重：0  
> 生产接入：无

## 1. 范围

本阶段只实现统一 Snapshot 契约、Market/Speculation 基础数据、独立 Collector、本地 DuckDB Repository、Research CLI 和测试。没有实现统一情绪分，没有进入 `stock_report`、DecisionEngine、A/B Shadow 或 Canonical Shadow。

```text
AKShare 批量源
  -> SentimentV3Collector
  -> 标准化 MarketQuote / LimitUpStock / symbol pool
  -> MarketSentimentSnapshot + SpeculationSentimentSnapshot
  -> sentiment_v3_snapshots（独立表）
  -> Research CLI / Repository 只读查询
```

## 2. 统一 Snapshot 契约

`SentimentSnapshotMeta`：

| 字段 | 语义 |
|---|---|
| snapshot_id | `sv3_{type}_{content_hash前32位}` |
| snapshot_type | MARKET、SPECULATION；枚举已为后续模块预留 |
| trade_date | 数据所属交易日 |
| symbol_scope | 当前为 `ALL_A` |
| source | 原始供应源 |
| source_timestamp | 原始数据对应/抓取完成时间；不能晚于 available_time |
| available_time | 真实世界中系统首次可以看到该版本的时间 |
| data_cutoff | 本次研究允许使用的数据截止点 |
| snapshot_time | 本系统完成 Snapshot 生成的时间 |
| finality | INTRADAY、PRELIMINARY、FINAL、CORRECTED |
| data_version | 数据源批次版本 |
| schema_version | Snapshot schema 版本 |
| algorithm_version | 模块计算算法版本 |
| content_hash | 稳定 SHA-256 |
| coverage_ratio | 可用组件覆盖率，不代表 confidence |
| quality_flags | 统一质量标志 |
| missing_fields | 缺失字段；缺失值保留 `None` |
| record_source | REAL、TEST、SYNTHETIC、REPLAY |
| revision | 修订序号，从 1 开始 |
| corrects_snapshot_id | CORRECTED 指向被修正版本 |
| created_at | 数据库对象创建时间 |
| formal_weight | 固定 Literal[0] |
| research_only | 固定 Literal[True] |

强制时间顺序：

```text
source_timestamp
<= available_time
<= data_cutoff
<= snapshot_time
<= created_at
```

四个核心时间不能互换。所有时间必须带时区。

## 3. Finality 与 EOD

- `INTRADAY`：盘中可变值，必须带 `NOT_FINAL`。
- `PRELIMINARY`：盘后初步值，必须带 `NOT_FINAL`。
- `FINAL`：需要 Collector 调用者显式 `confirmed_final=True`；当日收盘前禁止标 FINAL。
- `CORRECTED`：需要已存在的同类型、同交易日、同 scope、同 record_source 前版；revision 必须严格加 1。
- 历史 AKShare 池只能表达 EOD 最终状态，标记 `EOD_ONLY`，禁止包装为 INTRADAY。
- 昨日晋级和次日反馈只读取前一交易日已保存的 `FINAL/CORRECTED` Speculation Snapshot。

历史 as-of 查询按 `available_time <= cutoff AND data_cutoff <= cutoff` 选择当时可见版本。今天产生的 CORRECTED 不会覆盖历史 cutoff 下的旧版本。

## 4. Content hash 与去重

哈希输入：

```text
snapshot_type
source
trade_date
symbol_scope
schema_version
algorithm_version
normalized_payload
```

使用排序、无空白的 canonical JSON + SHA-256。采集时间、创建时间和 revision 不进入哈希，因此相同源内容、schema 和算法在不同时间重复采集得到相同 hash/snapshot_id。Repository 同时以唯一键阻止无意义重复版本。

算法改变必须提升 `algorithm_version`；schema 改变必须提升 `schema_version`，旧历史不覆盖。

## 5. Quality Contract

已实现：

```text
SOURCE_UNAVAILABLE
PARTIAL_DATA
STALE_DATA
PIT_UNSAFE
NOT_FINAL
EOD_ONLY
SOURCE_TIMESTAMP_INFERRED
NOT_IMPLEMENTED
MISSING_UNIVERSE
MISSING_LIMIT_UP_POOL
MISSING_LIMIT_DOWN_POOL
MISSING_BROKEN_BOARD_POOL
MISSING_STRONG_STOCK_POOL
MISSING_PREVIOUS_MARKET_SNAPSHOT
MISSING_PREVIOUS_SPECULATION_SNAPSHOT
DUPLICATED_SOURCE_DATA
SOURCE_SCHEMA_CHANGED
CALCULATION_ERROR
```

池缺失时对应 count/rate 为 `None`，不会写成 0。空池只有在接口成功返回且标准化结果确认为空时才表示 0。

## 6. Market Snapshot

已实现字段：

```text
universe_size / valid_quote_count
advancers / decliners / unchanged
advance_ratio / decline_ratio
advance_decline_ratio
breadth_denominator / breadth_strength
limit_up_count / limit_down_count / limit_up_down_ratio
broken_board_count / sealed_limit_up_count
broken_board_denominator
broken_board_denominator_definition
broken_board_rate
strong_stock_count
market_turnover / previous_market_turnover / market_turnover_change
四类 pool 的 symbol 明细
```

`breadth_strength = (advancers - decliners) / valid_change_count` 只是基础派生字段，不是 Market Score。

`broken_board_rate = broken / (sealed_limit_up + broken)`，原始分子、分母及定义字符串均持久化。

成交额变化只有前一交易日合法 Market Snapshot 存在时才计算，否则为 `None` 并标记缺失。

## 7. Speculation Snapshot

已实现字段：

```text
first_board_count / second_board_count / third_board_count
high_board_count（4板+）
max_board_height
board_ladder_distribution
board_height_by_symbol / limit_up_symbols
broken_board_count / broken_board_rate / denominator
strong_stock_count
promotion_count / denominator / rate
yesterday_limit_up_return_count
yesterday_limit_up_avg_return / median_return
yesterday_board_success_count / denominator / rate
broken_board_repair_count / denominator / rate
high_position_negative_feedback_count / denominator / rate
previous_snapshot_id
```

历史集合规则：

- 晋级率的 denominator 是昨日 FINAL Snapshot 中冻结的涨停/连板集合；今日新出现股票不会进入昨日分母。
- 昨日涨停今日表现只对昨日 `limit_up_symbols` 与今日可见 quote 的交集计算，并保存有效样本数。
- 炸板修复率以昨日冻结的 `broken_board_symbols` 为分母。
- 高位负反馈以昨日 `board_height >= 4` 为候选集合；只有今日有可见收益的股票进入有效分母。

## 8. 数据源和接口

未新增供应商。当前 Collector 使用现有 AKShare：

```text
stock_zh_a_spot_em
stock_zt_pool_em
stock_zt_pool_dtgc_em
stock_zt_pool_zbgc_em
stock_zt_pool_strong_em
```

五类数据在一次 Collector 调用中并行批量获取；不按股票请求。单源失败不会阻止其他 Snapshot 字段落地，但对应字段为 None 并添加质量标志。

涨跌停判断优先使用数据源池，不使用 `return >= 9.5%` 或统一 10% 的自行推断，避免主板、创业板、科创板、北交所、ST 规则及历史制度变化被粗糙合并。

## 9. Storage / Repository

独立表：`sentiment_v3_snapshots`。保存 PIT 查询列和完整不可变 `payload_json`。

接口：

```text
save_snapshot
get_snapshot
get_latest_snapshot
get_snapshot_as_of
previous_final_snapshot
list_snapshots
```

默认 `record_source=REAL`，TEST/SYNTHETIC/REPLAY 必须显式请求，不会混入正式研究统计。

该表不属于 B Shadow、Canonical、DecisionPacket、持仓或订单边界。

## 10. Collector 与 CLI

Collector 是可调度的单次任务，不是 daemon，也不由用户报告请求触发。

```text
python -m scripts.sentiment_v3_snapshot collect --trade-date YYYY-MM-DD
python -m scripts.sentiment_v3_snapshot show-latest MARKET
python -m scripts.sentiment_v3_snapshot show-as-of SPECULATION --cutoff ISO-8601
python -m scripts.sentiment_v3_snapshot validate
```

`stock_report` 没有导入 V3 包，也没有调用 V3 Collector/Repository。`--no-fetch --no-ai` 路径仍为 0 个 V3 外部请求、0 个 V3 模型调用；本阶段没有把本地 Snapshot 读取接入正式报告，因此本轮对其运行时开销是 0。

## 11. GitHub 参考登记

| source_repo | source_file | borrowed_concept | our_difference | license |
|---|---|---|---|---|
| akfamily/akshare | `akshare/stock_feature/stock_ztb_em.py` | 涨停、跌停、炸板、强势池批量接口 | 独立 PIT/finality/version/hash Snapshot；不接受池即历史真值 | MIT |
| aifinlab/FinClaw | `skills/a-share-board-analysis/SKILL.md` | 炸板率、连板梯队、最高板、昨日集合反馈 | 不复制阈值、交易判断或情绪总分；保存分子/分母 | Apache-2.0 |
| joinylee/stock-sentiment-cn | `scripts/full_report_v3.py` | 全市场共享 Snapshot、独立源并发 | 后台 Collector 与 report 完全解耦，不复制浏览器抓取/凭据/投资逻辑 | README 声明 MIT，根 LICENSE 待复核 |

没有复制上游代码。

## 12. 性能测量

Collector 输出以下实测字段：

```text
external_request_seconds
request_seconds_by_source
normalization_seconds
snapshot_calculation_seconds
db_write_seconds
total_seconds
request_count
failed_sources
```

CLI 的 `show-latest/show-as-of` 输出 `db_read_ms`。

2026-08-13 隔离临时库 dry-run 实测（标记 INTRADAY，不写生产库）：

| 阶段 | 耗时 |
|---|---:|
| 5 个批量源并行外部请求 | 5.257 s |
| 标准化 | 3.413 ms |
| 两类 Snapshot 计算 | 1.268 ms |
| Collector 总耗时 | 5.342 s |

其中涨停、跌停、炸板、强势池成功；`stock_zh_a_spot_em` 因运行环境代理连接失败，被隔离为 `SOURCE_UNAVAILABLE + MISSING_UNIVERSE`。系统没有把上涨/下跌/成交额造为 0，Market coverage 为 0.8。该结果证明失败隔离和 missing 语义有效，但不是“完整成功采集”的 SLA 样本。

临时 DuckDB 微基准：保存 Market+Speculation 两条约 207.3 ms；50 次独立 as-of 读取平均 33.0 ms（30.2–40.9 ms）。`stock_report` 尚未接入 V3 读取，因此本轮对其实际开销为 0。

测试结果：新增专项 25 passed；指定分层回归 249 passed；全量 `1368 passed, 0 failed, 0 skipped`，另有 1 条第三方 Starlette/httpx 弃用警告。新增/修改文件 Ruff 0 error。

## 13. 已知限制

1. AKShare/Eastmoney 无 SLA，字段和反爬可能变化；已用 `SOURCE_SCHEMA_CHANGED` 和失败隔离处理，但仍需日常监控。
2. AKShare 跌停/炸板等历史池窗口有限；没有从过去某个盘中时刻重建池的能力。
3. 历史池只能作为 EOD-only；S2A 不做盘中历史回填。
4. 强势股池是供应商定义，当前只记录 count/symbol，不能当作已验证 Alpha。
5. 当 decliners 或 limit_down_count 为 0 时，相应 ratio 为 None，避免用无穷值污染数据。
6. 市场成交额变化依赖前一交易日 Snapshot；S2A 不从非 V3 表静默补数。
7. promotion/repair/negative-feedback 的研究价值尚未验证，属于 S3 内容。
8. 没有 Event、Sector、Capital、Stock、Attention、News 模块实现；枚举只预留边界。
9. 没有任何统一情绪总分、权重、confidence、action、VETO 或仓位字段。
