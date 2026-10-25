# AIQUANT-LITE 前端接管与 WebUI 升级交付报告

> 交付时间：2026-10-25 · 执行：ZCode（前端负责人接管）
> 基线：`33ce95c`（WorkBuddy 交接检查点，位于 `sentiment-v3-recovery-strategy-lab` 分支）
> 独立备份：`backups/frontend-20261025/`（97 个源文件，含 node_modules/dist 除外全量）
> 本轮全部工作仅在前端；后端 Python/数据库零改动（后端由另一 ZCode 窗口负责，其 v3.2 在途变更仅做只读对接）。

---

## 一、交付总览

| 阶段 | 内容 | 状态 |
|---|---|---|
| 接管安全 | 暂存区核查（空）→ 密钥排查（无）→ 独立备份 → 检查点提交（不混入后端窗口未提交文件） | ✅ |
| 视觉升级 | 全站从深色量化终端升级为「晨雾青绿」清新浅色工作台，深色主题完整保留可随时切回 | ✅ |
| 主题定制中心 | 10 项定制能力 + 4 套主题预设 + 导入/导出，CSS Variables + Design Tokens，零新增依赖 | ✅ |
| 任务中心 | 「数据与研究任务中心」新页面：状态面板/数据操作/研究操作/任务管理；对接后端 v3.2 jobs 契约（提交→job_id→真实轮询→终态），未交付能力显示「暂不可用」 | ✅ 实测闭环 |
| v3.2 对接 | time_semantics 三日期、逐调仓 PIT 审计、quality 服务端筛选、suggested_start_date | ✅ 实测通过 |
| 验收 | vue-tsc 0 错误 · vite build 成功 · 19/19 单测 · 12 路由走查零意外错误 · 双主题切换实测 · 真实 API 闭环实测 | ✅ |

**依赖决策**：未引入 shadcn-vue/Vben/PrimeVue 等任何运行时依赖（保持 Apache-2.0、零版本兼容风险），仅借鉴其语义化 token 与 preferences 模式自研实现。package.json 零变化。

## 二、修改文件清单（自检查点 33ce95c 起）

**新增（10）**
- `src/styles/tokens.css` — Design Tokens：4 预设 × 2 模式（8 组色板）+ 结构/密度/阴影 tokens；保留 --bg/--bg-panel 等旧变量名，存量组件零改动换肤
- `src/domain/theme.ts` — 主题纯函数层：类型、预设/色盘/仪表盘组件注册表、localStorage 持久化、`applyTheme`（挂载前调用防闪烁）、`normalizeSettings`（导入校验）
- `src/domain/chartTheme.ts` — ECharts 取色层：`useChartTheme()` 响应式 token（读 CSS 变量，与实际渲染严格一致）+ `withAlpha`
- `src/stores/theme.ts` — Pinia 主题 store：设置/持久化/chartVersion 重绘信号/导入导出/重置
- `src/views/SettingsView.vue` — 外观与布局设置页（/settings）
- `src/views/TaskCenterView.vue` — 数据与研究任务中心（/task-center）
- `src/api/taskCenter.ts` — 任务中心唯一 HTTP 出口：能力注册表、契约化 payload 构造、405 诚实解释、任务留痕模型
- `tests/theme-tokens.test.mjs` — 主题架构守卫测试（tokens 完整性/无硬编码 hex 回归）
- `docs/screenshots-20261025/*.png` — 7 张验收截图（见第六节）
- `UI_UPGRADE_REPORT_20261025.md` — 本报告

**修改（34）**
- 骨架：`main.ts`（挂载前落主题）、`style.css`（全量 token 化重写 + 顶部导航/折叠样式）、`layouts/MainLayout.vue`（工作区化导航 + 主题快捷菜单）、`router/index.ts`（+ /settings、/task-center，旧路由全部保留）
- 图表：`charts.ts`（注册 wl-light/wl-dark 主题）、`composables/useECharts.ts`（按主题构建实例、chartVersion 变化重建并恢复 option，保留 null 守卫）
- v3 对接：`types/wealthLabV3.ts`（TimeSemantics/RebalanceRecord/v3.2 兼容字段）、`api/wealthLabV3.ts`（quality status/q 参数、V3BacktestRejectedError）
- 页面：`DashboardView`（组件显示/排列定制）、`WealthUniverseView`（服务端筛选）、`WealthStrategyFactoryView`（建议起始日采用并重跑、逐调仓记录合并）、`StrategyBacktestResult`（时间语义条 + 逐调仓表 + 合格数 step 折线 + v3.1/v3.2 双形状兼容）
- 取色迁移（硬编码色→token/chartTheme）：`BacktestCenter`、`AllocationCenter`、`StressTestCenter`、`WealthFactorsView`、`WealthStrategyCompareView`、`SentimentPanel`、`StrategyLabPanel`、`TransitionSankey/Heatmap`、`CandidateComparison`、`GroupReturnsChart`、`SymbolSeriesModal`、`KlineChart`、`BreadthChart`、`BreadthTrendChart`、`MarginTrendChart`、`CapitalBehaviorView`、`WealthLabView`、`WealthAiRiskView`、`SentimentGauge`、`WlDateInput`、`WealthDashboard`

