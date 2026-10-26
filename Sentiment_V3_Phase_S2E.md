# Sentiment V3 Phase S2E

## 1. 范围

S2E 只完成 PIT-safe Research Feature Assembly、Dataset 定义和 Forward Research Shadow。未增加新情绪指标，未接入正式策略，未生成总分、未来收益 Label、IC、RankIC 或权重。

## 2. 可拼装性审计

S2A-S2D 的所有 Snapshot 已具备统一 `SentimentSnapshotMeta`：snapshot ID、trade date、source/source timestamp、available time、data cutoff、snapshot time、finality、record source、schema/algorithm version、content hash、coverage、quality flags、revision/correction 和 `formal_weight=0`。

Sector、Stock、Capital、Margin、Dragon Tiger 另有 universe linkage；Sector/Stock/Capital 可通过 industry sector ID 关联；News、Event Intelligence、Event Reaction 继续使用同一 PIT/finality 规则。

S2E 发现并最小修复一处必要缺口：S2D Event 原为普通字符串 `record_source=RESEARCH`，无法隔离 REAL/TEST/REPLAY。现改为复用 `RecordSource`，事件保存、revision 查询和批量 as-of 均按来源隔离。未修改事件语义或正式链。

## 3. ResearchFeaturePacket

Packet 保存：

- identity：packet ID/type、symbol、trade date、decision time、cutoff、record source；
- universe：universe ID/version；
- Snapshot links：Market、Speculation、Sector、Stock、三层 Capital、Margin、Dragon Tiger、News；
- Event/Reaction：全部合法 event IDs 和 reaction IDs；
- version：Feature Registry 与 Assembly algorithm version；
- PIT/quality：PIT status、quality status、missing modules、quality flags；
- Feature：模块化 `features`、稳定 feature names、字段级 quality、missing reason、lineage、coverage；
- deterministic content hash；
- `formal_weight=0`、`research_only=true`。

Feature 保持命名空间：Market、Speculation、Sector、Stock、Capital Market/Sector/Stock/Margin/Dragon Tiger、News、Attention、Event、Reaction。Dataset Row 可扁平化，但 Packet 不丢失模块结构。

## 4. PIT Assembly

`ResearchFeatureAssembler` 每个 cutoff 通过一次批量 Snapshot 查询、一次 Event 查询和一次 Reaction 查询加载数据，再内存 join。Snapshot 查询同时要求：

```text
record_source匹配
available_time <= cutoff
data_cutoff <= cutoff
```

EOD Packet 只接受 FINAL/CORRECTED；Forward Intraday Packet 只接受真实 INTRADAY/PRELIMINARY。盘中 cutoff 无法读取盘后 FINAL。Snapshot trade date 与 Packet trade date 不一致时标记 STALE，不用前值冒充当日数据。

历史 Intraday 不允许重建；`FORWARD_INTRADAY_PACKET` 只用于真实未来采集。

## 5. Missing Policy

缺模块仍生成 Partial Packet，字段保持 None，不做 0、均值、中位数或前值填充。

支持区分：SOURCE_UNAVAILABLE、NOT_APPLICABLE、NOT_LISTED、NO_EVENT、NO_DRAGON_TIGER、NO_MARGIN_ELIGIBILITY、PIT_UNAVAILABLE、REACTION_NOT_AVAILABLE_YET、STALE、NOT_IMPLEMENTED。

`has_dragon_tiger=false` 表示确认未上榜；`None + SOURCE_UNAVAILABLE` 表示 Provider/数据不可用。Margin 采用相同语义。

## 6. Event、Reaction、News

Packet 保存 cutoff 前同一股票、同一 Packet trade date 的全部 Event，并汇总 event/new/risk/official-confirmed count、event type counts 和 direction counts，不生成 event score。

Reaction 只有在对应 Snapshot 已完成且 available time 不晚于 cutoff 时进入。事件已可见但窗口未完成时为 `REACTION_NOT_AVAILABLE_YET`。

