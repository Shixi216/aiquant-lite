# Sentiment V3 Phase S2C — Capital Sentiment Data Layer

状态：`RESEARCH / SHADOW`。所有新字段 `formal_weight = 0`；未接入 `stock_report`、Scanner、DecisionEngine、A/B Shadow 或 Canonical Shadow。

## 1. 当前资金面审计

### 1.1 现有实现

| 位置 | 当前行为 | 审计结论 |
|---|---|---|
| `data_hub/services/capital_flow_enhanced.py` | 报告时按股票调用 Tushare `margin_detail`、`top_list`，再与量价按 40/40/20 重算旧资金分 | 逐股外部请求；异常和无记录都变空列表；龙虎榜空列表直接解释为“未上榜”；旧链保留但不复用到 V3 |
| `data_hub/providers/tushare_snapshot.py` | 按交易日批量获取 `daily/daily_basic/moneyflow/stk_limit` | 批量方式可借；注释把 `moneyflow` 称为“主力资金”过度确定，V3 改标 `VENDOR_DERIVED` |
| `trading/scanner/freshness_guard.py` | 将 `net_mf_amount` 放入全市场快照 payload | 保留供应商原字段，但旧写入存在 `or 0`；不作为 V3 缺失语义 |
| `trading/research/capital_flow/` | canonical 日线量价、换手、融资、行业聚合和 Shadow 分数 | 主要是本地量价研究，不是供应商原始资金仓库；已有 score/weight 不进入 S2C |
| `data_hub/providers/full_market.py` | AKShare `stock_margin_detail_sse` | 仅上交所两融明细，不能作为统一 A 股全市场主源 |
| 腾讯 Provider | 行情、成交额、涨跌幅 | 无已实现且已核实的资金流、两融或龙虎榜批量能力 |
| DuckDB | `capital_flow_symbol/sector/market_snapshots` 等 | 仅 11 个股、2 板块、1 市场旧 Shadow 快照；没有可复用的全市场 raw margin/LHB 历史表 |

旧实现存在三类冲突：

1. `moneyflow` 订单大小分桶被注释为“主力”，但供应商方法并不等于机构真实持仓；
2. `top_list` 只给上榜汇总，旧注释却写“机构/游资”；机构席位必须用 `top_inst` 的公开席位名独立识别；
3. 接口失败、无数据和真实 0 在旧评分/Scanner 的部分路径中会收敛为 `[]` 或 `0`。S2C 不改冻结旧链，但在新契约中严格分开。

### 1.2 已核实 Provider/API

S2C 实际只使用项目已有 Tushare，不新增供应商：

- `daily(trade_date)`：成交额，原单位千元；
- `daily_basic(trade_date)`：换手率，原单位百分比；
- `moneyflow(trade_date)`：小/中/大/超大单买卖额和 `net_mf_amount`，原单位万元，分类口径为供应商衍生；
- `margin(trade_date)`：交易所级两融汇总；
- `margin_detail(trade_date)`：个股融资余额、买入、偿还、融券余额/卖出/偿还；
- `top_list(trade_date)`：龙虎榜上榜汇总、原因、买卖净额；
- `top_inst(trade_date)`：公开席位明细；
- `trade_cal`：前一/下一交易日，用于变化和可用时间。

2026-08-12 只读实测：`daily/daily_basic/moneyflow` 各 5539 行；`margin_detail` 4428 原始行；`top_list` 50 行、46 个唯一股票；`top_inst` 570 行。AKShare/Tencent 仅保留现有能力，不作为本轮采集源。

北向/跨境资金未实现：仓库没有已归档、可核实 available_time 且历史 PIT 安全的连续表；不因外部项目列出该指标就强行接入。

## 2. Capital taxonomy 与 methodology

| methodology_class | S2C 字段 |
|---|---|
| `OBSERVABLE_FACT` | 成交额、换手率、融资余额/买入/偿还、融券余额/卖出/偿还、两融余额、龙虎榜买入/卖出/净额、公开席位数、上榜原因 |
| `DERIVED_TRANSPARENT` | 成交额变化、融资净买入=`margin_buy-margin_repay`、融资余额变化/变化率 |
| `VENDOR_DERIVED` | 小/中/大/超大单净流、`net_mf_amount`、大单+超大单的 `vendor_main_net_flow`、相关流量比率和同口径相对值 |
| `EVENT_CAPITAL` | 龙虎榜、机构专用席位买卖、上榜原因、席位列表 |

每个字段由 `field_provenance` 和 `field_methodology` 记录来源、方法类别、透明度、内部单位和公式版本。`vendor_main_*` 从不命名为 institution。只有 `top_inst.exalter` 明确包含“机构专用”的公开席位才进入 `institution_*`，且其语义仅是 `institution_seat_flow`，不是全天机构资金。