## 三、主题预设与定制能力

| 预设 | 模式 | 主色 | 说明 |
|---|---|---|---|
| 晨雾青绿（默认） | light / dark | #0f9b84 青绿 | 珍珠白背景 · 薄荷卡片 · 清新专业 |
| 云朵浅蓝 | light / dark | #2f7ed8 蔚蓝 | 柔和浅灰蓝 · 清爽冷静 |
| 奶油杏金 | light / dark | #b97f24 杏金 | 米白暖底 · 温润亲和 |
| 极夜专业版 | dark（原深色终端完整保留）/ light 对应变体 | #4da3ff | 原有量化终端配色 1:1 保留 |

十项定制（/settings + 侧栏快捷菜单均可）：①浅色/深色模式 ②主色/辅助色 ③页面背景 ④卡片圆角 ⑤阴影强度 ⑥字号 ⑦密度（紧凑/舒适/宽松）⑧导航布局（左侧/顶部）+ 侧栏宽度/折叠 ⑨ECharts 色盘（跟随主题/青绿商务/蔚蓝金融/极夜霓虹）⑩仪表盘组件显示与排列。主题 JSON 导入/导出（下载/剪贴板/文件）。设置仅存浏览器 localStorage（`wl-theme-settings-v1`），不含任何金融数据或凭据。

主题变化联动范围：页面背景、卡片、表格、按钮、徽章、弹窗、侧边栏、滚动条、**全部 ECharts 图表**（实例重建 + option 内取色重算）与 SVG 仪表描边。

## 四、按钮与 API 对应表（任务中心）

> 更新：后端窗口在交付过程中已落地 **v3.2 任务控制中心**（`6e6e2d3`：`trading/wealth_lab/jobs.py` + `routes_jobs.py`，`/v1/wealth-lab/v3/jobs/*`）。前端已即时对接：8 个"暂不可用"按钮全部升级为**真实 job 提交 + 轮询**，并新增「后端任务队列」面板（真实列表/进度条/结果/日志/取消）。

| 按钮 | 执行方式 | 出口 | 状态 | 说明 |
|---|---|---|---|---|
| 更新基金名录 | job `update_fund_basic` | jobs 队列(8765) | ✅已接（待实测） | 拉官方 fund_basic 核对元数据 |
| 增量更新 ETF 行情 | job `update_market_data` | jobs 队列 | ✅已接 · **实测执行**（触达后端 bug，见下） | 按 data_last 增量抓取，可指定资产/区间 |
| 更新复权因子 | job `update_adj_factors` | jobs 队列 | ✅已接 | fund_adj 随行情强校验，缺失即失败 |
| 运行质量检查 | job `run_quality_check` | jobs 队列 | ✅已接 · **实测 SUCCEEDED** | 重算并写回质量状态（实测 24✓/9 降级） |
| 重新评估历史可投资池 | job `rebuild_pit` | jobs 队列 | ✅已接 | 全池 × 12 月初时点重评 |
| 启动策略回测（后台） | job `run_backtest` | jobs 队列 | ✅已接 | result 附 backtest_id/time_semantics/metrics |
| 触发 AI 研究（付费） | job `run_ai_research` | jobs 队列 | ✅已接（须 confirm=true，前端有确认对话框） | 依赖真实回测 ID |
| 生成研究报告 | job `generate_report` | jobs 队列 | ✅已接 | 从存档回测重放 |
| 运行财富策略回测（即时） | POST `/wealth-lab-api/v1/wealth-lab/v2/strategies/{id}/backtest` | 财富后端(8765) | ✅ **实测 SUCCEEDED**，`backtest_id` 留痕 | 同步链路 |
| 同步 A 股研究名录 / 抓取全市场快照 / 增量补充日线(8767) / 候选资产 enrichment / 新闻分析流水线 / 公告核验流水线 | POST `/api/v1/...` | Router 门面(8767) | ✅已接 · 实测 405 | 8767 为只读门面，前端如实显示"本次未产生任何数据变更" |

