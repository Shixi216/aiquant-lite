# Wealth Lab 前端交接文档（交 ZCode）

> 交接时间：2026-10-24 23:40（GMT+9）· 交接人：前端负责人（Claude）
> 基线：本地提交 `a2c0107`（v3.1 后端）· 前端全部成果在本地 `frontend/`，未提交、未推送
> 适用范围：财富管理实验室 v1/v2/v3.1 前端。股票投研系统（Dashboard/个股/量化等）本轮零改动。

---

## 一、页面与路由总览

| 路由 | 页面 | 契约 | 引擎/数据 |
|---|---|---|---|
| `/wealth-lab` | 大类资产配置（v1 四区：驾驶舱/配置/回测/压力测试） | v1 | v1 真实后端（8765） |
| `/wealth-factors` | ETF 多因子研究 | v2 | 真实（五因子 + composite_rank 已修复为 0-1 分位） |
| `/wealth-universe` | **多资产研究池（v3.1 新增）** | v3/v3.1 | 真实（33 只资产、6 池、PIT 判定、质量分页） |
| `/wealth-strategy-factory` | 策略工厂（S0-S8）+ 资产池选择 | v2 + v3 | 基线走 v2 引擎；动态池走 v3 引擎 |
| `/wealth-strategy-compare` | 策略对比与组合优化 | v2 | 真实 |
| `/wealth-ai-risk` | AI 研究/风险复核/风险 VETO/决策包 | v2 | 真实 |

导航：`MainLayout.vue` 「财富管理」组 6 项；搜索框在 `/wealth*` 路由下 placeholder 为"股票、基金或ETF代码/名称"（仅文案，逻辑与股票页共用）。

## 二、前端架构约定（联调前必读）

1. **信封结构**：所有 wealth API 返回统一信封。前端服务层返回 `{ envelope, source }`（`WealthResult<T>`，定义于 `api/wealthLab.ts`）。**成功路径取数一律 `response.envelope.data.xxx`**——历史上两次踩坑把 `response.data` 当 envelope 用导致 TypeError（现象：结果渲染了但错误条残留）。
2. **降级策略**：网络不可达 / 路由 404 → 自动回落契约 Mock（`source="offline-mock"`，页面显著标注"契约 Mock 样例"）；**HTTP 200 + status=FAILED（如 PIT 闸门拒绝）不降级**，原样展示 `sanitized_error`，不伪造结果。
3. **唯一 HTTP 出口**：`api/wealthLab.ts`（v1）、`api/wealthLabV2.ts`、`api/wealthLabV3.ts`。组件禁止直接发请求。
4. **中文标签唯一来源**：`domain/wealthLabLabels.ts`（v1/v2 通用）+ `domain/wealthV2Format.ts`、`wealthV3Format.ts`（v2/v3 专属，部分 re-export labels）。REAL→真实数据、WARN→风险警告、PASS→检查通过、VETO→风险否决。
5. **日期输入**：统一组件 `components/wealth/WlDateInput.vue`（文本框显示 YYYY-MM-DD，非法输入红框回退）。**emit 事件名必须是 `update:modelValue`**（曾误写 `update` 导致 v-model 静默失效）。
6. **图表**：`composables/useECharts.ts` 的 `setOption` 已加 null 守卫；数据未就绪时 option 为 null 不得传入。
7. **代理**：`vite.config.ts` 中 `/wealth-lab-api` → `http://127.0.0.1:8765`（rewrite 去前缀）。后端换端口需同步 `api/wealthLab*.ts` 的 baseURL。
8. **跨页状态**：`stores/wealthLabV2.ts`（pinia）保存最近一次策略回测 `{backtestId, strategyId, strategyVersion, universeId}`，供 AI 研究/决策包自动关联（带上下文校验）。

## 三、v3.1 已对接的真实接口（实测响应为准）

| 端点 | 状态 | 前端用法 |
|---|---|---|
| `GET /v3/universes` | ✓ 6 池（含 multi-asset-v2 v2.0"分类修正"） | 研究池下拉 + 工厂池选择 chips |
| `GET /v3/universes/{id}/assets` | ✓ | 未用于主表（quality 覆盖） |
| `GET /v3/quality?page&limit&category` | ✓ 分页生效，33 只 | 研究池表格（每页 10，验收走查 10+10+10+3）|
| `GET /v3/eligible?universe_id&asof` | ✓ PIT 判定 | 历史可投资池（含排除原因）|
| `POST /v3/strategies/{id}/backtest` | ✓（部分起点被 PIT 闸门正确拒绝）| 工厂动态池回测 |

**v3.1 实测响应结构要点**（契约 yaml 尚未更新这些细节，前端已按实际响应实现）：
- `AssetQuality` 新增：`product_form`（ETF/LOF）、`underlying`（OVERSEAS_EQUITY…）、`market_scope`（HK_CONNECT/OVERSEAS/DOMESTIC）、`currency_risk`（HKD/USD_MULTI/NONE）。162411→`QDII_LOF`（LOF·境外·美元多资产），159545→`CN_STYLE_ETF` + 港股通/港元。
- `quality` 分页：`page/limit/next_page`；`total=33`，QUALIFIED 29 / DEGRADED 4。
- `pit_rebalance_snapshots` 为 **dict**：`{ note, rebalances(int 总次数), first/last: {decision_date, eligible_count, excluded: {symbol: [原因]}}, eligible_count_min, eligible_count_max }`。**没有逐调仓序列**——前端审计区对"逐调仓合格数量序列"显示"未提供"，不作推算。

