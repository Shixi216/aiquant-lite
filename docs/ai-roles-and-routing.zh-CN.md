# 三个模型，协助你完成不同研究工作

AIQUANT-LITE 把问题理解、材料整理和重要结论复核分开，让量化结果更容易被使用者理解与核查。当前部署采用以下模型，使用者可按服务条件配置。

| 模型 | 分工 | 给使用者的帮助 |
|---|---|---|
| **DeepSeek `deepseek-v4-flash`** | Hermes 中的交互与任务组织；返回标识可能为 `deepseek-flash` | 理解问题、安排研究环节，把工具结果组织成可继续追问的对话 |
| **Qwen `qwen3.8-max-0902`** | 新闻处理、公告核验、五维研究综合 | 把材料整理为事件与证据，解释各维度观察，列出缺口 |
| **DeepSeek `deepseek-v4-pro`** | 重要模型输出的风险复核 | 检查证据、推断和状态矛盾，提出修改与补充核查要求 |

## 先看证据，再讨论结论

Qwen 的新闻角色保留事实、媒体表述与推测的区分；公告核验回到 PDF 正文与页码证据；研究综合整理调用方提供的五维材料。缺失不是让模型自由补全的邀请。

Pro 的复核也是模型意见，需要与原始材料一起检查。它可以指出研究摘要中不充分的推断，要求修正，而不是替使用者完成审批。已记录的一次案例中，Pro 指出了静态指标被写成上穿事件的问题。

## 让模型服务于策略

正式评分由量化模块计算，数据状态与硬风险规则独立约束建议。AI 负责阅读、解释与核查；新闻情绪不直接改写四维结构化情绪 rank。完整流程见 [研究策略手册](research-playbook.zh-CN.md)。

每次报告按实际入口组合能力，不强制运行所有模型。普通复用报告可以很快；在线取数、正文核验或 Pro 深度复核需要更多时间。[查看耗时口径](product-evidence-2026-10-19.md)。

## 需要接入时的接口

| 入口 | 用途 |
|---|---|
| Hermes / Finance Data MCP | 对话组织与已批准工具；契约见 [集成说明](hermes-integration.md) |
| `POST /v1/invoke` | 新闻、研究综合、风险三个通用 Handler |
| `POST /v1/pipelines/announcement-verification` | 官方公告专用核验 |
| `POST /v1/pipelines/news-analysis` | 构建材料包后执行新闻分析；与直接角色调用分别验证 |
| `GET /v1/roles` / `GET /v1/routing/policy` | 查看角色元数据与通用风险预算 |

通用 Router 先检查风险与预算；`high / critical` 在条件满足时追加 Pro 复核，人工复核要求保留。预算限制如下，首次调用、重试、结构修复与追加复核共享物理次数。

| 预算 | 单次输出 Token 上限 | 任务物理调用上限 |
|---|---:|---:|
| economy | 512 | 1 |
| standard | 2048 | 4 |
| premium | 8192 | 8 |

小预算可能不足以完成复杂推理，失败与缺失会保留状态。复核的 `approve / revise / reject` 是研究意见，不是交易委托；最终采纳和操作由使用者自己完成。

模型以本地 Provider 配置和实际响应为准，不把角色表偏好当作调用结果。原常驻服务与公开源码更新的加载范围分别记录，详见 [实跑审核](live-model-validation-2026-10-19.md) 和 [调用审计 API](api-reference.md#recent-model-calls)。

源码：[模型配置](../router/config.py)、[新闻处理](../router/services/news_processor_handler.py)、[公告核验](../router/services/announcement_verifier.py)、[研究综合](../router/services/research_synthesizer_handler.py)、[风险复核](../router/services/risk_controller_handler.py)、[预算策略](../router/services/routing_policy.py)。