**实测发现的后端缺陷（本窗口不修改后端代码，移交后端窗口）**：`job_handlers.py::_update_market_data` 的来源合并行
`"/".join(sorted(set(...)) - {""})` 为 `list - set`（`sorted()` 返回 list），触发 `TypeError: unsupported operand type(s) for -: 'list' and 'set'`，导致 `update_market_data`/`update_adj_factors` 任务在首只资产写入后失败（job `wljob_8b9a052ccee6`，进度 5%）。前端已如实把该错误透出到任务记录与队列面板。

## 五、真实联调证据（2026-10-24/25 实测）

1. **用户指定验收场景（job 闭环）**：任务中心点击「增量更新 ETF 行情」（510300.SH）→ 确认对话框展示请求体 → 提交 → job_id `wljob_8b9a052ccee6` 出现在本地记录与后端队列 → 真实进度轮询（5%）→ 后端执行失败 → 前端如实展示后端错误（该错误为后端窗口代码缺陷，已移交；见第四节）。成功路径由「运行质量检查」完整验证。
2. **job 成功闭环（run_quality_check）**：UI 提交 → `wljob_10a733eb93cb` → 排队 → 运行 → SUCCEEDED 100%（3.1s）→ 结果 `{quality_counts:{QUALIFIED:24,DEGRADED:9},total:33}` 真实写回质量库；「后端任务队列」面板同步显示两条成功任务（UI 提交 + API 提交）的进度条与结果 JSON。截图 `08-task-center-jobs-queue-real-loop.png`。
3. **任务闭环（同步成功路径）**：点击「运行财富策略回测」（S2_cross_momentum）→ 真实 POST → `SUCCEEDED`，耗时 0.3s，后端引用 ID `wlbt_484fa7981627`，详情展开可见完整请求体与响应（`request_id: req_45dd2bba…`、`data_mode: REAL`、策略参数 lookback_days=126/top_k=2）。截图 `04-task-center-real-run-record.png`。
4. **任务闭环（诚实失败路径）**：点击「抓取全市场快照」→ 真实 POST → 405 → 任务记录 FAILED + 明确原因"后端当前部署为只读可视化服务（GET-only 代理）…本次未产生任何数据变更"。
5. **PIT 拒绝 → 建议起始日 → 重跑成功**：策略工厂选 multi-asset-v1（29 只）+ 2020-04-08 起点回测 → 200+FAILED（PIT 闸门）→ 页面展示 `suggested_start_date: 2021-02-04`（交接单遗留 #2 在实测后端已交付）→ 点击「采用并重跑」→ SUCCEEDED：610 交易日、累计收益 +20.84%。
6. **时间语义三日期**：结果区显示 请求起点 2021-02-04 / 实际首日 2024-04-15 / 首次建仓 2024-04-15 / 实际交易日 610 + 顺延原因（多资产池行情窗口自 2024 起的诚实展示）。
7. **逐调仓 PIT 审计**：31 次调仓全量记录（决策日期/决策数据截止/PIT 合格数/实际入选/现金仓位/成本/目标权重/被排除原因），合格资产数 step 折线图；交接单 #1 的"仅首末快照"已升级。截图 `06-factory-pit-rebalance-audit.png`。
8. **服务端筛选**：研究池页选择「数据降级」→ 显示"第 1–4 条 / 共 4 只 · 服务端筛选（status/q）"（v3.2 status 参数真实生效；q 关键字同链路）。截图 `07-universe-server-side-filter.png`。
9. **主题切换**：晨雾青绿 → 极夜专业版 → 晨雾青绿 往返实测，`<html>` dataset 与 --accent 计算值断言正确（#0f9b84 ↔ #4da3ff），图表/表格/侧栏同步换肤。截图 01/02/03。
10. **状态面板（真实数据）**：最近行情日期 2026-10-23（wealth 数据池）、研究资产 33、A股名录 5871 条（版本 universe_7b642449a139b438）、质量 29✓/4 降级、快照"陈旧"语义、Router ok、待处理任务数来自后端队列真实统计。
11. **路由走查**：12 条路由全部渲染正确 h1、零 pageerror（/capital-behavior 的"暂无对应数据"为后端 404 如实展示，属既有行为）。
12. **静态验收**：`vue-tsc --noEmit` 0 错误；`vite build` 成功；`node --test` 19/19（含新增主题架构守卫测试：tokens 完整性、style.css/组件 style 块无硬编码 hex 回归）。

**兼容性确认**：v1 四区（驾驶舱/配置/回测/压力测试）、v2 因子/对比/AI 风控、WlDateInput（update:modelValue 未动）、信封取数（envelope.data）、200+FAILED 不降级、离线 Mock 徽章——全部保持；Pinia `wealthLabV2` 回测上下文（backtestId/strategyId/universeId）继续写入。

