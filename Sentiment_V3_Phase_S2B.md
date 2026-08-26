# AIQUANT-LITE Sentiment V3 Phase S2B

> 状态：RESEARCH / SHADOW；formal_weight=0；未接入 stock_report 或正式决策链。

## 1. Universe 契约

统一对象 `MarketUniverse`：

- `universe_id / universe_version / rules_version`
- `trade_date / as_of`
- `expected_symbol_count / eligible_symbol_count / observed_symbol_count`
- `excluded_symbol_count / coverage_ratio`
- `source / source_universe_version`
- `exclusion_summary / symbols / symbol_hash / created_at`

规则版本为 `A_SHARE_UNIVERSE_RULES_V1`，版本前缀为 `A_SHARE_BASE_V1`。Universe 内容和规则发生变化会产生新 version，旧版本不覆盖。

只保留普通 A 股股票：交易所限 SH/SZ/BJ，`security_type=STOCK`，交易日处于上市区间且状态为上市。ETF、指数、债券、可转债、B 股不进入。采用三个少量子集合：

1. `BASE_A_SHARE_UNIVERSE`：包含北交所、ST/*ST、新股和停牌股。
2. `BREADTH_ELIGIBLE_UNIVERSE`：从 Base 排除当日明确停牌 `suspend_type=S`；复牌 `R` 不排除。
3. `SECTOR_ELIGIBLE_UNIVERSE`：再要求 cutoff 前可获得的板块 membership。

基础 Universe 与指标有效 Universe 分离，避免把 ST、新股或停牌规则混成一个隐式分母。

## 2. Provider symbol 差异

2026-08-13 实测：

- 本地主数据：5,534 只 active；SH 2,310、SZ 2,892、BJ 332。
- Tushare daily：5,540 只。
- Tushare 当前 listed master：5,543 只；排除 3 只当日停牌后 breadth eligible=5,540。

本地与 Tushare daily 的净差 6 只由以下集合差异组成：

- Tushare daily 多 10 只：均为近期上市、本地主数据尚未同步的股票。
- 本地多 4 只：3 只当日停牌；1 只已于 2026-07-30 退市但本地主数据仍保留 active。

因此差异来自主数据新鲜度、停牌和退市状态，不是随机漏数。

## 3. 腾讯时间预算

`TencentMarketBreadthProvider` 的总预算为 8 秒，覆盖：

```text
Router Universe 读取
+ 腾讯所有批次行情请求
+ 结果等待
```

V3 预算调用的单批网络 timeout 上限 2 秒；不 retry、不 sleep。超时抛 `TimeoutError`，Provider chain 记录 `PROVIDER_TIMEOUT`，全部失败时继续生成 `PARTIAL_DATA`。真实全市场验证在 8.003 秒退出，不再发生 166 秒同步阻塞。

未给腾讯 Provider 设置总预算的旧调用保持原有单批 15 秒语义，避免改变其他系统调用方；V3 fallback 始终显式传入预算。

## 4. SectorSentimentSnapshot

实现 industry 与通用 concept 数据结构。字段包括：

- 标识与链接：sector_id/name/type、universe_id/version、market_snapshot_id、membership_version。
- 覆盖与宽度：member/observed count、coverage、advancers/decliners/unchanged、advance_ratio。
- 收益：mean_return、median_return、market_return、relative_market_return、relative_market_breadth。
- 成交：sector_turnover、previous_sector_turnover、turnover_change、volume_change。
- 池状态：limit_up/down_count、broken_board_count、limit_up_ratio、broken_board_rate。
- 扩散/集中候选：leader_symbol/return、top_n mean/median。
- quality、missing、field_provenance 和标准 meta。

行业使用 Tushare `stock_basic.industry` 当前唯一主行业，实际得到 110 个行业。概念支持多 membership、不会随机选一个，但当前没有稳定 PIT 概念成员数据源，因此 Collector 未采集概念。

## 5. Membership PIT

当前行业 membership 来源为 Tushare `stock_basic` 当前行业。它没有可靠历史有效区间，所有相关 Sector/Stock Snapshot 明确记录：

```text
CURRENT_MEMBERSHIP_ONLY
PIT_UNSAFE_FOR_HISTORY
```

结构支持 `effective_from / effective_to / available_time / membership_version / pit_safe_for_history`。项目已有 `historical_industry_memberships`（申万成员、含 valid_from/to 和 available_time），但本轮未自动回填或伪造；后续仅在表中真实覆盖相应时点时才能标记历史 PIT-safe。

## 6. 相对强弱与 Leader

使用连续变量，不形成评分：

```text
sector_relative_market = sector_mean_return - market_mean_return
stock_relative_market  = stock_return - market_mean_return
stock_relative_sector  = stock_return - sector_mean_return
```

同时保存 stock、sector、market 原始收益。Market 或 Sector 缺失/不完整时 relative 为 `None`，不使用 0。

Leader 版本 `RETURN_RANK_V1`：按 Snapshot cutoff 时可见收益降序，symbol 稳定打破并列。当前数据源为 EOD，因此它只能解释为 EOD leader，全部 Snapshot 标记 `EOD_ONLY`。

## 7. StockTradingSentimentSnapshot

实现：return_pct、open_gap_pct、intraday_return_pct、amplitude、turnover_rate、volume_ratio、volume_change、turnover_change、CLV、high-to-close drawdown、low-to-close recovery、gap/fade、gap/follow-through、market/sector 原始及相对收益、market/sector volume context、limit-up/down/broken 状态，以及三层 snapshot/universe 链接。

`CLV_V1 = (close-low)/(high-low)`；`high==low` 返回 `None + missing_fields`。

`GAP_PATH_V1` 不使用固定涨跌幅阈值：

```text
direction = sign(open_gap)
path = direction * intraday_return
gap_and_follow_through = max(path, 0)
gap_and_fade = max(-path, 0)
```

涨停/跌停/炸板状态来自 Market Snapshot 的明确池集合，不从收益率阈值推断。

相对成交量只在相同 Tushare 单位且当前/昨日完整覆盖时计算；不完整则为 `None`。Tushare `vol` 单位保持同源日间比较，`amount` 统一从千元转换为元。

## 8. 数据源与 Collector

新增 `TushareSectorStockSource` 和 `SectorStockSentimentCollector`。一次批量运行调用：daily、daily_basic、stock_basic、suspend_d、trade_cal 和上一交易日 daily；无逐股票或逐行业外部请求。

复用已有 `SentimentV3Repository`：Universe 独立版本表；Sector/Stock 复用统一 Snapshot 表和 as-of、finality、revision、record_source、hash 契约。5,650 条 Sector/Stock 采用 DataFrame 注册后单条 `INSERT ... SELECT` 批量写入，无逐股票 DB 查询或写入。

本轮还最小修复共享 Snapshot ID 的来源碰撞：content hash 不变，snapshot_id 增加 record_source 后缀，使 REAL/TEST/SYNTHETIC/REPLAY 相同内容不会主键冲突。

## 9. 真实性能

2026-08-13 数据、隔离临时 DuckDB：

| 阶段 | 耗时 |
|---|---:|
| Tushare 完整批量采集 | 1.483 s |
| Universe 构建 | 10.86 ms |
| 110 个 Sector 计算 | 43.38 ms |
| 5,540 个 Stock 计算 | 508.54 ms |
| Universe + 5,650 Snapshot 批量写入 | 951.76 ms |
| 单只 Stock as-of 读取 | 57.32 ms |

写入最初使用 DuckDB `executemany` 超过 3 分钟，已停止并改为单条批量写入；最终不存在明显 N+1。

## 10. 测试与限制

- S2A/S2A.1/S2B 专项：88 passed。
- S2B 新增：44 tests。
- 分层回归：308 passed。
- 全量：1,431 passed，0 failed，0 skipped；1 条第三方 Starlette/httpx 弃用警告。
- 修改文件 Ruff：0 error。

已知限制：当前行业映射为 current-only；概念仅有数据结构；所有本轮真实 Sector/Stock 数据为 EOD-only；当日新股缺少昨日数据时变化字段保持 `None`；相对成交量要求严格同源完整覆盖。

未接入 stock_report，未修改正式 score/action/confidence/VETO、A/B Shadow 或 Canonical Shadow，未产生交易状态变化。