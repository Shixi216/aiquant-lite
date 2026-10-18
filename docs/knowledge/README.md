# 研究证据库 · Research Evidence

**百万级数据沉淀，版本化策略研究，带来源的决策与复盘。**

AIQUANT-LITE 把数据层、研究层、决策层和知识层连在一起：数据有覆盖边界，实验有比较协议，结论有证据和有效期，失败结果也进入下一轮研究。

![Research evidence inventory](../assets/research-evidence.svg)

图中数字来自 **2026-10-19 对单个本地安装的只读盘点**，分别为历史日线行数、Replay 特征包行数、候选定义数和 Walk-Forward 指标行数。数据集相互关联，不能相加；原始数据不随仓库分发。详见 [数据资产](data-assets.md) 与 [机器可读证据摘要](evidence-snapshot-2026-10-19.json)。

## 四层研究资产

| 层次 | 已沉淀的内容 | 对研究者的价值 |
|---|---|---|
| **事实层** | 标准化日线、财务记录、历史风险与 Replay 特征包 | 从已落库事实出发，明确时间、覆盖和缺失 |
| **实验层** | 202 个候选定义、4 档成本、144 行 Walk-Forward 指标 | 同时检查组合收益、成本、年度稳定性和复杂度 |
| **决策层** | PIT 输入约束、版本化 DecisionPacket、证据关联、风险 VETO | 可以追问结论依据，并检查当时的信息边界 |
| **知识层** | 从项目对话、长期记忆和排障记录提炼的 8 个主题 | 把研究经验写成能复用、能质疑、能更新的规则 |

## 8 个精选主题

| 文档 | 核心问题 |
|---|---|
| [数据资产与覆盖边界](data-assets.md) | 数据规模是多少，哪些行数不能相加？ |
| [PIT、时间与冻结实验协议](point-in-time-contract.md) | 如何避免未来信息进入历史判断？ |
| [策略实验协议对照](experiment-comparison.md) | 不同标签、入场、成本的结果如何解释？ |
| [失败实验与无效基准案例](negative-results.md) | 为什么正 IC、高聚合收益仍可能不晋级？ |
| [模型、数据库与日志排障复盘](operations-lessons.md) | 如何定位空输出、网络错误和漏记调用？ |
| [Hermes 研究工作流](hermes-research-workflows.md) | Agent 如何复用事实工具并保留人工边界？ |
| [Forward 与市场情绪观察进度](observation-progress.md) | 已有多少真实业务日期，后续验证差在哪里？ |
| [记忆与对话知识治理](memory-and-dialogue-governance.md) | 对话经验如何变成带证据的可维护知识？ |

## 如何读这些证据

**已核查的盘点数字**来自 Owner API 或离线只读表统计；**历史研究结论**来自保留报告，本次未重跑；**公开实现**可以沿文档中的源码链接检查。三类证据分别标记，避免把代码存在、历史报告和当前有效结果混为一谈。部分本地研究归档的代码与数据不包含在公开源码快照中。

公开材料保留方法、汇总数字和失败教训。完整聊天、个人记忆、原始行情、第三方技能、授权凭证与账户状态留在本地。发布范围和核查方法见 [本次审核](../release-audit-2026-10-19.md)。

**English:** This evidence hub documents one local installation's data inventory, research protocols, negative findings, operations lessons and curated Hermes workflows. Counts are non-additive metadata, not distributed datasets or investment-performance claims. See the [dated JSON inventory](evidence-snapshot-2026-10-19.json) for scope and provenance.

[返回项目首页](../../README.md)