## 六、浏览器截图（frontend/docs/screenshots-20261025/）

1. `01-dashboard-mist-jade-light.png` — 市场总览 · 晨雾青绿（真实行情数据）
2. `02-dashboard-polar-night-dark.png` — 极夜专业版切换实测（主题菜单展开）
3. `03-settings-theme-center.png` — 主题定制中心
4. `04-task-center-real-run-record.png` — 任务中心 · 真实回测留痕（请求/响应展开）
5. `05-strategy-factory-form.png` — 策略工厂（工作台浅色）
6. `06-factory-pit-rebalance-audit.png` — 逐调仓 PIT 审计（三日期 + 31 条记录 + 折线）
7. `07-universe-server-side-filter.png` — 研究池服务端筛选实测
8. `08-task-center-jobs-queue-real-loop.png` — 后端任务队列 + 本地留痕（job 成功/失败全证据链）
9. `09-ai-research-workspace.png` — AI 智能研究工作台（v3.3）
10. `10-ai-research-roles-and-error.png` — 模型角色面板与真实失败提示
11. `11-sentiment-workspace.png` — 多资产市场情绪工作台（市场卡/类别/33 资产/任务条）
12. `12-market-data-regression-fixed.png` — 行情增量更新回归：昨日 FAILED vs 今日 SUCCEEDED 同框
13. `13-navy-dashboard-1920.png` — 深海军专业版默认主题（1920）
14. `14-sentiment-zh-1920.png` — 情绪页全面中文化（1920）
15. `15-ai-research-zh.png` — AI 研究页中文化（稳定研究/实验性研究/研究员角色）
16. `16-task-center-zh-1280.png` — 任务中心中文化（1280 宽，无溢出）
17. `17-sentiment-navy-thememenu.png` — 深海军主题下的情绪页与五预设主题菜单
18. `18-mist-jade-restored-pixel-icons.png` — 晨雾青绿恢复 + 像素图标导航与主题控件
19. `19-sentiment-viz-icons-tint.png` — 情绪页三组图表 + 八色图标侧栏
20. `20-universe-quality-viz.png` — 研究池树图与质量柱状（真实审计）
21. `21-compare-scatter.png` — 策略风险收益散点（真实比较结果）
22. `22-sentiment-viz-dark.png` — 极夜主题图表同步重绘

## 七、待后端补充/确认的接口

| # | 事项 | 说明 |
|---|---|---|
| 1 | **后端 bug：`update_market_data` 来源合并** | `job_handlers.py` 中 `sorted(set(...)) - {""}` 为 list-set 运算，触发 TypeError；请后端窗口修复后，前端「增量更新 ETF 行情」「更新复权因子」即可完整跑通（前端无需改动） |
| 2 | jobs 契约升版补录 | `/v1/wealth-lab/v3/jobs/*`（提交/列表/详情/日志/取消）请补录进 `docs/contracts/wealth-lab-api-v3.yaml`，前端按实际响应实现（含 `duplicated`/`whitelist` 字段） |
| 3 | pipeline 契约对齐 | 8765 运行实例要求 `claims` 字段，仓库 `router/schemas/*` 无此字段——请确认线上版本与 schema 演进 |
| 4 | `rebalance_records` 位置统一 | 当前返回顶层（`data.rebalance_records`）而非 `pit_rebalance_snapshots.rebalance_records`，前端已双兼容；建议契约固定一种并同步 v3.1/v3.2 全部增量字段 |
| 5 | 首末快照结构统一 | `first/last` 已变为 RebalanceRecord 同构（`excluded_reasons`），前端已双兼容；同上建议契约固定 |
| 6 | 多资产池行情窗口声明 | multi-asset-v1 实际行情自 2024-04 起，`requested vs effective` 差异较大；建议在 universes 端点透出 `data_first` 以便前端预提示 |
| 7 | 8767 只读门面的写链路 | `/v1/universe/sync`、`/v1/market-snapshots/sync`、`/v1/data-expansion/run`、`/v1/candidates/enrich`、`/v1/pipelines/*` 六个前端已接按钮，在门面开放 POST（或迁移到 jobs 白名单）后即可用，无需前端改动 |

## 八、v3.3 追加：AI 智能研究工作台（2026-10-25 第二轮）

**新工作区「🧠 AI 研究」→ /ai-research**，对接后端 v3.3 三模型受控研究契约（`96cfa33`/`723000f`，`/v1/wealth-lab/v3/research/*`）。零新增依赖；清新主题与自由定制系统完全沿用。

