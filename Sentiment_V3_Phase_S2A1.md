# AIQUANT-LITE Sentiment V3 Phase S2A.1

> 状态：RESEARCH / SHADOW；正式权重：0；生产策略接入：无。

## 1. 问题结论

2026-08-13 实测 `AKShareBatchProvider.fetch_market_snapshot -> stock_zh_a_spot_em`。应用现有 `NO_PROXY` 直连策略后仍失败：

```text
ConnectionError
('Connection aborted.', RemoteDisconnected(
  'Remote end closed connection without response'
))
```

此前环境也观察到 `ProxyError`；当前可复现错误是远端在 HTTP 响应前断开。它不是字段标准化或 Market 计算错误，属于网络/上游接口连接路径不稳定。单次失败约 11.4–12.3 秒。

## 2. 已审计数据能力

| 能力 | 接口 | 实测 | PIT/finality |
|---|---|---|---|
| AKShare spot | `stock_zh_a_spot_em` | 当前失败 | 当前截面，不可历史重建 |
| AKShare四类池 | 涨/跌停、炸板、强势池 | 正常，约0.08–0.27秒/池 | 当日盘中可变；历史EOD-only |
| Tushare daily | `pro.daily` | 5,540行；0.286–0.566秒 | EOD-only |
| 腾讯行情 | `TencentSnapshotProvider` | 5,202/5,534，94.00%；166.823秒 | 盘中/准实时 |
| Data Hub本地Snapshot | `market_snapshot_runs/items` | 5,532行、99.96%，但停在8月6日 | 当前已过期 |

腾讯无参模式会自行打开主 DuckDB，受到 Router 所有权保护。V3 Adapter 改为通过现有 `/v1/universe` 只读路由获取股票池后显式传入；测试临时库可直接读库。

## 3. 统一 Adapter

新增 `MarketBreadthProvider -> MarketBreadthRawData`。Collector 不再理解各供应商字段。

统一结果包含：`source/source_timestamp/available_time/symbols/expected_universe_size/coverage_ratio/field_provenance/quality_flags/eod_only`。

实现：

- `AkshareMarketBreadthProvider`
- `TushareMarketBreadthProvider`
- `LocalMarketBreadthProvider`
- `TencentMarketBreadthProvider`

未新增供应商。

## 4. Provider优先级

### INTRADAY

```text
AKShare spot
-> Data Hub同交易日且不超过10分钟的本地Snapshot
-> 腾讯批量行情
-> PARTIAL_DATA / MARKET_BREADTH_UNAVAILABLE
```

AKShare 是一次低成本批量请求；本地 Snapshot 零新增金融 API 且最快，但必须足够新；腾讯实测可用但很慢，仅作最后 fallback。

### FINAL / CORRECTED

```text
Tushare daily EOD
-> AKShare当日收盘行情
-> 腾讯批量行情
-> PARTIAL_DATA / MARKET_BREADTH_UNAVAILABLE
```

Tushare 实测最快、字段完整且按交易日请求。旧 Data Hub 表没有 finality，不能为 FINAL/CORRECTED 背书，因此不进入 FINAL 链。

### PRELIMINARY

```text
Tushare daily -> 新鲜本地Snapshot -> AKShare -> 腾讯
```

## 5. Freshness / PIT

本地 Snapshot 必须同时满足：

```text
snapshot_time <= requested cutoff
stored data_cutoff <= requested cutoff
snapshot trade_date == requested trade_date
0 <= requested cutoff - snapshot_time <= 10 minutes
```

Router 返回 `stale=true` 时立即拒绝；Adapter 再做交易日和10分钟校验。过期记录 `STALE_DATA`，不返回旧 quotes。

Tushare daily 标记 `EOD_ONLY`：`source_timestamp=交易日15:00`，`available_time=实际请求完成时间`。INTRADAY 链不会使用 Tushare daily。

## 6. Provenance与健康诊断

Market Snapshot 新增 `field_provenance` 和 `provider_health`。健康诊断包括 provider、success、latency_ms、error_type、安全清洗后的错误、record_count、coverage、source_timestamp、fallback_used。

真实 FINAL Snapshot：breadth/成交额来自 `Tushare daily EOD`；涨跌停、炸板、强势股来自 `AKShare / Eastmoney`；Snapshot source 为 `MULTI_SOURCE`。

错误最多300字符，去除换行，并遮蔽 API key/token/cookie/authorization/secret。health 保留在 payload，但 latency 不参与 content hash，避免同内容因耗时变化产生新版本。

## 7. 质量语义

新增：`PRIMARY_SOURCE_FAILED/FALLBACK_USED/MARKET_BREADTH_UNAVAILABLE/MULTI_SOURCE`。

全部 breadth Provider 失败时：

```text
advancers = None
decliners = None
unchanged = None
market_turnover = None
breadth_strength = None

quality_flags += SOURCE_UNAVAILABLE
                 PARTIAL_DATA
                 MARKET_BREADTH_UNAVAILABLE
```

AKShare 四类池若成功，其字段仍正常生成。失败不填0，也不终止整个 Collector。

## 8. 场景验证

### Case A：Primary正常

真实 FINAL 使用 Tushare：5,540行，coverage 100%，上涨1,143、下跌4,317、平盘80，延迟472.3ms。连续两次相同内容，Market/Speculation 各只保留1条。

### Case B：Primary失败、Fallback成功

真实强制链 `AKShare -> Tushare`：AKShare `ConnectionError` 11,442.2ms；Tushare成功5,540行、565.7ms；总计12.008s；正确记录 `PRIMARY_SOURCE_FAILED + FALLBACK_USED + EOD_ONLY`。

### Case C：全部Provider失败

专项测试确认：Provider chain 不抛终止异常；返回 `data=None`、完整health、`PARTIAL_DATA + MARKET_BREADTH_UNAVAILABLE`。Collector仍可生成四类池字段。

### Case D：本地Snapshot过期

真实通过 Router 读取8月6日快照，针对8月13日请求被拒绝：556.3ms；错误为 `local market snapshot is stale`；标记 `STALE_DATA/SOURCE_UNAVAILABLE/PARTIAL_DATA/MARKET_BREADTH_UNAVAILABLE`。

## 9. 性能

最终真实 FINAL Collector，使用隔离临时 DuckDB：

| 阶段 | 耗时 |
|---|---:|
| 外部批量阶段 | 473.5ms |
| Tushare breadth | 472.3ms |
| AKShare池（并行单池） | 163.8–269.8ms |
| 标准化 | 3.35ms |
| Market+Speculation计算 | 4.38ms |
| 保存两条Snapshot | 221.77ms |
| as-of读取 | 41.09ms |
| Collector总耗时 | 781.33ms |

腾讯真实 fallback 为166.823秒、5,202/5,534，因此不作常规Primary。本轮没有高频daemon或调度改动。

## 10. 测试与冻结确认

- S2A/S2A.1专项：44 passed。
- 分层回归：249 passed。
- 全量：`1387 passed, 0 failed, 0 skipped`；1条第三方弃用警告。
- 修改文件Ruff：0 error。
- `stock_report` 未导入V3 Provider/Collector；`--no-fetch --no-ai`仍为0外部金融API、0 LLM。
- 未修改指标定义、时间契约、finality、revision、record_source或formal_weight=0。
- 未生成统一情绪分，未赋新权重，未修改60/40、DecisionEngine、A/B Shadow或Canonical Shadow。
- 未产生订单、成交、TradePlan或持仓变化。
- 未执行commit/push/merge/rebase/reset/restore/clean/checkout/stash。
