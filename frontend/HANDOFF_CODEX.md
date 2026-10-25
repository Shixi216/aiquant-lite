# AIQUANT-LITE 前端交接手册（交 Codex 审计）

> 交接时间：2026-10-25 深夜 · 交接人：GLM（前端负责人，六轮迭代）
> 审计基线：`5ee0573`（发布前自查）+ `c598565`（可视化）+ 报告更新提交
> 状态：**前端代码已冻结，等待 Codex 独立审计与推送**。审计期间请勿在前端目录并行开发。

---

## 一、项目概览

| 项 | 值 |
|---|---|
| 位置 | `E:/hermes-opc/frontend/`（独立于后端仓库目录，单页应用） |
| 技术栈 | Vue 3.5 + TypeScript 5.9 + Vite 7 + Pinia 3 + Vue Router 4 + ECharts 6 |
| 运行时依赖 | 仅 6 个：axios / echarts / pinia / vue / vue-router（+类型）。**零 UI 组件库、零图标库**（像素图标为内联 SVG path） |
| 许可证 | Apache-2.0（项目）+ MIT（pixelarticons 图形数据，来源已在模块头注明） |
| 常用命令 | `npm run dev`（5174，5173 被旧进程占用）/ `npx vue-tsc --noEmit` / `npm test`（19 用例）/ `npm run build` |
| 提交纪律 | 全部本地提交，**从未推送 GitHub**；推送由 Codex 决定 |

## 二、六轮迭代史（git log 可核对）

| 提交 | 内容 |
|---|---|
| `33ce95c` | WorkBuddy 交接基线检查点（98 文件） |
| `aa76a1c` | 主题系统（4 预设×2 模式）+ 任务中心 + 图表 token 化 |
| `ce6a66c` | 任务中心对接后端 v3.2 jobs 契约（job_id + 真实轮询） |
| `2db185a` | v3.3 AI 智能研究工作台（三模型受控研究） |
| `359f116` | 多资产市场情绪工作区（wealth-sentiment/v1.1） |
| `e176fad` | 全站中文化 + 深海军主题（**已被下轮回退**，中文化保留） |
| `efe0c26` | 恢复晨雾青绿默认 + 像素图标（pixelarticons, MIT） |
| `c598565` | 图标八色化放大 + 四组专业图表 |
| `5ee0573` | 发布前自查（组件状态聚合 + AI 页白屏修复 + AI 真实验收） |

## 三、六工作区与路由（18 条，全部保留兼容）

| 工作区 | 路由 |
|---|---|
| 股票研究 | /dashboard /opportunities /quant /stocks/:symbol /sim-tracker /capital-behavior /decisions /research-health |
| 财富管理 | /wealth-lab /wealth-factors /wealth-universe /wealth-strategy-factory /wealth-strategy-compare /wealth-ai-risk |
| 数据中心 | /task-center |
| 系统设置 | /settings |
| AI 研究 | /ai-research |
| 市场情绪 | /wealth-sentiment |

## 四、核心架构约定（审计重点）

### 1. 主题系统：CSS Variables 单一取色处
- `src/styles/tokens.css`：4 预设（晨雾青绿默认/云朵浅蓝/奶油杏金/极夜专业版）× 2 模式；旧变量名（--bg/--bg-panel…）保留为别名，存量组件零改动换肤；
- **组件内禁止硬编码 hex**——`tests/theme-tokens.test.mjs` 为架构守卫（含白名单 HealthChip）；
- `src/domain/theme.ts`（纯函数：预设/持久化/DOM 应用）+ `src/stores/theme.ts`（pinia：chartVersion 重绘信号 + 导入导出）；持久化仅 localStorage `wl-theme-settings-v1`（纯外观偏好，无任何数据/凭据）。

### 2. 中文语义层：`src/domain/labels.ts`
"唯一中文语义层"约定——状态枚举/角色/类别/校验标记的映射全在此（v3.3 研究状态、任务状态、情绪方向/分析状态/类别、v3 九类资产、健康状态等）。页面内禁止散落三元翻译。

### 3. API 三出口（不要合并，端口语义不同）
| 出口 | 文件 | 说明 |
|---|---|---|
| `/api`（Router 8767） | `api/client.ts` 的 `api` | **只读可视化门面**：GET 正常，POST 一律 405——任务中心相关按钮如实显示失败，**不得绕过** |
| `/wealth-lab-api`（财富后端 8765） | `api/wealthLab*.ts` / `aiResearch.ts` / `sentiment.ts` / `taskCenter.ts` 的 `wealthApi` | 可写链路：回测、jobs 队列、AI 研究、情绪 |
| 统一信封 | 各文件 `unwrap` | HTTP 200 + `status=FAILED`（如 PIT 拒绝）**不降级为 Mock**，原样展示 `sanitized_error` |

降级规则：仅网络不可达/404 落契约 Mock（`source="offline-mock"`，页面显著标注）。

### 4. 图表封装
`charts.ts`（**按需注册**：Line/Bar/Pie/Scatter/Candlestick/Heatmap/Sankey/Treemap——新增图表类型必须先在此注册，否则静默不渲染，见遗留教训）+ `composables/useECharts.ts`（主题热切换：dispose→按主题重建→恢复 lastOption；null 守卫）+ `domain/chartTheme.ts`（取色读 CSS 变量，与渲染主题严格一致）。**所有 setOption 均为 notMerge 模式**（防主题切换合并残留）。