## 3. Snapshot 契约

复用 `SentimentSnapshotMeta`，schema 升为 `sentiment-v3-snapshot-1.3.0`；新增五种 `SnapshotType`：

- `MARKET_CAPITAL`
- `SECTOR_CAPITAL`
- `STOCK_CAPITAL`
- `MARGIN_FINANCING`
- `DRAGON_TIGER`

仍使用 `source_timestamp <= available_time <= data_cutoff <= snapshot_time <= created_at`、`FINAL/CORRECTED`、`REAL/TEST/SYNTHETIC/REPLAY`、稳定 `content_hash`、revision/correction chain、field provenance、quality flags 和 `formal_weight=0`。Repository 继续使用同一 `sentiment_v3_snapshots` 表、批量写入和 `available_time <= cutoff` 的 as-of 查询。

### 3.1 时间/PIT

| 数据 | source_timestamp | available_time | finality/flag |
|---|---|---|---|
| 日线/供应商 moneyflow | 交易日 15:00 | 保守设为当日 18:00 | `FINAL + EOD_ONLY + AVAILABLE_TIME_INFERRED` |
| 两融 T 日 | T 日 15:00 | 下一开市日 09:00 | `FINAL + EOD_ONLY + NEXT_SESSION_AVAILABLE + AVAILABLE_TIME_INFERRED` |
| 龙虎榜 T 日 | T 日 15:00 | 保守设为 T 日 18:00 | `FINAL + EOD_ONLY + AVAILABLE_TIME_INFERRED` |

以上 available_time 是保守研究假设，不伪装成交易所精确发布时间。15:00/盘中 as-of 无法看到当日晚间龙虎榜；T 日早盘无法看到 T 日两融。供应商修正按现有 `CORRECTED -> corrects_snapshot_id` 链保存，历史 as-of 不穿越。

## 4. 数据结构与公式

### 4.1 MarketCapitalSnapshot

实现：Universe/Market 链接、expected/observed、`market_turnover`、前日成交额、成交额变化；供应商分桶的总买/总卖、净流、`vendor_main_net_flow`、主力流/成交额比率；Margin 链接预留。

`vendor_main_net_flow = (buy_lg-sell_lg) + (buy_elg-sell_elg)`，但方法类别仍是 `VENDOR_DERIVED`。没有把全市场 vendor flow 称为机构净流入。

### 4.2 SectorCapitalSnapshot

实现：industry sector、member/observed/coverage、成交额及变化、vendor main flow sum/median、大/小单净流汇总、主力流比率、相对市场成交额与同口径相对 flow、Market/Sector/Universe 链接。

行业成员沿用 S2B 当前 Tushare `stock_basic.industry`，因此实际数据标记 `CURRENT_MEMBERSHIP_ONLY + PIT_UNSAFE_FOR_HISTORY`。未来若提供有效时序 membership，该标记可解除。Concept Capital 未实现，原因同 S2B：当前没有 PIT 安全的历史概念成员图谱。

### 4.3 StockCapitalSnapshot

实现：成交额、前日成交额、变化、换手率；供应商小/中/大/超大单净流、main flow、flow ratio；同源同口径的相对市场/行业流；Market/Sector/Stock/Universe Snapshot 链接。

相对资金使用归一化比率差：`stock_vendor_main_flow/stock_turnover - context_vendor_main_flow/context_turnover`。若来源或方法论不兼容，结果为 `None + INCOMPATIBLE_METHODOLOGY`，禁止直接相减。

### 4.4 MarginFinancingSnapshot

实现：`has_margin_data` 三态、融资余额/买入/偿还/净买入、融券余额/卖出/偿还、两融余额、融资余额变化/变化率、市场两融总额、Universe 链接。

- Provider 成功且有行：`has_margin_data=True`；
- Provider 成功但 Universe 股票无行：`False`，金额为 `None`；
- Provider 失败：`None + SOURCE_UNAVAILABLE`。

覆盖率按 `Universe ∩ margin_detail` 计算，不能用原始行数冒充。

### 4.5 DragonTigerSnapshot

实现：`has_dragon_tiger` 三态、原因、买/卖/净额、机构专用席位买/卖/净额、席位数、机构席位数、Top 买卖席位、Universe 链接。

- Provider 成功但未上榜：`False`；
- Provider 失败：`None + SOURCE_UNAVAILABLE`；
- 不建立知名游资身份表，不把普通营业部自动称为游资。

## 5. 单位与缺失

内部金额统一为 `CNY`：

- `daily.amount * 1,000`；
- `moneyflow.*_amount * 10,000`；
- margin/LHB 原值直接按 CNY；
- `turnover_rate / 100` 变 DECIMAL；
- `rqmcl/rqchl` 为 `SHARES`，不与 CNY 相加。