## 四、待 ZCode 补充/确认（前端已留好接入口）

| # | 事项 | 现状 | 前端处理 |
|---|---|---|---|
| 1 | 逐调仓 PIT 明细（每次调仓的 eligible_count + excluded 序列） | 仅 first/last 端点 + min/max | 审计区显示"未提供"；序列到位后可直接加折线图 |
| 2 | PIT 拒绝时返回建议起始日期（如 `suggested_start_date`） | FAILED 响应只有 reason | 页面展示 reason + 引导用户去研究池查可投资时点；字段到位即展示 |
| 3 | quality 的质量状态筛选 / 关键字搜索服务端参数 | 无（只有 category） | 状态筛选与搜索走"全量拉取+前端过滤+本地分页"，UI 已标注"前端过滤（契约暂无服务端参数）"；参数到位后切回服务端即可 |
| 4 | 部分资产 name 乱码占位（如 `utf8-LQF A-CNY`） | 数据侧问题，前端如实展示 | 待修 |
| 5 | quality 明细 11 只 vs total 33（当时观察） | 最新实测已返回全量 33 | 已确认修复，无需动作 |
| 6 | v3 池回测（S0 + multi-asset-v1 + 2020 起点）被 PIT 闸门拒绝 | 后端正确行为 | 见 #2 |
| 7 | 契约 yaml 未含 v3.1 增量字段（分页/分类维度/快照 dict） | 前端按实际响应实现 | 建议契约升版补录，保持"先契约后代码"流程 |
| 8 | `source_breakdown` 契约声明 object，实际返回 JSON 字符串 | 前端 `parseSourceBreakdown` 双兼容 | 统一为一种 |

## 五、本轮修复过的关键 bug（避免复现）

1. `WlDateInput` emit 名 `update` ≠ v-model 约定 `update:modelValue` → 用户改日期静默失效。已修。
2. `fetchV3Quality` 用的 `get` helper 不透传 params → 分页参数丢失、"下一页"恒禁用。已修（直接 `http.get(url, { params })`）。
3. 工厂页 `universes` computed 漏写 → 模板 `undefined.filter` 运行时崩溃。已修。
4. v1 压力测试/v3 AI 研究的 `response.data.*` 误用（应为 `response.envelope.data.*`）→ catch 里 TypeError、错误条残留。已修并全库 grep 确认。
5. `useECharts.setOption(null)` 抛 "option is null/undefined" → composable 加空值守卫。
6. 后端对并发 POST 敏感（DuckDB 单写者）：前端已做 stress 串行化 + 静默重试一次 + backtest(800ms)/stress(600ms) 错峰防抖；如后端支持并发建议告知。

## 六、前端验收记录（本轮）

- `vue-tsc --noEmit` + `vite build` 通过（0 错误）。
- 分页：第 1–10 / 11–20 / 21–30 / 31–33 全量走查 ✓。
- 162411/159545 分类维度渲染 ✓（含 v3.1 修正说明原文）。
- multi-asset-v2 回测：610 交易日、+20.84%、PIT 审计区（首末快照 + 排除原因 + 幸存者偏差声明）✓。
- 基线兼容：core4-v1 回测、v1 四区联动（权重滑杆→回测重算 ¥133.44万→¥134.49万）✓。
- 四页无 pageerror；截图在 `.workbuddy/shots/wealth-lab-*.png`（1920 宽 2x）。

## 七、前端文件清单（本轮全部新增/修改，均未提交）

```
新增  src/types/wealthLabV2.ts, wealthLabV3.ts
新增  src/mocks/wealthLabFixtures.ts, wealthLabV2Fixtures.ts, wealthLabV3Fixtures.ts, wealthLabV3QualityMock.ts(脚本生成)
新增  src/api/wealthLab.ts, wealthLabV2.ts, wealthLabV3.ts
新增  src/domain/wealthFormat.ts, wealthV2Format.ts, wealthV3Format.ts, wealthLabLabels.ts
新增  src/components/wealth/{WealthDashboard,AllocationCenter,BacktestCenter,StressTestCenter,WlDateInput}.vue
新增  src/components/wealthv2/StrategyBacktestResult.vue
新增  src/views/{WealthLabView,WealthFactorsView,WealthUniverseView,WealthStrategyFactoryView,WealthStrategyCompareView,WealthAiRiskView}.vue
新增  src/stores/wealthLabV2.ts
修改  src/router/index.ts, src/layouts/MainLayout.vue, src/charts.ts, src/composables/useECharts.ts, vite.config.ts
```

## 八、给 ZCode 的联调建议

1. 联调时直接跑 `npm run dev`（5173），后端 8765 即可；Mock 降级仅在后端未启动时出现，徽章可区分。
2. 如改字段/加参数：先升 `docs/contracts/wealth-lab-api-*.yaml` 并知会前端，前端再同步 `types/wealthLab*.ts` 与 fixtures——前端所有类型由 `satisfies` 与契约 Mock 绑定编译期校验。
3. `pit_rebalance_snapshots` 若补逐调仓明细，建议数组结构 `{date, decision_data_through, eligible_count, excluded, weights}`，前端审计区可直接升级为逐行表 + 合格数折线图。
4. 决策包自动关联依赖前端 store 的回测上下文（含 universeId）；若后端要做服务端关联校验，请以 `strategy_id + universe_id + backtest_id` 三元组为准。