**新增文件**：`src/types/aiResearch.ts`（1:1 契约镜像：ResearchPlan/角色/审计/决策包）、`src/api/aiResearch.ts`（唯一出口：roles/stable/experimental/approve/run 详情/决策包，长同步超时 780s）、`src/views/AiResearchView.vue`；修改 `router/index.ts` 与 `MainLayout.vue`（第五工作区）。

**功能对照验收单**：
| 验收项 | 实现 |
|---|---|
| 自然语言目标 + 资产池/区间/风险偏好/运行模式/预算 | 目标输入框（4–500 字）；资产池/区间在实验模式由 AI 规划并经白名单校验（契约即如此，不做前端预设）；模式 seg 默认 STABLE_RESEARCH，实验模式由后端 flag 控制（未启用禁用+403 说明）；预算只读显示后端契约值（STABLE≤2 / EXPERIMENTAL≤3），不自创参数 |
| 十步流程 | 十步步骤条 1:1 映射后端真实 stage（PLANNING/VALIDATION/APPROVAL_PENDING/BACKTESTING/HARD_RISK_CHECK/RESEARCH/REVIEW/VERIFICATION/COMPLETED/FAILED）；approve 期间每 2.5s 轮询 `GET /runs/{id}` 展示真实阶段；**无任何模拟进度**（同步 POST 在途时明确显示"真实请求在途"） |
| 用户控制 | 查看方案（plan 全字段）、确认按钮（显示剩余调用预算）、查看模型调用次数（调用审计表：角色/模型/延迟/tokens/状态/错误）、实验对比表（每组真实 backtest_id）、风险否决原因、数值核验状态、决策包详情（/v2/decision-packets/{id}）按需加载、run_id 历史查询（localStorage 记忆最近一次） |
| 取消 | v3.3 契约未提供 research run 取消端点 → 按钮禁用并如实标注"后端总超时 720s 兜底"（不伪造可取消） |
| 失败/超预算/模型不可用 | 后端 error_reason / HTTP 错误原样透出；VETOED 与 VERIFICATION_FAILED 有专门红色说明（硬风控不可被 AI 解除） |
| 演示标记 | 后端已交付，无演示壳；仅当后端 flag 关闭时实验模式按钮禁用并注明原因 |

**实测结果（2026-10-25，全部真实调用）**：
1. 页面/工作区/角色面板就绪：三模型（Qwen 3.8 Max 0902、DeepSeek V4 Pro、DeepSeek V4 Flash）全部"可用"（来自真实 roles 端点），实验模式 flag 已开。截图 `09/10`。
2. **实验模式规划 → 真实失败**：提交目标后 POST 在途约 60s，后端返回 `Deepseek API network error: ConnectError`；前端诚实透出错误、回到可重试状态、无假进度。重试一次同样失败。
3. **稳定模式执行 → 真实失败于复核阶段**：以真实 `wlbt_484fa7981627` + confirm_paid_calls 发起，后端真实执行约 78s（硬风控与 Qwen 研究阶段通过），在 DeepSeek Pro 复核处 ConnectError 失败；前端如实展示错误。
4. **根因（环境级，非代码）**：本机直连 `api.deepseek.com` 超时（curl 000），上游网络不可达（疑似需代理）。**成功路径的前端部分已全部就绪**——上游恢复后直接在页面重跑即可完成"规划→确认→实验→结论→复核→决策包"全链路验收，前端无需任何改动。
5. vue-tsc 0 错误 · vite build 通过 · 19/19 单测。

**待后端/环境**：① DeepSeek 上游连通性（代理配置）恢复后完成成功路径验收；② 建议补 research run 列表端点（当前仅能按 run_id 查询）与取消端点；③ 契约 yaml 补录 `/v3/research/*` 与决策包关联字段。

## 十、第三轮：多资产市场情绪工作区（wealth-sentiment/v1.1）

**新工作区「📡 市场情绪」→ /wealth-sentiment**，1:1 对接 `trading/wealth_lab/sentiment/CONTRACT.md`（v1.1）与 v3.4 jobs 白名单新增的 3 个情绪操作。

**新增文件**：`src/types/sentiment.ts`、`src/api/sentiment.ts`（读端点 + 三写任务；写任务复用 v3 jobs 轮询）、`src/views/WealthSentimentView.vue`；修改 `router/index.ts`、`MainLayout.vue`（第六工作区）、`AiResearchView.vue`（执行中显示真实轮询时间戳与"可离开本页按 run_id 恢复"——非阻塞强化）。

