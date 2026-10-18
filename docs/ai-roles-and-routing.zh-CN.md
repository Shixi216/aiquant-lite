# 当前模型、具体分工与运行契约

本页按 2026-10-19 本机有效配置、实际服务响应和修复后新构建的实跑结果撰写。模型名称是这次部署的选择；开源安装仍需自行配置凭证、模型与端点。

## 实际使用的三种模型

| 具体模型 | 运行位置 | 职责与输入 | 输出与已验证范围 |
|---|---|---|---|
| **`deepseek-v4-flash`** | Hermes 主模型；服务返回 `deepseek-flash` | 理解研究任务、规划专业角色的工作顺序；Hermes 安装自行管理主模型配置 | 原生 Agent 生成 4 步规划；本次未打开工具、记忆与私有上下文，不能据此宣称 MCP 自动全链通过 |
| **`qwen3.8-max-0902`** | Router 的 Qwen Provider | 新闻文本 / 元数据结构化、官方公告 PDF 核验、提供的五维证据摘要 | 三种任务均取得该模型的实际返回；分别校验事件结构、公告证据关联和研究 Schema |
| **`deepseek-v4-pro`** | Router 的 DeepSeek Provider | 接收原始请求与 Qwen 研究输出，独立检查证据、推断、缺失与风险 | 新构建的同任务高风险追加复核通过，返回 `revise`、发现与修正要求；使用 premium 预算 |

模型请求与返回标识分别记录，不把名称差异静默改写。Hermes 的主模型在其自身配置中选择；Router 的 `OPC_QWEN_MODEL`、`OPC_DEEPSEEK_MODEL` 等选择专业模型。角色表的 `preferred_model` 是元数据；实际请求由 Provider 设置决定。仓库 Qwen 默认值为 `qwen3.7-plus`，本次有效配置覆盖为 `qwen3.8-max-0902`。

来源：[模型配置](../router/config.py)、[角色注册](../router/registry.py)、[Qwen 请求](../router/providers/qwen.py)、[DeepSeek 请求](../router/providers/deepseek.py)、[实跑结果](model-validation-2026-10-19.json)。

## Qwen 的三个具体岗位

| 岗位 | 输入与工作 | 输出与边界 | 入口 |
|---|---|---|---|
| **新闻处理 `news_processor`** | 新闻 / 公告材料及来源元数据；提取时间、主体、事件、数值与单位，聚类重复报道 | `items / clusters / summary`；区分 `FACT / MEDIA_STATEMENT / SPECULATION`，缺失保留空值；Schema 失败可在预算内修复一次 | 通用 `/v1/invoke`；新闻流水线 `/v1/pipelines/news-analysis` |
| **公告核验 `announcement_verifier`** | 选择已持久化、已核验官方公告，下载 PDF、提取分页正文，核对待核验陈述 | 逐条 verdict、原文页码证据、时间性质、文档 / 文本哈希；正文不足不能靠标题补造内容 | 专用 `/v1/pipelines/announcement-verification`；没有通用 Handler |
| **研究综合 `research_synthesizer`** | 调用方提供的技术、基本面、情绪、政策 / 新闻、资金摘要；Handler 不自动搜齐全部市场数据 | `RESEARCH`、五维观察、缺失项、风险、置信度；校验证券代码一致性；不改算正式分数或套用旧权重 | 通用 `/v1/invoke` |

新闻岗位没有最终事实裁决权，公告核验需要实际正文证据，研究摘要的 Schema 通过也不等于每句推断都正确。本次 DeepSeek 复核实际指出了 Qwen 摘要的静态指标 / 交叉事件混淆。

源码：[新闻 Handler](../router/services/news_processor_handler.py)、[新闻 Schema 校验](../router/services/news_output.py)、[新闻流水线](../router/services/news_pipeline.py)、[公告流水线](../router/services/announcement_pipeline.py)、[公告核验](../router/services/announcement_verifier.py)、[研究 Handler](../router/services/research_synthesizer_handler.py)。

## DeepSeek Pro 如何复核

通用 Router 的 `high / critical` 请求在主结果返回后，若 DeepSeek 已配置且尚有物理调用额度，追加 `risk_controller`；风险角色自身不再递归追加复核。

输出为 `approve / revise / reject`、风险等级、发现、修正要求与置信度。四种执行状态为 `completed / unavailable / failed / budget_exhausted`，均不取消高风险人工复核要求。返回修改意见不会自动改写原文，也不是人工批准。

