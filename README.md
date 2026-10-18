<div align="center">

![AIQUANT-LITE — Research deeper. Decide with evidence.](docs/assets/hero.svg)

# AIQUANT-LITE · 怡怡投研

### 为认真研究市场的人，打造自己的 AI 投研中枢。

**百万级数据沉淀 × 量化实验体系 × 多模型研究 × 可追溯决策**

从市场全景到个股深研，从因子试验到证据复盘。把专业投研的深度，带进你的本地工作台。

[![CI](https://github.com/Shixi216/aiquant-lite/actions/workflows/ci.yml/badge.svg)](https://github.com/Shixi216/aiquant-lite/actions/workflows/ci.yml) [![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](pyproject.toml) [![Vue](https://img.shields.io/badge/Vue-3-42B883?logo=vuedotjs&logoColor=white)](frontend/package.json) [![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](router/api/app.py) [![DuckDB](https://img.shields.io/badge/Data-DuckDB-FFF000?logo=duckdb&logoColor=black)](database/connection_manager.py) [![MCP](https://img.shields.io/badge/Agent-MCP-9AABF7)](docs/hermes-integration.md) [![License](https://img.shields.io/badge/License-MIT-6E7681)](LICENSE)

**[🚀 快速部署](#快速开始) · [🤖 AI 分工](#ai-如何分工具体模型具体任务真实调用) · [🖥️ 界面预览](#眼见为实投研工作台) · [🧠 研究证据](docs/knowledge/README.md) · [🏗️ 系统架构](#一套研究内核多种工作入口) · [💬 项目自述](#项目自述从一次比赛开始) · [English](README.en.md)**

</div>

---

## 让研究的每一步，都有据可查

AIQUANT-LITE 面向 **A 股研究者、量化开发者与小型研究团队**，把数据平台、量化研究台和 Agent 工作流汇入一套本地优先的投研系统。数据、实验、风险和判断在同一条研究链路中衔接，让一次研究留下下一次能够复用的资产。

![AIQUANT-LITE 六大能力](docs/assets/capability-map.svg)

| 研究深度 | 工程底座 |
|---|---|
| **🔎 多源事实，一处研究**<br>行情、财务、公告、新闻与风险信息保留来源、覆盖与时点，支持继续追问结论依据。 | **🧠 模型可配置，入口可组合**<br>Router 组织研究角色，Web、CLI、Hermes、MCP 与桌面源码复用研究基础设施。 |
| **🧪 策略有比较，复杂度有代价**<br>静态、动态、模型与消融路线接受成本、年度稳定性和 Walk-Forward 检验，失败方案一并留存。 | **⏱️ 时间进入研究契约**<br>PIT 截止、来源可得时间与历史成员质量分别约束输入；缺失与过期显式呈现。 |
| **🧾 判断可以回到证据**<br>版本化 DecisionPacket 固定区间、关联来源并校验完整性，支持人工检查和复盘。 | **🔐 本地数据，清晰所有权**<br>Router 是主 DuckDB 的在线唯一 Owner；研究侧车按各自只读契约提供结果。 |

## AI 如何分工：具体模型，具体任务，真实调用

**DeepSeek Flash 组织任务，Qwen 整理与核验证据，DeepSeek Pro 复核结论。** 数据取得、量化评分、风险 VETO 和决策证据由对应工程模块完成。下面展示 **2026-10-19 本机实际配置与实跑结果**；模型可按部署环境替换。

![当前模型分工与实跑结果](docs/assets/ai-role-map.zh-CN.svg)

| 工作环节 | 本次实际请求模型 | 具体做什么 | 实跑结果 |
|---|---|---|---|
| **Hermes 交互与任务组织** | **`deepseek-v4-flash`**<br>服务返回标识 `deepseek-flash` | 理解研究问题，安排新闻、公告、综合研究与风险复核职责 | 原生 Hermes 规划运行通过，生成 4 步分工；本次关闭工具与记忆，未执行 MCP 全链 |
| **新闻处理**<br>`news_processor` | **`qwen3.8-max-0902`** | 提取事件、主体与来源，区分事实 / 媒体表述 / 推测，整理重复材料 | 真实历史公告标题与元数据 → 1 条结构化事件，Schema 校验通过；未提供正文时保留缺口 |
| **公告核验**<br>`announcement_verifier` | **`qwen3.8-max-0902`** | 对照官方 PDF，核对主体、事实、页码证据与时间性质 | 2 条陈述均为 `SUPPORTED`；区分 `CURRENT_STATUS` 与 `FORWARD_LOOKING`，留存文档哈希 |
| **个股研究综合**<br>`research_synthesizer` | **`qwen3.8-max-0902`** | 汇总技术、基本面、情绪、政策 / 新闻、资金证据，列出缺失与风险 | 本地报告 → 5 维观察、6 个缺失项；保留行情基准与财报期，研究输出校验通过 |
| **风险控制与追加复核**<br>`risk_controller` | **`deepseek-v4-pro`** | 审查 Qwen 输出中的证据缺口、推断越界和状态矛盾，给出修改要求 | 同一任务内完成复核：`revise`、5 条发现、4 项修正要求；人工复核要求保留 |

### 这次实跑，模型协作解决了什么

Qwen 的摘要把 **DIF 大于 DEA 的静态关系写成了“上穿”事件**，还把具有部分量价数据的资金维度标为完全缺失。DeepSeek Pro 在复核中指出这两处问题，要求改为静态指标描述，并将资金状态改为 `PARTIAL`。

这体现了项目的价值：**研究摘要有独立复核，模型的说法有证据与状态约束，修正要求进入同一任务的审计链。** 本次返回的是待修正研究结果；复核意见不会自动改写原文，也不代表人工审批完成。

### 哪些能力本次没有跑通

- **MiMo `mimo-v2.5`**：文本适配器请求返回 **HTTP 402**；本次没有通过连通性检查，也没有执行图片识别。普通 / 复杂视觉角色尚缺通用图片输入与 Handler。
- **LongCat / Hy3**：LongCat 当前未配置、未绑定现行角色；Hy3 对抗审查仍禁用。两者不列入已验证模型组合。

**最终验收 6 项：5 项通过、1 项未通过。** 新闻 / 公告在现有服务上运行；修复后的研究与风险链在公开源码的新构建、隔离 Router 和临时数据库上运行。公告使用 2025 年历史材料；个股报告的行情基准为 2026-10-16、财报期为 2026-06-30，情绪来自缓存。这些样本不代表实时行情、长期可用率或投资绩效。

本次还修复了风险复核的固定 1024 Token 截断问题，以及研究摘要的证券代码传递和旧版权重提示问题；**17 项相关测试与 Ruff 通过**。本机原常驻 Router 尚未加载新补丁。

[查看具体模型、接口与预算规则](docs/ai-roles-and-routing.zh-CN.md) · [查看实跑审核与修复记录](docs/live-model-validation-2026-10-19.md) · [机器可读结果](docs/model-validation-2026-10-19.json)

## 眼见为实：投研工作台

**市场驾驶舱 → 机会排序 → 个股研究 → 风险检查 → 决策留存 → 模拟复盘**，让研究视图跟随工作流程展开。

![市场驾驶舱：覆盖、时点与市场结构](docs/assets/dashboard-live-20261019.jpg)

<table width="100%">
<tr>
<td width="50%" align="center"><strong>个股深研 · 事实与因子同屏</strong><br><img src="docs/assets/stock-overview-live-20261019.jpg" alt="个股研究总览真实界面" width="100%"></td>
<td width="50%" align="center"><strong>行情观察 · 价格与研究联动</strong><br><img src="docs/assets/stock-chart-live-20261019.jpg" alt="个股 K 线研究真实界面" width="100%"></td>
</tr>
</table>

<details>
<summary><strong>展开查看个股研究大图</strong></summary>

![个股研究全尺寸真实界面](docs/assets/stock-overview-live-20261019.jpg)

![个股 K 线全尺寸真实界面](docs/assets/stock-chart-live-20261019.jpg)

</details>

> 截图于 2026-10-19，来自生产构建的中文 WebUI 与本地已落库数据，未启用演示样本。行情日期、缺失项和过期提示按实际状态保留；截图不代表实时行情或策略收益。流程图展示概念架构。

<details>
<summary><strong>查看量化实验与策略研究界面</strong></summary>

![量化实验室真实界面](docs/assets/quant-live-20261019.jpg)

![策略研究真实界面](docs/assets/research-live-20261019.jpg)

</details>

### 六个工作区，贯穿研究全程

| 工作区 | 你可以做什么 | 实现入口 |
|---|---|---|
| **市场驾驶舱** | 看涨跌分布、数据覆盖、行情时点和来源状态 | [Dashboard](frontend/src/views/DashboardView.vue) |
| **机会排序** | 全市场确定性扫描、条件筛选、四维横截面 rank | [Scanner](trading/scanner) · [Cross Section](trading/research/orchestration/cross_section.py) |
| **个股研究** | 联看证券身份、财务、因子、决策区间和 K 线 | [Stock Intelligence](frontend/src/views/StockIntelView.vue) |
| **量化实验室** | 查看情绪、两融、龙虎榜与日线研究面板 | [Quant Lab](frontend/src/views/QuantLabView.vue) |
| **策略与资金研究** | 比较策略家族、成本敏感性和滚动验证结果 | [Strategy Lab](trading/research/strategy_lab) · [Research](webui/routes/research.py) |
| **决策与复盘** | 检查风险 VETO、仓位约束、版本化决策与本地模拟 | [Decision Support](trading/decision_support) · [Manual Tracking](manual_tracking) |

## 研究实力，落在可核对的积累上

![本地研究数据与实验规模](docs/assets/research-evidence.svg)

**2,782,220 行历史日线、2,625,560 个 Replay 特征包、2,238,764 条 Strategy Lab 对齐输入**，构成本地数据与实验积累。Strategy Lab 保留 **202 个候选定义、0 / 20 / 40 / 60bp 四档成本、144 行 Walk-Forward 指标**，让方案优劣接受统一协议检查。

项目同时沉淀 [8 个研究知识主题](docs/knowledge/README.md)：数据边界、PIT、实验协议、失败案例、故障治理、Hermes 工作流、Forward 观察和记忆治理。把“为什么有效、为什么不采用、下一步缺什么”一并留存。

> 统计来自 **2026-10-19 单个本地安装的只读盘点**。各表相互关联，不能相加；候选和指标行数不代表有效策略数。原始数据不随仓库分发。查看 [数据边界](docs/knowledge/data-assets.md)、[实验协议](docs/knowledge/experiment-comparison.md) 与 [机器可读证据摘要](docs/knowledge/evidence-snapshot-2026-10-19.json)。

## 从市场事实，到可复用的研究资产

![研究、决策与知识沉淀流程](docs/assets/research-workflow.svg)

1. **采集事实**：通过已配置数据源读取市场信息，标记来源、覆盖、业务日期和新鲜度。
2. **筛选与研究**：确定性计算组织候选；按配置调用模型角色，区分事实与推断。
3. **验证方案**：固定样本、标签和成本，对照简单基准、年度稳定性与滚动验证。
4. **留存证据**：相关决策流程记录版本、风险与证据，支持完整性核对。
5. **人工复盘**：检查模拟和观察结果，把经核查的方法与失败教训整理为知识。

不同 API 的边界分别定义；Hermes 的筛选结果需明确选择才进入决策评估，该集成工具不创建订单或 DecisionPacket。详见 [Hermes 契约](docs/hermes-integration.md) 与 [研究工作流](docs/knowledge/hermes-research-workflows.md)。

## 向成熟开源基础设施看齐

参考成熟项目的架构思想，聚焦 **A 股语境、日常研究体验和决策证据链**。

| 设计参照 | 值得学习的方向 | AIQUANT-LITE 的聚焦点 |
|---|---|---|
| **[Microsoft Qlib](https://github.com/microsoft/qlib)** | AI 量化研究、数据到模型的完整研究流程 | 把因子、实验和可读判断接进日常 A 股工作台 |
| **[OpenBB](https://github.com/openbq-org/OpenBB)** | 多源数据集成与面向分析师、量化和 Agent 的接口 | 组织 A 股来源状态，连接界面、API、Hermes 与 MCP |
| **[QuantConnect LEAN](https://github.com/QuantConnect/Lean)** | 模块化策略研究、回测与执行引擎 | 在研究与模拟范围内连接策略证据、风险约束和复盘 |

这些项目是设计参照；功能、数据覆盖、性能与投资收益没有被认定为等价。项目当前用于研究和人工决策支持，公开实现不接入券商、不自动提交实盘订单。

## 一套研究内核，多种工作入口

![研究工作台与服务架构](docs/assets/architecture-overview.svg)

前端采用 **Vue 3 + TypeScript + ECharts**，后端以 **FastAPI + DuckDB** 组织事实、研究与证据。WebUI 的主库事实与评分请求通过 Router；图表和研究产物可按契约读取只读侧车。数据源适配包括 Tushare、AKShare / Eastmoney、BaoStock、CNInfo；金牛历史数据为需独立配置的可选实验能力。

<details>
<summary><strong>展开工程细节：评分、风险与模型审计</strong></summary>

当前代码包含多条有明确来源的评分路径，不能混称为同一种分数：

| 路径 | 当前语义 |
|---|---|
| `cross_section_4d` | 技术 / 基本面 / 情绪特征 / 两融的横截面 rank，按 30 / 30 / 25 / 15 对可用项归一化；指定 cutoff 时读取不晚于该时点的历史截面 |
| `legacy_60_40_fallback` | 横截面不可用时回退技术 60% + 基本面 40%，显式标记 `degraded` |
| 编排实验接口 | 支持两因子、50 / 35 / 15 三因子及自定义权重研究；与上述横截面分数分开解释 |

动作还受数据状态、持仓上下文、分位数阈值与风险 VETO 约束。更多说明见 [架构文档](docs/architecture.md) 和 [当前限制](docs/limitations.md)。

模型角色及其服务凭证按本地配置选择；外部连通性需独立核查。物理调用审计覆盖任务与独立研究请求，记录已知用量、失败原因与未关联数量，错误脱敏显示。见 [调用审计 API](docs/api-reference.md#recent-model-calls)。离线维护主库前需停止持有主库的在线服务。

</details>

## 快速开始

需要 **Python 3.11**、[uv](https://docs.astral.sh/uv/)；构建 Web 工作台另需 **Node.js 22.12+** 与 npm。项目提供 Windows 运维脚本；其他系统可分别启动 Python 服务。

### 1. 安装与配置

```powershell
git clone https://github.com/Shixi216/aiquant-lite.git
cd aiquant-lite
uv sync --dev --frozen
Copy-Item .env.example .env
npm --prefix frontend ci
npm --prefix frontend run build
```

按需填写本地 `.env` 的数据与模型凭证。开源代码附带经过整理的研究摘要，不附带授权数据、个人数据库或完整实验结果集；完整研究链需要自行配置数据源并建立本地数据。

### 2. 启动工作台

Windows 上使用项目内脚本统一管理服务：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_hermes_opc.ps1
powershell -ExecutionPolicy Bypass -File scripts\status_hermes_opc.ps1
```

打开 [本地投研工作台](http://127.0.0.1:8767)。Router 默认端口为 `8765`，Data Hub 为 `8766`。首次使用可运行 `uv run python -m scripts.preflight_check` 查看配置与服务状态。

也可在不同终端分别启动：

```powershell
uv run python -m scripts.run_router_api
uv run python -m scripts.run_data_api
uv run python -m scripts.run_web_ui --host 127.0.0.1 --port 8767
```

### 3. 只看演示界面

完成前端构建后，可单独启动隔离的界面样本，无需数据凭证或启动 Router：

```powershell
$env:WEBUI_DEV_FIXTURES="1"
$env:HERMES_ENV="development"
$env:ROUTER_URL="http://127.0.0.1:9"
uv run python -m scripts.run_web_ui --host 127.0.0.1 --port 8877
```

访问 `http://127.0.0.1:8877`；市场总览、机会排序与示例个股页面显示契约样本，其余视图会按缺失数据降级。结束演示后在新终端启动正常服务，或清除以上环境变量。

## 质量验证

软件质量有公开记录：[源码 `53022f7` 的 Windows CI](https://github.com/Shixi216/aiquant-lite/actions/runs/39104295548) 留存 **后端 1794 passed、0 failed、4 skipped、8 deselected；前端 5 passed，生产构建通过**。当前分支检查见顶部 CI 标识。

```powershell
uv run ruff check .
uv run python -m scripts.run_offline_tests --workers 2
uv run python -m scripts.check_no_live_execution
npm --prefix frontend test
npm --prefix frontend run build
```

验证使用隔离环境和临时数据库。模块独立进程是已验证模式；审核 Windows / Python 3.11.1 环境的长时间混合进程原生崩溃尚未解决。跳过项、外部服务及本地数据的未验证范围见 [源码审核](docs/release-audit-2026-10-18.md) 和 [研究资料审核](docs/release-audit-2026-10-19.md)。软件测试不代替数据质量与长期策略绩效检验。

本次中文 WebUI 修复通过 **14 项前端回归、58 项相关后端测试与生产构建**，真实页面检查、截图来源及未解决的数据边界见 [WebUI 审核记录](docs/webui-review-2026-10-19.md)。

## 代码地图

```text
frontend/                  Vue 3 + TypeScript + ECharts 工作台
webui/                     GET-only 展示 API、图表链与研究产物读取
router/                    模型路由、研究编排、审计与统一 API
data_hub/                  行情、历史、财务、公告与市场数据
trading/scanner/            全市场扫描、查询与候选排序
trading/decision_support/   评分解析、风险、动作、仓位与决策包
trading/research/           因子、情绪、基本面与策略实验
trading/simulation/         本地模拟与组合研究
manual_tracking/           人工录入的外部成交事实
desktop/                   PySide6 桌面源码
database/migrations/       数据库结构演进
scripts/                   运维、研究与验证入口
tests/                     单元、接口、隔离与回归测试
docs/knowledge/            数据资产、研究协议、失败案例与知识治理
```

## 文档与路线

| 了解系统 | 使用与运维 | 深入研究 |
|---|---|---|
| [系统架构](docs/architecture.md) | [API](docs/api-reference.md) | [因子编排](docs/five-factor-orchestration.md) |
| [数据管线](docs/data-pipeline.md) | [运维手册](docs/operations.md) | [策略实验](docs/experiments.md) |
| [研究证据库](docs/knowledge/README.md) | [知识治理](docs/knowledge/memory-and-dialogue-governance.md) | [实验协议与失败案例](docs/knowledge/experiment-comparison.md) |
| [安全边界](docs/security.md) | [Hermes 集成](docs/hermes-integration.md) | [PIT 基本面](docs/fundamental-point-in-time.zh-CN.md) |

下一阶段聚焦：**数据质量度量、评分来源一致性、策略证据积累、研究可复现性与工作台交互完善**。规划内容不作为已经交付的能力。

## 项目自述：从一次比赛开始

我与股票的第一次真正接触，始于第十二届“东方财富杯”比赛。那次比赛为我打开了一扇门，让我从课堂上的金融概念，走进了充满变化的金融市场。

走进市场以后，我也遭遇过挫折，交过不少学费。经历这些之后，我越来越想弄清楚：一次判断究竟依据什么，哪些信息当时已经可得，结果出来以后，又该如何检验自己的想法。

我本身是金融科技专业，量化交易一直是一个让我着迷的主题。它把金融问题变成可以研究的假设，把模糊的直觉拆成数据、规则和实验，也让每一次得失都有机会成为下一次研究的起点。

AIQUANT-LITE 就是在这样的经历与兴趣中做出来的。我想为自己搭建一套投研工作台：把分散的行情、财务和市场信息组织起来，用量化方法检验想法，让 AI 协助梳理证据，再把判断、风险和复盘留下来。

因此，我很在意这个项目里的时间边界、数据来源、交易成本和失败记录。一个看起来漂亮的结果，还需要回答它是怎样得到的、适用于什么条件、还有哪些问题没有解决。这些要求，也是在回应我曾经交过的学费。

市场的复杂与不确定性始终存在。我希望这个项目能让我研究得更扎实，判断得更有依据，也更诚实地面对自己的局限。代码、数据和模型，是我持续学习金融市场的工具。

这个项目既是一套投研工具，也是我作为金融科技专业的学习者，探索量化交易的一份实践记录。如果你也对金融市场、数据和技术之间的联系着迷，欢迎一起完善它，把问题问清楚，把证据留下来。

—— AIQUANT-LITE 作者

[独立阅读这篇自述](docs/project-story.zh-CN.md)

## 参与建设

如果你关注 A 股数据质量、量化验证、Agent 工具或研究工作台，欢迎从一个明确问题开始：复现缺陷、补充数据语义、改进验证或完善交互。先阅读 [贡献指南](CONTRIBUTING.md)，通过 [Issues](https://github.com/Shixi216/aiquant-lite/issues) 提交可复核的问题与建议。

喜欢这个方向，欢迎 Star，让更多认真做研究的人看到这个项目。

## 安全与许可

凭证只放在本地 `.env`；数据库、日志、缓存、运行状态和个人研究产物不进入公开仓库。服务默认绑定本机，远程使用需自行配置认证与网络控制。漏洞反馈见 [SECURITY.md](SECURITY.md)，贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)。

本项目用于研究、教育、回测、模拟与人工决策支持，不构成投资建议，不承诺收益。第三方数据与模型的使用须遵守相应服务条款。源码采用 [MIT License](LICENSE)。
