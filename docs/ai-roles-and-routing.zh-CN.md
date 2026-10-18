# AI 分类、角色分工与模型路由

按公开源码 `c561083` 核对，整理于 2026-10-19。本文描述实现与配置契约，不宣称所有模型在线可用；当前更新只补充文档与可视化。

## 先看三层分工

| 层次 | 负责什么 | 关键边界 |
|---|---|---|
| **交互与工具编排：Hermes / MCP / Web / CLI** | 接收研究问题，取得工具数据，展示研究结果；Hermes 的主模型由其自身安装配置 | Hermes 主模型不是 Router 的第八个固定角色；入口有各自的工具与确认契约 |
| **专业 AI：Agent Router 与研究提取器** | 新闻结构化、公告核验、五维摘要和风险复核；按条件调用不同 Provider | 角色定义、Provider 配置、Handler、专用流水线、在线成功需要分别核查 |
| **确定性内核：Data Hub / Quant / Risk / Evidence** | 数据取得与时点检查、扫描与评分、风险 VETO、相关流程的决策包与模拟 | 数据库数值与正式分数来自对应计算路径；AI 风险复核不替代硬风控 |

五维研究指技术、基本面、情绪、政策 / 新闻、资金的证据分类；当前四维横截面评分与旧版 60/40 回退是不同计算路径。研究综合 Prompt 中仍有旧版 60/40 边界文字，不能据此把整个项目描述成仅使用旧策略。当前评分语义见 [主页工程细节](../README.md#一套研究内核多种工作入口) 与 [架构](architecture.md)。

## 七个角色的输入、产出与实现状态

### 信息处理：把材料变成可用证据

**新闻处理 `news_processor` · Qwen**

- 输入：提供的媒体文本、公告材料和来源元数据；新闻流水线先从 Data Hub 构建材料包。
- 职责：提取事件时间、主体、数值与单位，保留评级 / 目标价等观点的发布者，聚类重复报道并识别新增信息。
- 输出：`items / clusters / summary`，含情绪方向、强度、市场信号、置信度和待核验说法。
- 约束：`FACT` 依赖上游官方 / 核验元数据；媒体转述与猜测分别保留为 `MEDIA_STATEMENT / SPECULATION`。置信度与强度为 0–1，缺失保留空值。
- 实现：通用 Handler、新闻流水线；首次结构校验失败时可请求一次修复，仍受外层物理调用预算约束。
- 源码：[新闻 Handler](../router/services/news_processor_handler.py)、[新闻结构校验](../router/services/news_output.py)、[新闻流水线](../router/services/news_pipeline.py)。

**公告核验 `announcement_verifier` · Qwen**

- 输入：官方公告文档、正文、证券主体与待核验材料；沿公告证据链检查，不把标题当完整正文。
- 职责：核对主体、日期、原文事实与证据关联，整理结构化核验结果。
- 实现：专用公告流水线和核验服务。它没有通用 `/v1/invoke` Handler，调用应走公告入口。
- 源码：[公告流水线](../router/services/announcement_pipeline.py)、[公告核验](../router/services/announcement_verifier.py)、[公告证据](../router/services/announcement_evidence.py)。

### 研究综合：把五维证据变成有缺口说明的研究摘要

**个股研究综合 `research_synthesizer` · Qwen**

- 输入：调用方提供的本地点时五维摘要；Handler 本身不自动搜集完整市场数据。
- 输出：`symbol / summary / factor_observations / missing_data / risk_flags / confidence / not_trade_recommendation`。
- 实现：通用 Handler；JSON / Schema、证券代码一致性与研究边界字段校验，默认 Prompt 要求列出缺失和风险、不输出交易动作。
- 约束：研究综合结果属于 `RESEARCH`；五维摘要不等于五维正式评分，更不自动生成实盘操作。
- 源码：[研究 Handler](../router/services/research_synthesizer_handler.py)、[研究 Schema](../router/schemas/research.py)。

### 风险复核：检查另一个 Agent 的结论

**风险控制 `risk_controller` · DeepSeek**

- 输入：原始角色、问题、证券代码、风险等级与待复核输出。
- 职责：检查证据充分性、事实与推断混淆、来源冲突、重大遗漏和需要人工处理的事项。
- 输出：`decision=approve|revise|reject`、`assessed_risk_level / findings / required_actions / confidence`。
- 实现：既可作为通用角色调用，也可在其他角色的 `high / critical` 通用请求后追加复核；不会对风险角色自身再递归追加。
- 约束：`approve` 是模型复核意见；高风险结果仍要求人工复核，确定性硬 VETO 不受该意见覆盖。
- 源码：[风险 Handler](../router/services/risk_controller_handler.py)、[通用调用与追加复核](../router/services/invocation.py)。

### 视觉与对抗审查：明确标注尚未完成的能力

| 角色 | 设计职责 | 当前代码边界 |
|---|---|---|
| `vision_reader` · MiMo | 普通图表、截图、报告页面读取 | Provider 已实现，政策提取器有 `IMAGE_ONLY` 分支及 Qwen 再检查；通用请求只有文本 `prompt / system_prompt`，没有图片载荷，且缺少通用 Handler。该分支不足以证明图像已被传输和识别 |
| `complex_vision` · MiMo | 复杂图表、跨页表格与高难视觉材料 | 已注册角色，缺少通用 Handler；当前 MiMo 适配器共享单一配置模型，并未按两类视觉角色自动切换模型 |
| `adversarial_reviewer` · Hy3 | 为研究结论寻找反例、漏洞和证据缺口 | 注册表保留设计；启用函数返回禁用，Provider 注册表和 Handler 注册表均未接入 Hy3 |

MiMo 配置齐备时，角色列表的 `enabled` 可以为真，但仍不代表通用调用已支持图片。视觉角色应标记为待完善能力。源码：[角色表](../router/registry.py)、[Handler 表](../router/services/role_handler_registry.py)、[MiMo 适配器](../router/providers/mimo.py)、[请求 Schema](../router/schemas/invocation.py)、[政策提取器](../trading/research/policy_news/extractor.py)。

## 模型归属与实际请求模型

| Provider | 当前职责 | 配置默认模型 | 角色表偏好 | 选择说明 |
|---|---|---|---|---|
| **Qwen** | 新闻、公告、五维研究；情绪 / 政策结构提取 | `qwen3.7-plus` | 三个角色均为 `qwen3.8-max-0902` | 适配器实际请求读取 `router_settings.qwen_model`，由 `OPC_QWEN_MODEL` 等配置覆盖 |
| **DeepSeek** | 风险审查与条件升级复核 | `deepseek-v4-pro` | `deepseek-v4-pro` | 适配器读取 Provider 模型配置 |
| **MiMo** | 视觉角色设计及局部条件路由 | `mimo-v2.5` | `MiMo V2.5` / `MiMo V2.5 Pro` | 当前适配器读取同一个 `mimo_model`；必须显式配置端点；不代表已完成两档视觉模型调度 |
| **LongCat** | 早期新闻主路由；当前保留适配器 | `LongCat-2.0` | 当前无角色绑定 | Provider 注册存在；当前新闻与情绪主路由使用 Qwen，未自动启用 LongCat 跨模型回退 |
| **Hy3** | 对抗审查的保留设计 | 未接入配置 | `Hy3` | 当前禁用，不属于已可调用 Provider |

不要从 `preferred_model`、旧脚本的硬编码断言或注册数量推断实际调用。**请求模型看 Provider 设置，返回模型看调用结果，在线可用性看成功记录与错误状态。** 配置就绪检查只检查字段是否齐备。

来源：[模型配置](../router/config.py)、[Provider 注册](../router/services/provider_registry.py)、[Qwen 适配器](../router/providers/qwen.py)、[DeepSeek 适配器](../router/providers/deepseek.py)、[LongCat 适配器](../router/providers/longcat.py)。

## 通用 Router：风险等级与调用预算

下表适用于 `/v1/invoke`；其他专用提取器不应被宣称天然共享同一预算契约。

| 预算 | 单次输出 Token 上限 | 一次任务物理调用上限 |
|---|---:|---:|
| `economy` | 512 | 1 |
| `standard` | 2,048 | 4 |
| `premium` | 8,192 | 8 |

| 风险等级 | 温度上限 | 最低预算 | 人工复核 | 追加风险角色 |
|---|---:|---|---|---|
| `low` | 0.8 | `economy` | 不由该策略强制 | 无 |
| `medium` | 0.4 | `economy` | 不由该策略强制 | 无 |
| `high` | 0.2 | `standard` | 必须 | 条件调用 `risk_controller` |
| `critical` | 0.0 | `premium` | 必须 | 条件调用 `risk_controller` |

角色可用性、Handler、Provider 与风险 / 预算组合先检查，再执行模型请求。`critical + standard` 会提前拒绝。首次请求、网络重试、结构修复与追加风险复核共享任务的物理调用额度；请求 Token 数还受预算上限约束。

追加复核通过 `risk_review.status` 返回 `completed / unavailable / failed / budget_exhausted`；任一状态都保留高风险人工复核要求。当前机制返回要求与状态，不应描述为已经部署完整人工审批队列。

源码：[路由策略与矩阵](../router/services/routing_policy.py)、[复核 Schema](../router/schemas/routing.py)、[调用服务](../router/services/invocation.py)。

## 情绪与政策提取：独立的专业协作链

| 链路 | 主提取 | 条件升级 / 缺失处理 |
|---|---|---|
| **新闻 / 公告情绪提取** | 公告走 Qwen 公告角色，新闻走 Qwen 新闻角色 | 满足升级条件且 DeepSeek 可用时复核；方向冲突时置中性并降低置信度、标记冲突；失败返回规则提取与明确状态 |
| **政策 / 重大事件提取** | 文本公告 / 新闻走 Qwen；图像类别保留 MiMo 条件分支 | 重大调查、处罚、重组、违约等或低置信度 / 高强度触发条件复核；正文不足限制可提取字段，失败保留确定性回退与风险标记 |

这里的 DeepSeek 复核输出按各自事件提取 Schema 校验，不等同于通用风险 Handler 的 `approve / revise / reject`。MiMo 路由分支也不等于完整图片输入能力。

源码：[情绪提取器](../trading/research/sentiment/event_extractor.py)、[批量提取](../trading/research/sentiment/batch_extractor.py)、[政策提取器](../trading/research/policy_news/extractor.py)。

## 入口与核查方法

| 入口 | 用途与边界 |
|---|---|
| `GET /v1/roles` | 角色元数据及配置就绪标记；不能代替 Handler / 图像协议 / 连通性核查 |
| `GET /v1/routing/policy` | 不含凭证的通用风险 / 预算矩阵 |
| `POST /v1/invoke` | 当前通用 Handler 为新闻、研究综合、风险控制三个角色 |
| `POST /v1/pipelines/news-analysis` | 先构建材料包，再调用新闻角色，返回任务与调用关联 |
| `POST /v1/pipelines/announcement-verification` | 专用官方公告核验流水线 |
| Hermes / Finance Data MCP | 使用工具取得数据、扫描、研究与明确确认的决策评估；工具名单与边界见 [集成契约](hermes-integration.md) |

只读检查本机配置状态，无需发起付费模型请求：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/v1/roles
Invoke-RestMethod http://127.0.0.1:8765/v1/routing/policy
```

物理调用审计涵盖任务关联与独立研究请求的已知用量、耗时、成功 / 失败原因和未关联数量。核查时对照实际响应模型与任务 / 调用关联；用量缺失不能当作零消耗，离线通过也不能当作在线成功。见 [调用审计 API](api-reference.md#recent-model-calls)。

## 与早期版本如何对齐

| 早期版本 | 当时重点 | 本次介绍如何保留并更新 |
|---|---|---|
| [`341b621`](https://github.com/Shixi216/aiquant-lite/blob/341b621/README.zh-CN.md) | LongCat 新闻 + Qwen 公告；风险 / 预算矩阵；六个基础只读 MCP 工具 | 保留信息处理双职责与确定性路由思想；说明新闻已切换 Qwen、MCP 工具随阶段扩展 |
| [`d624571`](https://github.com/Shixi216/aiquant-lite/blob/d624571/README.zh-CN.md) | DeepSeek 高风险追加复核；MiMo Provider；视觉输入仍待完善 | 复原风险复核的四种状态和人工复核要求；维持视觉能力边界 |
| [`c1366aa`](https://github.com/Shixi216/aiquant-lite/blob/c1366aa/README.md) | AI + Quant、五维研究、风险 VETO、决策证据与本地模拟 | 把专业角色接回完整研究架构；以当前代码解释评分与角色实现，不照搬旧 60/40 为全局现状 |

**延续的是专业分工和证据约束，更新的是当前模型绑定、入口与实现状态。** 已有中文界面截图、研究数据盘点和作者自述继续保留在 [项目主页](../README.md)。
