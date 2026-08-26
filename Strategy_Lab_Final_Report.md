# AIQUANT-LITE Strategy Lab Final Report

生成时间：2026-08-26T19:58:35.999294+08:00

## 结论

**最优 Overall Candidate：Static 35/40/25（Tech/Fund/Sentiment），龙虎榜缺失采用 Event-Gated 或 Available Renormalization。**

- 状态：**PRODUCTION_CANDIDATE（仅研究候选，禁止自动上线或真实交易）**
- 40bp：年化收益代理 60.81%，Sharpe 1.183，Sortino 1.607，最大回撤 -30.68%，Calmar 1.982
- 排序：IC 0.0428，RankIC 0.0354，IC IR 3.608，Top-Bottom Spread 3.49%
- 交易：18 个非重叠 20D 组合期，894 个入选标的，胜率 72.22%，Profit Factor 2.377
- 相对 Legacy：Sharpe +0.368，最大回撤改善 17.21%，尾损改善 17.07%

该候选通过冻结的 Alpha 与 Defensive 晋级条件，但样本只有 18 个非重叠持有期；2026 也不是完全未观察的 pristine holdout。因此只能标为 Production Candidate，不能直接进入生产权重。

## 数据与 PIT

- Sentiment Replay：2,238,764 个合格包；Feature Registry hash `20f80a62ec073fbe44622cdb6fbcba1c9b02c4abf66823989bc2bff0d9a8b726`，未漂移。
- TechnicalHistoricalPacket：1,936,178 个可用，严格复刻冻结 60 日技术算法。
- FundamentalHistoricalPacket：206,197 个可用；仅使用 `data_available_time/event_time <= decision_cutoff` 的 499 只股票财务记录，缺失保持 unavailable。
- 正式共同样本：170,982 条，348 个交易日，约 492 只/日；2024-11 至 2026-07。
- 20D 标签使用 next-open-to-close executable return；训练/验证间 purge 20 日、embargo 5 日。

## 最优家族

| 家族 | 40bp Sharpe | 年化代理 | 最大回撤 | RankIC | 结论 |
|---|---:|---:|---:|---:|---|
| Legacy 60/40/0 | 0.815 | 35.95% | -37.05% | 0.0006 | 永久基准 |
| Static 35/40/25 | 1.183 | 60.81% | -30.68% | 0.0354 | Overall / Alpha / Defensive |
| Dynamic | 1.242 | 59.57% | -27.38% | 0.0359 | 聚合强，但 2026 为负，拒绝上线 |
| Risk Overlay | 0.820 | 36.24% | -37.05% | 0.0027 | 几乎不改善，拒绝 |
| Regime | 1.094 | 57.55% | -33.04% | 0.0135 | 不及简单 Static |

## 成本敏感性

Static Sharpe：0/20/40/60bp = 1.280 / 1.232 / 1.183 / 1.135。60bp 后仍为 56.91% 年化代理、Sharpe 1.135，不是零成本幻觉。

## 年度稳定性

Static 40bp：
|   period |   periods |   annualized_return |   sharpe |   max_drawdown |
|---------:|----------:|--------------------:|---------:|---------------:|
|     2024 |         2 |            0.234106 | 0.841187 |     -0.0363032 |
|     2025 |        10 |            0.958762 | 1.94464  |     -0.129462  |
|     2026 |         6 |            0.264274 | 0.644064 |     -0.306792  |

2024 只有 2 个非重叠组合期，统计力度低；2025/2026 更有解释力。Static 三年均为正，Dynamic 与 Ridge 在 2026 为负，因此复杂模型淘汰。

## Sentiment 消融与最终角色

| component         |   sharpe |   rank_ic |   max_drawdown |
|:------------------|---------:|----------:|---------------:|
| FULL_AVAILABLE    | 1.18338  | 0.0353603 |      -0.306792 |
| NO_DRAGON         | 1.05203  | 0.0266575 |      -0.340713 |
| GAP_ONLY          | 1.04912  | 0.0133909 |      -0.317316 |
| TURNOVER_ONLY     | 0.952299 | 0.0390735 |      -0.354674 |
| DRAGON_EVENT_ONLY | 0.902569 | 0.0107718 |      -0.350589 |

结论：Sentiment V3 当前更适合作为 **Alpha/Interaction 因子**，同时带来一定防御收益；单独的龙虎榜风险 veto 没有稳定价值。完整组合优于去龙虎榜，说明稀疏事件有边际贡献；但龙虎榜单独使用明显弱于完整组合，主要稳定贡献仍来自换手与 gap/reaction。

## 稳健性与过拟合控制

- Static 邻域通过率：88.89%（冻结阈值 60%）。
- 20 日移动块 bootstrap 的 RankIC 增量：均值 0.0348，95% CI [0.0075, 0.0535]。
- 进行了家族内 BH-FDR；结果表在 DuckDB `candidate_fdr`。该近似未做 Newey-West 修正，仍按探索性证据解释。
- 动态、ML 在聚合期看似更强，但年度稳定性与短 fold 稳定性不足；依据复杂度规则不晋级。
- 未删除失败样本，未修改标签定义，三种龙虎榜缺失方式全部保留。

## 失败方案

- 纯 Risk Overlay：风险事件太稀疏，最大回撤与 Legacy 基本相同。
- Dynamic + Risk：没有超过纯 Dynamic，2026 仍弱。
- Regime：有改善但 RankIC 和 Sharpe 不及 Static。
- Ridge：训练期强、2026 转负，典型时间不稳定。
- 单因子消融：换手、gap、龙虎榜单独均不及完整 Sentiment。

完整拒绝原因见 `Strategy_Lab_Rejected_Candidates.json`。

## 安全结论

- 真实订单：0
- 真实成交：0
- 真实持仓变化：0
- 外部交易接口调用：0
- Git push：未执行

最终测试状态以 `Strategy_Lab_Run_Manifest.json` 为准。研究代码不导入 execution/broker/order，候选 `creates_trade=false`。
