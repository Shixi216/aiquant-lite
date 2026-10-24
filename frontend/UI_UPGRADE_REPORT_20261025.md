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
| 任务中心 | 「数据与研究任务中心」新页面：状态面板/数据操作/研究操作/任务记录，按钮→真实后端 API | ✅ |
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

| 按钮 | 方法与路径 | 出口 | 状态 | 说明 |
|---|---|---|---|---|
| 同步 A 股研究名录 | POST `/api/v1/universe/sync` | Router 门面(8767) | ✅已接 · 实测 405 | 请求体严格按 `UniverseSyncRequest`（analysis_mode/dry_run/provider/request_budget/data_cutoff） |
| 抓取全市场快照 | POST `/api/v1/market-snapshots/sync` | Router 门面 | ✅已接 · 实测 405 | 按 `MarketSnapshotSyncRequest` |
| 增量补充日线行情 | POST `/api/v1/data-expansion/run` | Router 门面 | ✅已接 · 实测 405 | `expansion_type=DAILY_BARS`，dry_run 优先 |
| 候选资产 enrichment | POST `/api/v1/candidates/enrich` | Router 门面 | ✅已接 · 实测 405 | ≤20 只，RESEARCH 模式 |
| 运行财富策略回测 | POST `/wealth-lab-api/v1/wealth-lab/v2/strategies/{id}/backtest` | 财富后端(8765) | ✅已接 · **实测 SUCCEEDED** | 返回 `backtest_id` 已留痕 |
| 新闻与公告分析流水线 | POST `/api/v1/pipelines/news-analysis` | Router 门面 | ✅已接 · 实测 405 | 按 `NewsPipelineRequest` |
| 官方公告核验流水线 | POST `/api/v1/pipelines/announcement-verification` | Router 门面 | ✅已接 · 实测 405 | 按 `AnnouncementVerificationPipelineRequest` |
| 更新基金名录 / 更新复权因子 / 运行质量检查 / 重新评估历史可投资池 / 多策略比较 / 触发 AI 研究 / 运行风险检查 / 生成研究报告 | — | — | **暂不可用（明示原因）** | 后端契约未交付，按规范置灰不伪造成功 |

> **实测发现**：当前 8767 部署为「只读可视化服务（GET-only 代理转发 8765）」，POST 一律 405。前端如实显示失败原因并注明"本次未产生任何数据变更"；8765 侧真实可写链路（财富回测）已闭环验证。另实测发现 8765 的 pipeline 契约要求 `claims` 字段，与仓库内 schema 文件不一致，需后端确认版本。

## 五、真实联调证据（2026-10-24 实测）

1. **任务闭环（成功路径）**：任务中心点击「运行财富策略回测」（S2_cross_momentum）→ 真实 POST → `SUCCEEDED`，耗时 0.3s，后端引用 ID `wlbt_484fa7981627`，详情展开可见完整请求体与响应（`request_id: req_45dd2bba…`、`data_mode: REAL`、策略参数 lookback_days=126/top_k=2）。截图 `04-task-center-real-run-record.png`。
2. **任务闭环（诚实失败路径）**：点击「抓取全市场快照」→ 真实 POST → 405 → 任务记录 FAILED + 明确原因"后端当前部署为只读可视化服务（GET-only 代理）…本次未产生任何数据变更"。
3. **PIT 拒绝 → 建议起始日 → 重跑成功**：策略工厂选 multi-asset-v1（29 只）+ 2020-04-08 起点回测 → 200+FAILED（PIT 闸门）→ 页面展示 `suggested_start_date: 2021-02-04`（交接单遗留 #2 在实测后端已交付）→ 点击「采用并重跑」→ SUCCEEDED：610 交易日、累计收益 +20.84%。
4. **时间语义三日期**：结果区显示 请求起点 2021-02-04 / 实际首日 2024-04-15 / 首次建仓 2024-04-15 / 实际交易日 610 + 顺延原因（多资产池行情窗口自 2024 起的诚实展示）。
5. **逐调仓 PIT 审计**：31 次调仓全量记录（决策日期/决策数据截止/PIT 合格数/实际入选/现金仓位/成本/目标权重/被排除原因），合格资产数 step 折线图；交接单 #1 的"仅首末快照"已升级。截图 `06-factory-pit-rebalance-audit.png`。
6. **服务端筛选**：研究池页选择「数据降级」→ 显示"第 1–4 条 / 共 4 只 · 服务端筛选（status/q）"（v3.2 status 参数真实生效；q 关键字同链路）。截图 `07-universe-server-side-filter.png`。
7. **主题切换**：晨雾青绿 → 极夜专业版 → 晨雾青绿 往返实测，`<html>` dataset 与 --accent 计算值断言正确（#0f9b84 ↔ #4da3ff），图表/表格/侧栏同步换肤。截图 01/02/03。
8. **状态面板（真实数据）**：最近行情日期 2026-10-23（wealth 数据池）、研究资产 33、A股名录 5871 条（版本 universe_7b642449a139b438）、质量 29✓/4 降级、快照"陈旧"语义、Router ok。
9. **路由走查**：12 条路由全部渲染正确 h1、零 pageerror（/capital-behavior 的"暂无对应数据"为后端 404 如实展示，属既有行为）。
10. **静态验收**：`vue-tsc --noEmit` 0 错误；`vite build` 成功（9.2s）；`node --test` 19/19（含新增主题架构守卫测试：tokens 完整性、style.css/组件 style 块无硬编码 hex 回归）。