`None` 永不自动变成 0。只有源真实返回 0 或明确公式得到 0 才保存 0。旧冻结链仍存在 `or 0`，但不会进入 S2C Snapshot。

## 6. Collector、缓存与频率

- `CapitalCollector`：一次批量 `daily/daily_basic/moneyflow`，内存标准化后批量生成 Market/Sector/Stock；
- `MarginCollector`：按日批量 `margin_detail/margin`，不逐股请求；
- `DragonTigerCollector`：按日各一次 `top_list/top_inst`；
- 任一 Provider 异常被隔离为部分/缺失 Snapshot，不抛到用户报告；错误仅留类型或清洗后摘要，不保存 Token/Cookie；
- 相同内容+schema+algorithm 产生稳定 hash，Repository 去重；修正生成新 revision；
- stock_report 未 import/调用任何 S2C Collector。

建议调度：Capital 在 EOD 源稳定后一次；Sector 紧随 Stock Capital 本地派生；Margin 每交易日一次、按下一交易时段可见语义；Dragon Tiger 盘后事件批量一次并允许稍后 correction。禁止统一每 5 分钟轮询。

## 7. 覆盖率与性能

2026-08-12 真实数据、隔离临时 DuckDB、`REPLAY` 验证：

| 项目 | 结果 |
|---|---:|
| Universe | expected/eligible 5543；行情 observed 5539；99.9278% |
| Capital Provider 总耗时 | 1.031 s；moneyflow 单接口 0.482 s |
| Stock/Market Capital 计算 | 610.650 ms（5539 Stock） |
| Sector Capital 增量计算 | 5.718 ms（110 industry） |
| Margin Provider 总耗时 | 1.250 s；原始 4428 行 |
| Margin Universe 覆盖 | 3850/5543 = 69.4570%；生成 5543 个三态 Snapshot |
| Margin 计算 | 770.289 ms |
| Dragon Tiger Provider | 0.465 s；46 原始唯一事件、45 个在 Universe 内 |
| Dragon Tiger 评估覆盖 | 100%（接口成功后每个 Universe 股票为 True/False）；生成 5543 Snapshot |
| Dragon Tiger 计算 | 607.358 ms |
| DB 批量写入 | Capital 1087.936 ms；Margin 1611.813 ms；LHB 1958.735 ms；合计 4658.483 ms |
| 单个 as-of 读取 | 61.555 ms |

计算使用集合映射、内存聚合和三次批量写入，没有 5000 次逐股票 DB/API 查询，无明显 N+1。完整后台批量秒级可接受；不进入同步报告路径。

## 8. GitHub 参考登记

| source_repo | source_file | borrowed_concept | our_difference | license |
|---|---|---|---|---|
| qwen-dianjin | `market-sentiment-monitor/SKILL.md` | 将两融、龙虎榜、成交额列为不同资金事实 | 不用固定阈值、总分或商业源；增加 PIT/methodology | 根/目标路径统一许可未核实，只借概念 |
| FinClaw | `skills/a-share-board-analysis/SKILL.md` | 批量 Snapshot、事件池与连续市场事实分离 | 龙虎榜单独 `EVENT_CAPITAL`，不输出投资判断 | Apache-2.0 |
| AKShare | `stock_feature/stock_fund_em.py`, `stock_lhb_em.py` | 资金流/LHB 批量接口目录和供应商口径警示 | 本轮实际使用现有 Tushare；AKShare SSE 两融不充当全市场源；不复制代码 | MIT |

## 9. 已知限制

1. `moneyflow` 分桶方法不透明，只能作为供应商候选特征；
2. industry membership 是当前映射，历史 Sector Capital 不 PIT 安全；
3. available_time 为保守推定值，已显式标记；
4. Margin 只覆盖约 69.46% Base Universe，未覆盖不等于 0；
5. 龙虎榜不是连续全日机构资金，只是盘后公开事件；
6. 游资身份、北向/跨境资金、Concept Capital 均未实现；
7. 未生成 Capital Score、方向标签、正式权重或策略动作。
## 10. 测试结果

- S2C 新增专项：14 tests；
- S2A/S2A.1/S2B/S2C 联合专项：全部通过；
- Sentiment/Provider/Data Hub/stock_report/DecisionEngine/A-B Shadow/Canonical 专项：全部通过；
- 全量：`1445 passed, 0 failed, 0 skipped`（pytest 退出码 0；仅 1 条第三方 Starlette/httpx deprecation warning）；
- Ruff（S2C 新增/修改文件）：`All checks passed`。

隔离核对：`stock_report` 没有 import S2C Capital/Margin/DragonTiger Collector；`--no-fetch --no-ai` 回归继续保持 0 外部金融 API、0 LLM。未创建 TradePlan、订单、成交或持仓变更；未执行 commit/push/merge/rebase/reset/restore/clean/stash/checkout。