News Sentiment 继续只装配观点、预期和文本倾向字段；document/source/novel count 放入独立 Attention namespace。

## 7. Feature Quality 与 Lineage

字段级 quality 支持 VALID、MISSING、STALE、PIT_UNSAFE、VENDOR_DERIVED、INFERRED_TIME、LOW_COVERAGE、NOT_APPLICABLE、NOT_LISTED。

每个有来源的 Feature 可追溯至 Snapshot ID、Snapshot type、source、source timestamp、available time、schema/algorithm version 和 content hash。

Packet 保留 Market/Sector/Stock/Capital/News coverage，低覆盖不自动丢弃样本。

## 8. Dataset

`ResearchDatasetBuilder` 支持 symbols、date range、cutoff policy、record source、feature modules 和 history-ready-only。Dataset Definition 保存：dataset ID/version、Registry/Assembly version、date range、cutoff policy、universe versions、feature list、row/symbol count、record source、packet IDs、content hash 和 created time。

Dataset ID/hash 排除 created time，由稳定定义和 Packet IDs 生成；相同输入产生相同 ID/hash。Dataset 不包含任何 label。

`HISTORY_SAFE_V1` 为 39 个 canonical Feature；`FORWARD_SHADOW_V1` 为 124 个已实现 canonical Feature。详情见 Registry 文档。

## 9. Research Shadow

`SentimentV3ResearchShadow.observe` 只生成并批量保存真实未来 Packet。没有 BUY、SELL、HOLD、VETO、position 或 execution API。算法变化通过版本升级保留旧数据，不重置 REAL 研究期。

Shadow stats 只统计来源 Packet 数和 Complete/Partial 数，不输出收益、胜率或 Alpha。

## 10. Repository 与性能

Repository 新增 Packet/Dataset 表，仍在现有 V3 DuckDB 边界内；Packet 以 DataFrame + INSERT SELECT 批量写入，不按 Feature 单行 INSERT。

本地 TEST 基准：

| 操作 | 耗时 |
| --- | ---: |
| Registry load/build | 3.733 ms |
| 单次批量 as-of 读取（11 Snapshot） | 42.373 ms |
| 单 symbol assembly | 119.516 ms |
| 100 symbols assembly | 192.346 ms |
| 5540 symbols assembly | 6378.496 ms |
| 100-row HISTORY_SAFE Dataset build | 161.171 ms |
| 5540 Packet DuckDB batch write | 4909.387 ms |
| 单 Packet DB read | 62.243 ms |

全市场装配仍只进行 3 次批量读取，不随股票数增加查询次数；未发现明显 N+1。

## 11. CLI

新增独立 `scripts.sentiment_v3_research`：assemble、show、validate、build-dataset、feature-registry、shadow-stats。默认只读/研究；只有显式 `--persist` 才写 Research 表，不接策略或交易表。

## 12. 正式系统隔离

- 未读取 Legacy Sentiment V2，避免触发旧缓存或外部增强；S3 可另行只读对照。
- 未装配正式 60/40、formal score 或 action；它们不是 V3 Feature。
- 未修改 DecisionEngine、正式 confidence/VETO、A/B Shadow 或 Canonical Shadow。
- stock_report 未导入 Assembler、Dataset Builder 或 Research Shadow。
- 未产生 TradePlan、订单、成交或持仓变化。

## 13. 测试

S2E 专项覆盖 Registry、alias、History Safe、完整/部分 Packet、missing 语义、finality、inferred time、correction、Event/Reaction PIT、来源隔离、lineage、deterministic Dataset、批量查询/写入和 Research Shadow 无交易 API。

S2E 新增 26 个专项测试。最终全量 pytest：1502 passed、0 failed、0 skipped，
耗时 1116.29 秒；仅有既有 Starlette/httpx 弃用警告。S2E 修改文件 Ruff：0 error。