### 5. 像素图标
`components/global/PixelIcon.vue` + `domain/pixelIconPaths.ts`（29 组 24×24 path，pixelarticons MIT）。单色 currentColor 随主题；新增图标从该仓库 svg/ 复制 path。

### 6. 中文化原则
用户可见层全部中文（枚举/路径/参数名经 labels.ts 或 title 悬浮映射）；**原始 JSON 仅保留在"技术审计"折叠区**（请求参数/响应原文），审计能力不删除。

## 五、真实数据链路（审计验证入口）

| 功能 | 验证方式 | 实测证据 |
|---|---|---|
| 仪表盘/量化/个股 | 打开即真实行情（Router 8767 GET） | 截图 01/13 |
| 任务中心 jobs | 提交→job_id→3s 真实轮询→终态 | `wljob_7f9debdd9d16`（情绪重建 100%）、`wljob_64074752707c`（行情增量，rows=0 周末语义） |
| 财富回测 | 策略工厂/任务中心 | `wlbt_484fa7981627`（S2，610 日） |
| v3.2 时间语义+逐调仓 | 工厂选多资产池 | requested 2021-02-04 / effective 2024-04-15 / 31 条 rebalance_records |
| v3.3 AI 研究 | /ai-research 发起稳定研究 | `wlrun_aa9d6a8f55c0`：硬风控 WARN→Qwen 81.7s→Pro 4.8s→核验通过→决策包 `wpkt_eb06518fe29b` |
| 情绪 | /wealth-sentiment | 市场 +17.3（53 证据）、33 资产逐只评分、重建 job `wljob_7f9debdd9d16` |

## 六、已知问题与遗留（诚实清单）

1. **`/capital-behavior` 显示"暂无对应数据"**——后端当日无数据（404 如实展示），非前端缺陷；
2. **任务中心 6 个只读网关按钮失败展示**——8767 部署为 GET-only 门面（HTTP 405），前端如实显示"本次未产生任何数据变更"；等后端开放写链路后**无需前端改动**即可用；
3. **AI 实验散点待实验模式运行**——3 次调用预算，逻辑与已验证的稳定模式同构；本轮以稳定模式验收（耗时图/审计表/决策包已出图）；
4. **情绪 30 日历史仅 1 个自然日快照**——后端刚部署，随数据积累展开；
5. **词云未做**——契约无分词数据，不做假词频；
6. **research run 无列表/取消端点**——契约限制，UI 如实标注（720s 总超时兜底）；
7. **pipeline 线上要求 `claims` 字段**——与仓库 schema 不一致，需后端确认。

## 七、教训清单（防复发，审计时可核对）

1. **vue-tsc 输出曾被管道截断**，掩盖 `watchEffect` 未导入 → AI 页白屏（`5ee0573` 修复）。**跑类型检查不截断输出**；
2. **ECharts 按需注册**：新增 series 类型必须同时注册（Scatter/Treemap 曾漏）；
3. **scoped `button.primary` 覆盖全局**导致主按钮文字不可见——已全站清除，勿再引入；
4. **vite HMR 腐化**会让未改动页面白屏（fetchAssetScore is not a function）——先重启 dev server 再排查；
5. **后端 sentiment GET 每请求新建 DuckDB 连接**，页面首载 33 并发有锁竞争——前端已加一次静默重试；根治需后端改单例只读连接（已移交后端）；
6. **axios 在 dev 下曾挂起**（compare 请求 XHR 200 但 promise 不 resolve）——生产构建不复现；排查时先试 `npm run preview`。

## 八、给 Codex 的审计建议（复现路径）

1. **静态**：`npx vue-tsc --noEmit`（0 错误）；`npm test`（19/19，含主题守卫）；`npm run build`（通过）；
2. **主题**：清 localStorage → 默认晨雾青绿；UI 菜单往返极夜/晨雾青绿；设置页改主色/密度/导航布局并刷新（持久化恢复）；
3. **图标**：侧栏六工作区八色 tint + 页标题同色图标（对照截图 18/19）；
4. **任务中心**：「运行财富策略回测」→ job 提交 → 内联轮询 → 终态留痕（对照截图 04/08）；「抓取全市场快照」→ 405 诚实失败展示；
5. **AI 研究**：清缓存后 /ai-research → 稳定研究 + 勾选付费确认（**会产生 2 次真实模型调用**）→ 步骤条/审计表/耗时图/决策包（对照截图 24）；**注意会产生付费调用，审计时可仅验证 UI 与 roles 端点**；
6. **情绪**：/wealth-sentiment 三图 + 33 资产表 + 聚合口径标注（对照截图 19/22）；
7. **宽度**：1920/1440/1280/1024 四档 18 路由（上轮矩阵 0 溢出 0 错误）。

## 九、边界声明

- 后端 Python/数据库/任务队列/风控规则**零改动**（其他窗口负责）；
- 前端未推送 GitHub，未切分支；独立备份在 `backups/frontend-20261025/`；
- 完整交付报告（六轮明细 + 中文化对照 + 数据来源表）：`frontend/UI_UPGRADE_REPORT_20261025.md`；验收截图 24 张：`frontend/docs/screenshots-20261025/`。