**兼容性确认**：v1 四区（驾驶舱/配置/回测/压力测试）、v2 因子/对比/AI 风控、WlDateInput（update:modelValue 未动）、信封取数（envelope.data）、200+FAILED 不降级、离线 Mock 徽章——全部保持；Pinia `wealthLabV2` 回测上下文（backtestId/strategyId/universeId）继续写入。

## 六、浏览器截图（frontend/docs/screenshots-20261025/）

1. `01-dashboard-mist-jade-light.png` — 市场总览 · 晨雾青绿（真实行情数据）
2. `02-dashboard-polar-night-dark.png` — 极夜专业版切换实测（主题菜单展开）
3. `03-settings-theme-center.png` — 主题定制中心
4. `04-task-center-real-run-record.png` — 任务中心 · 真实回测留痕（请求/响应展开）
5. `05-strategy-factory-form.png` — 策略工厂（工作台浅色）
6. `06-factory-pit-rebalance-audit.png` — 逐调仓 PIT 审计（三日期 + 31 条记录 + 折线）
7. `07-universe-server-side-filter.png` — 研究池服务端筛选实测

## 七、待后端补充的接口（前端已留好接入口）

| # | 事项 | 说明 |
|---|---|---|
| 1 | **任务控制契约（最关键）** | 现有 8767 为 GET-only 只读门面，POST 405。请交付异步任务契约：受理返回 `job_id`，提供 `GET /jobs/{id}`（或事件流）含状态/进度/开始完成时间/结果/失败原因。前端 `TaskRunRecord.jobId` 与轮询接入口已预留，交付后即可从"同步即终态"升级为 job 轮询 |
| 2 | 数据操作写链路 | 「更新基金名录/更新复权因子/质量检查/PIT 池重建」等按钮现为"暂不可用"，契约到位后填入 `TASK_OPS` 对应项即可 |
| 3 | pipeline 契约对齐 | 8765 运行实例要求 `claims` 字段，仓库 `router/schemas/*` 无此字段——请确认线上版本与 schema 演进 |
| 4 | `rebalance_records` 位置统一 | 当前返回顶层（`data.rebalance_records`）而非 `pit_rebalance_snapshots.rebalance_records`，前端已双兼容；建议契约升版时固定一种并同步 `docs/contracts/wealth-lab-api-v3.yaml`（含 v3.1/v3.2 全部增量字段） |
| 5 | 首末快照结构统一 | `first/last` 已变为 RebalanceRecord 同构（`excluded_reasons`），前端已双兼容；同上建议契约固定 |
| 6 | 多资产池行情窗口声明 | multi-asset-v1 实际行情自 2024-04 起，`requested vs effective` 差异较大；建议在 universes 端点透出 `data_first` 以便前端预提示 |

## 八、已知边界（如实记录）

- 5173 端口被另一个旧 dev server 占用，本轮验证使用 5174；旧进程属上一轮会话，未擅动。
- `frontend/probe3.mjs` 为交接遗留物，未删（不参与构建）。
- 图表主题热切换通过实例重建实现（ECharts 不支持运行时换主题），切换瞬间图表重绘一次，为预期行为。
- 任务记录保留最近 50 条于 localStorage，"清空记录"可用；同步接口无进度条属真实语义（请求返回即终态），未伪造进度。