**功能对照验收单**：
| 验收项 | 实现 |
|---|---|
| 市场总体情绪 | 大卡：分数（涨跌配色）、置信度、证据口径徽章、直接/间接/观测/来源计数、版本与快照时间、方向分布、证据下钻按钮 |
| 八大类别情绪 | 前端按后端资产评分聚合（**明确标注"后端无独立类别评分，仅供分化概览"**）；实际渲染 9 类（v3.1 新增 QDII_LOF），如实展示 |
| 33 只资产情绪 | 逐只调用后端 `/assets/{symbol}/score`；分数/置信/口径/直接·间接/观测/最新证据 |
| 证据不足 | `INSUFFICIENT_DATA` → 灰显"证据不足"；`CLASS_LEVEL_EVIDENCE` → "间接推导"徽章（置信封顶 0.6）；`direct_evidence_count=0` 不作强信号展示 |
| 直接 vs 类别关联证据 | 资产表分列展示 direct/class 计数；事件 `asset_links` 按分级渲染（直接点名=绿、其它=黄） |
| 新闻与事件列表 | 事件表（时间/标题/方向/分析状态/关联分级/新闻来源数），支持按代码/类别/状态过滤；资产行展开显示该资产资讯（含**原文链接**与逐条关联分级）；MERGED 显示归并指向 |
| 评分审计 | `/scores/{id}/evidence` 下钻：贡献事件、分析记录、validation_flags 校验标记、完整审计 JSON |
| 三个写按钮 | 更新资讯/重新分析（付费需确认）/重新计算情绪分数 → `POST /v1/wealth-lab/sentiment/{refresh,analyze,rebuild-factor}`（内部走 Jobs API）→ job_id → 内联真实轮询（3s）→ 终态后自动刷新情绪数据 |

**实测（全部真实）**：市场 +17.3（直接证据口径，53 条观测）；33/33 资产评分加载（债券 +34.1/REITs +67.0 等，全部正确标注间接推导与 60% 置信封顶）；「重新计算情绪分数」UI 提交 → `wljob_7f9debdd9d16` → 排队 → 20% → **成功 100%**（结果含 389 条重关联、373 事件归并的真实维护审计）。截图 `11`。

**开发期修复**：资产评分批量加载曾全量失败——根因是前端 import 遗漏（`fetchAssetScore` 未导入）叠加后端并发连接的瞬态锁竞争；已修复导入、错误信息透出真实原因、并按任务中心既有先例增加一次静默重试。经验教训：此前 vue-tsc 输出被管道截断掩盖了真实类型错误，本轮起类型检查不再截断输出。

**控制中心遗留问题回归**：后端 `723000f` 修复 `_update_market_data` 的 list-set TypeError 后，重跑「增量更新 ETF 行情」→ `wljob_64074752707c` **SUCCEEDED 100%**（rows=0 为正确语义：周末无新增交易日；来源审计 fund_daily/fund_adj 均 OFFICIAL）。任务记录中昨日 FAILED 与今日 SUCCEEDED 同框对照（截图 `12`）。8767 只读门面的 6 个按钮维持如实失败展示，未绕过。



## 十一、第四轮：中文化专项 + 深海军专业主题 + 全站精修

**设计规范升级**：新增「深海军专业版」预设并设为默认（深海军蓝侧栏 + 白/浅灰内容区 + 克制蓝 #2f6fed 强调；参考 art-design-pro / vben-admin 的专业后台配色结构）。侧栏文字改为独立 token（--sidebar-text 系列），深色侧栏预设可配浅色文字；其余四套主题全部保留可切换（含深浅模式与全部定制能力）。按钮体系补齐按压态（:active）、禁用悬停抑制（:hover:not(:disabled)）与统一行高。

**技术字段中文化（对照）**：

