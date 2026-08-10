# 基本面自动接入、时间截止与运行模式

适用版本：Hermes-OPC 0.10.0。

本模块只用于本地 A 股筛选、研究和决策支持，不连接券商，不生成或执行真实订单。

## 三种模式

| 模式 | 目的 | 自动抓取 | 时间要求 | 因子 |
|---|---|---:|---|---|
| `SCREENING` | 全市场轻量筛选 | 否 | 允许披露时间缺失，但明确标记 | 默认不持久化 |
| `RESEARCH` | 候选股深入研究 | 是 | 默认遵守截止时间；未核验数据仅作参考 | `shadow_mode=true` |
| `DECISION` | 正式 DecisionPacket | 仅当前或近实时受控抓取 | 强制 point-in-time | `shadow_mode=false` |

`/v1/decisions/from-data` 是正式决策入口，因此只接受 `DECISION`。聊天中的普通筛选默认映射到 `SCREENING`；出现“详细分析”“深度研究”等明确研究意图时映射到 `RESEARCH`；只有调用方显式确认正式决策时才进入 `DECISION`。

## 时间语义

- `report_period`：报表所属期间。
- `announcement_time`：官方公告或披露时间。
- `data_available_time`：系统认定最早可使用时间。
- `fetched_at`：本系统抓取时间，不能代替公告时间。
- `data_cutoff`：本次分析可见信息的截止时间。

只有日期而没有具体时间时，默认按 Asia/Shanghai 18:00 可用；小时由 `FUNDAMENTAL_DISCLOSURE_DATE_AVAILABLE_HOUR` 配置。正式决策只使用 `data_available_time <= data_cutoff` 的记录。披露时间缺失、披露时间晚于截止时间、或数据冲突的记录不得进入正式计算。

历史截止时间缺少本地快照时，系统返回 `HISTORICAL_DATA_GAP`，不会调用当前接口补历史数据。估值价格必须来自不晚于 `data_cutoff` 的 canonical market record。

## 指标口径

所有指标由确定性 Python 代码计算，不使用 LLM。当前公式版本为 `fundamental-metrics-v1`。

- 资产负债率 = 总负债 / 总资产。
- 经营现金流 = 现金流量表 `n_cashflow_act`，保留报告期累计口径。
- ROE = 归母净利润 / 平均归母净资产；平均净资产使用可比期初和期末归母净资产。缺少可比期时不计算。
- 营业收入增长率 = 本期收入 / 上年同口径同期收入 - 1。
- 净利润增长率 = 本期归母净利润 / 上年同口径同期归母净利润 - 1。
- PB = 截止时间股价 × 总股本 / 归母净资产。
- PE TTM：当前只在已取得年度归母净利润及截止时间价格时计算。尚未具备完整四季度滚动数据时不制造 TTM 值。
- PEG = 正 PE /（正净利润增长率 × 100）。PE 或增长率无效、为零或为负时不计算。

百分比统一用小数表示，例如 18% 保存为 `0.18`。缺失值保持 `null`，不会填 0，不使用行业均值替代。年报、半年报和季报按 `period_type` 隔离，可比增长只允许相同报告日和相同期间类型。

正式策略仍使用既有技术面 60%、基本面 40% 的组合权重；本阶段没有调整评分阈值或风险否决规则。

## 证据和人工输入

FUNDAMENTAL FactorOutput 可追溯至：

- `canonical_financial_records`
- 原始 `data_records`
- 用于估值的 `canonical_market_records` 和原始市场记录
- `manual_fundamental_inputs`
- `fundamental_analysis_audits`

人工输入固定标记 `USER_PROVIDED` 和 `UNVERIFIED`。默认不覆盖 canonical 数据。正式决策只有在调用方显式允许、填写覆盖原因并确认操作者后才采用人工覆盖；行为会进入追加式审计记录。系统只保存结构化指标、证据、风险标志和操作记录，不保存模型隐藏思维链。