本次真实结果为 **`revise`**。Pro 指出：静态 DIF / DEA 关系不够证明刚发生上穿；资金维度存在量价数据，应为部分可用；财报期与情绪缓存需继续人工核查。复核意见也是模型输出，需要研究者结合原始材料检查。

修复前，即使选择 premium，追加复核仍被固定限制在 1024 Token，本次因此出现 `finish_reason=length`。修复后使用所选预算的单次输出上限；真实调用使用 8192 上限，记录 4811 个输出 Token，其中可能包含服务计入的推理 Token，不等同于最终可见文本长度。物理调用次数仍共用任务额度。

源码：[调用与追加复核](../router/services/invocation.py)、[风险 Handler](../router/services/risk_controller_handler.py)、[风险 Schema](../router/schemas/routing.py)。

## 风险与预算矩阵

适用于通用 `/v1/invoke`；专用公告、情绪和政策提取有各自契约。

| 预算 | 单次输出 Token 上限 | 任务物理调用上限 |
|---|---:|---:|
| `economy` | 512 | 1 |
| `standard` | 2048 | 4 |
| `premium` | 8192 | 8 |

| 风险 | 温度上限 | 最低预算 | 人工复核 |
|---|---:|---|---|
| `low` | 0.8 | `economy` | 策略不强制 |
| `medium` | 0.4 | `economy` | 策略不强制 |
| `high` | 0.2 | `standard` | 必须 |
| `critical` | 0 | `premium` | 必须 |

预算不兼容时先拒绝；首次调用、网络重试、结构修复与追加复核共享物理次数。小预算仍可能不足以完成复杂推理，此时保留失败状态与人工复核要求。本次仅验证 premium 下的真实追加复核，不推断其他档位也一定足够。

来源：[路由策略](../router/services/routing_policy.py)、[预算回归测试](../tests/test_routing_policy.py)。

## 其他模型与注册角色：按现状标注

| 模型 / 角色 | 本机现状 | 本次结果 |
|---|---|---|
| **MiMo `mimo-v2.5`** | 配置字段齐备，文本适配器存在 | 实际请求返回 HTTP 402；未收到有效模型结果，未验证图片识别 |
| `vision_reader / complex_vision` | 角色注册、MiMo Provider 与局部政策路由分支存在；共享一个配置模型 | 通用请求缺少图片载荷和 Handler；不能把配置就绪写成视觉功能已完成，也没有验证独立 Pro 视觉模型 |
| **LongCat `LongCat-2.0`** | 适配器存在，但本机未配置，现行新闻角色未绑定它 | 未调用，不作为当前新闻主模型 |
| **Hy3 `adversarial_reviewer`** | 角色禁用；无 Provider / Handler | 未调用，不作为已交付审查能力 |

源码：[Provider 注册](../router/services/provider_registry.py)、[Handler 注册](../router/services/role_handler_registry.py)、[MiMo 适配器](../router/providers/mimo.py)、[请求 Schema](../router/schemas/invocation.py)。

## 专业提取与确定性内核

情绪 / 政策提取当前按公告 / 新闻类别走 Qwen；符合重大事件、低置信度或高强度等条件且 DeepSeek 可用时，可走独立升级复核。这些分支本次没有重新全量运行，不能把已有缓存报告当作新调用证据。图像类别虽有 MiMo 条件分支，也不证明图像已传输和识别。

Data Hub 管理结构化事实；扫描、评分、数据时点、持仓约束和硬风险 VETO 由确定性模块负责。五维是研究证据分类，四维横截面评分与旧版 60/40 回退分别解释；模型不能自行切换评分路径。

源码：[情绪提取](../trading/research/sentiment/event_extractor.py)、[政策提取](../trading/research/policy_news/extractor.py)、[评分解析](../trading/decision_support/formal_score_resolver.py)、[架构](architecture.md)。

## 如何核对自己的部署

`GET /v1/roles` 返回角色与配置就绪标记，不能代替连通性 / Handler / 图片协议检查。`GET /v1/routing/policy` 返回通用预算矩阵。核对 [物理调用审计](api-reference.md#recent-model-calls) 中实际模型、任务关联、耗时、已知用量和失败状态；缺失用量不当作零消耗。

```powershell
Invoke-RestMethod http://127.0.0.1:8765/v1/roles
Invoke-RestMethod http://127.0.0.1:8765/v1/routing/policy
```

本次验证新补丁使用隔离 Router / 临时库；本机原常驻 Router 没有重启加载补丁。完整结果和前置条件见 [实跑审核](live-model-validation-2026-10-19.md)。