| 位置 | 修改前 | 修改后 |
|---|---|---|
| 任务中心操作卡徽章 | `POST /v1/universe/sync`、`8765 可写` | 「即时接口 · 只读网关」「受控任务 · 真实执行」（技术路径移入悬浮提示，保留审计） |
| 任务状态 | `SUCCEEDED` / `FAILED` / `RUNNING` | 执行成功 / 执行失败 / 执行中 |
| 任务描述 | `POST /v1/data-expansion/run（expansion_type=DAILY_BARS）…` | 纯中文业务说明 |
| 状态面板 | `Router：ok`、`版本 universe_7b64…` | 路由网关：正常 · 已按版本登记 |
| 请求/响应详情 | 「请求体/响应」裸 JSON | 「请求参数（技术审计）/后端响应原文（审计留痕）」 |
| AI 研究模式按钮 | `STABLE_RESEARCH` / `EXPERIMENTAL_AI_RESEARCH` | 稳定研究（推荐）/ 实验性研究（英文枚举移入悬浮提示） |
| AI 研究状态徽章 | `COMPLETED` / `VETOED`… | 研究完成 / 风险否决（VETO）…（labels.ts 统一词典） |
| 模型角色 | `RESEARCHER` | 研究员 RESEARCHER（中文职责为主） |
| AI 参数名 | `allow_reuse：…`、`confirm_paid_calls：…` | 中文语义（对应后端参数名移入悬浮提示） |
| 情绪方向/状态 | `POSITIVE` / `ANALYZED` / `MERGED` | 利好 / 利空 / 已分析 / 重复报道（已归并）… |
| 情绪类别 | `MACRO_DATA` / `CN_BOND_ETF` | 宏观数据 / 债券 ETF（新增 v3 九类中文映射） |
| 校验标记 | `cleared_symbols_on_insufficient` | 证据不足，已清除资产断言（悬浮保留原文） |
| 契约徽章 | `契约 wealth-lab/v2` | 接口版本 v2（悬浮保留完整契约名） |
| `Jobs API` 措辞 | 受控更新操作（Jobs API） | 受控更新操作（后端任务队列） |

集中词典落在 `src/domain/labels.ts`（延续"唯一中文语义层"约定）：RESEARCH_STATUS / RESEARCH_ROLE / RESEARCH_MODE / JOB_STATUS_ZH / SENTIMENT_DIRECTION / SENTIMENT_ANALYSIS_STATUS / NEWS_CATEGORY / ASSET_CATEGORY_LABELS_V3 / VALIDATION_FLAG 九组映射 + 全函数。

**浏览器回归（真实操作）**：
- 18 条路由全部渲染正确中文标题、零横向溢出（1920/1440/1280/1024 四档抽验）。
- 主题：清除缓存后默认即深海军专业版；UI 菜单晨雾青绿 ↔ 深海军往返切换正常（bg 计算值断言）。
- 中文化断言：情绪页无 POSITIVE/ANALYZED/MACRO_DATA 残留；AI 页无 STABLE_RESEARCH/EXPERIMENTAL_AI_RESEARCH 裸露；任务中心徽章/状态/网关文案全部中文。
- /capital-behavior「暂无对应数据」为后端 404 如实展示（既有行为，非本轮引入）。
- vue-tsc 0 错误 · vite build 通过 · 19/19 单测。

**截图**：13 深海军总览（1920）、14 情绪页中文化（1920）、15 AI 研究中文化、16 任务中心（1280）、17 情绪页深海军+主题菜单。



## 十二、第五轮：恢复明亮默认主题 + 像素风图标（pixelarticons, MIT）

**主题恢复（精确回退 e176fad 的 navy 改动，diff 逐块核对）**：
- `styles/tokens.css`：删除 navy-pro 全部 token 块与侧栏覆盖变量（-107 行），完整还原四套预设原始配色；
- `domain/theme.ts`：预设列表回到晨雾青绿（默认）/云朵浅蓝/奶油杏金/极夜专业版四套，默认 preset=mist-jade；
- `style.css`：侧栏颜色全部恢复原 token 绑定（--text-dim/--accent-soft 等）；e176fad 中的按钮精修（:active 按压、:hover:not(:disabled)、统一行高）**有意保留**——与本轮按钮细则一致；
- `MainLayout.vue`：侧栏 footer 颜色还原。

**像素图标（真实可用的本地方案，零新增依赖）**：
- 图形数据精选自 pixelarticons（MIT License, © Gerrit Halfmann），内联 29 组 24×24 path 到 `domain/pixelIconPaths.ts`（模块头注明来源与许可）；
- `components/global/PixelIcon.vue`：currentColor 单色渲染（自动随主题）、默认 16px、shape-rendering=crispEdges、基线对齐；
- 接入位置（保持文字/功能/位置/尺寸不变）：六个工作区 tab + 全部二级导航（emoji → 像素图标，共 24 处）、主题菜单扳手与浅色/深色切换（aria-label 保留）、仪表盘与设置页组件排序 ↑↓、四处弹窗关闭 ✕（任务中心/情绪抽屉/情绪确认/决策版本链，中文 aria-label 补齐）。

**验收（真实浏览器操作）**：
- 清除缓存后默认主题即晨雾青绿（bg 断言 rgb(243,247,245)）；UI 菜单 极夜 ↔ 晨雾青绿 往返正常；
- 32 个像素图标渲染于总览页（9 个于情绪页），着色随主题（rgb(11,125,107) 青绿）；
- 1920/1280/1024 三档宽度零溢出、零控制台错误；禁用按钮 cursor:not-allowed、无误导性悬停；
- vue-tsc 0 错误 · vite build 通过 · 19/19 单测。

**截图**：`18-mist-jade-restored-pixel-icons.png`（晨雾青绿恢复 + 像素图标导航 + 像素主题控件）。

**未完成/遗留**：管理后台其余文字按钮（如「刷新」「运行」）按"不推倒重做"要求保持纯文字；社区情绪模块接入仍待后续任务。



## 十三、第六轮：马赛克图标彩色化 + 专业数据可视化

**马赛克图标（保留像素图案，只调尺寸与配色）**：
- 放大：工作区 16→20px、导航 15→18px，各加 26px 圆角色块容器（内边距与间距随之增加，导航无挤压）；
- 八色独立配色（低饱和清新系，`--tint` CSS 变量驱动）：市场总览薄荷绿、股票研究珊瑚橙、财富管理天空蓝、多资产研究池薰衣草紫、AI 研究樱花粉、市场情绪柠檬黄、数据中心青蓝、系统设置浅灰紫；
- 容器 = `color-mix(tint 13%, transparent)` 底 + tint 色图标；悬停加深至 22% + 轻投影；激活态 26%；页面标题（h1）前渲染同色同图标的 30px 容器——侧栏与页面标题颜色一致；
- 像素图案本身未改动；深浅主题均可读。

**专业图表（全部复用现有 ECharts 封装 + chartTheme 主题联动，零新增依赖；全部真实数据）**：

| 图表 | 页面 | 数据来源 |
|---|---|---|
| 策略风险收益散点（x 波动率 / y 收益 / 点大小=夏普） | 策略对比与优化 | `POST /v2/strategies/compare` 真实 comparison（实测 3 策略：S2 +5.64%/11.18%、S3 +7.27%/16.04%、S4 +6.23%/4.59%） |
| 资产类别分布矩形树图 | 多资产研究池 | `GET /v3/quality` 全量 33 只按类别（悬停显示合格/降级/拒绝/缺失率） |
| 数据质量状态柱状 | 多资产研究池 | 真实 quality_counts（实测 29/4/0） |
| 市场情绪 30 日走势面积线 | 市场情绪 | `GET /market/history` 真实每日快照 |
| 事件方向分布环形图 | 市场情绪 | 真实 by_direction 计数（利好 21/利空 13/中性 6/多空交织 13） |
| 类别情绪均值条形 | 市场情绪 | **前端聚合口径，卡片内显著标注**（非后端独立评分） |
| 实验风险收益散点 + 模型调用耗时条形 | AI 研究 | 真实 experiments metrics 与调用审计 latency_ms（待真实研究运行后出图，空态守卫已验证） |

回测净值/回撤渐变面积图（v1 BacktestCenter、v2 StrategyBacktestResult、v3 逐调仓 step 线）此前已实现，本轮验证未重做。

**顺带修复**：① ECharts 缺注册 `ScatterChart`/`TreemapChart`（图表无法渲染的根因）；② 四个财富视图的 scoped `button.primary` 覆盖导致主按钮青绿字压在青绿渐变底上不可见——统一走全局 `button.mini.primary`（运行比较/运行优化/发起研究等主按钮文字恢复可见）；③ 类别条形被误插进 `.cat-grid` 导致 166px 塌陷——移至网格外并反转 Y 轴排序（最高在上）；④ 图表 setOption 统一 notMerge 模式防主题切换合并残留。

**验收**：vue-tsc 0 错误 · build 通过 · 19/19 单测；极夜↔晨雾青绿切换后全部图表同步重绘（截图 22）；1280 宽无溢出；散点/树图/柱状/环形/面积线全部真实数据渲染（截图 19-22）。

**未完成/遗留**：① AI 研究散点与耗时图需一次真实研究运行产出 experiments/model_calls 后出图（空态守卫已验证，逻辑与散点图同构）；② 情绪 30 日历史目前仅 1 个自然日的快照（后端刚部署），随数据积累曲线展开；③ 词云未做（契约未提供分词数据，不做假词频）。

## 九、已知边界（如实记录）

- 5173 端口被另一个旧 dev server 占用，本轮验证使用 5174；旧进程属上一轮会话，未擅动。
- `frontend/probe3.mjs` 为交接遗留物，未删（不参与构建）。
- 图表主题热切换通过实例重建实现（ECharts 不支持运行时换主题），切换瞬间图表重绘一次，为预期行为。
- 任务记录保留最近 50 条于 localStorage，"清空记录"可用；同步接口无进度条属真实语义（请求返回即终态），未伪造进度。
