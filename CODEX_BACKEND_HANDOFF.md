# AIQUANT-LITE 后端交接手册（交 Codex 审查推送）

> 交接人：后端技术负责人（ZCode）· 冻结提交 `e0ff3c7` · 2026-10-25
> 远程分支 `sentiment-v3-recovery-strategy-lab` 当前在 `ddcf0e6`，本地领先 **37 个提交**（262 文件，+43,396 行）。

---

## 一、你现在需要做什么

1. 审查本文档第二至六节，确认无异议。
2. 将其他窗口（Codex/前端 GLM/WorkBuddy）的未提交修改收口为干净提交。
3. 运行第七节的全量回归测试，确认所有失败项已解决或可接受。
4. 执行安全扫描（第六节），确认无泄露。
5. 推送 `sentiment-v3-recovery-strategy-lab` 分支（不合并 main，除非你决定发 PR）。
6. 通知前端 GLM 拉取最新代码。

---

## 二、本地 37 个提交的内容概览

### Wealth Lab 后端（v1→v3.4，全部由 ZCode 开发）

| 版本 | 提交 | 内容 |
|---|---|---|
| v1 | `7a9129d` + `2e0021a` | 四资产回测、双源数据池、/v1/wealth-lab/* API、Tushare volume 修复 |
| v2 | `ed88101` | 九策略 S0-S8、组合优化（pypfopt/native HRP）、ETF 因子、AI 双 Agent、决策包、QuantStats 等效报告 |
| v3 | `c7712f4` + `89f4958` | 33 只八类资产、Research/Backtest Eligible（PIT）、五池注册、v3 动态池回测 |
| v3.1 | `a2c0107` | 162411→QDII_LOF 分类修正、逐调仓 PIT 重评、质量分页 |
| v3.2 | `424dc80` + `6e6e2d3` | 逐调仓完整记录、时间语义、任务控制中心（白名单/串行/幂等/取消） |
| v3.3 | `96cfa33` | 三模型受控 AI 研究（Flash 规划/Qwen 研究/Pro 复核/硬 VETO/数值核验） |
| v3.4 | `723000f` | jobs 类型 bug、质量口径修复（29/4 恢复）、多资产 AI 全链路 COMPLETED |
| 封板 | `777e24b` + `e0ff3c7` | 情绪 API 契约、OpenAPI 路由白名单 |

### 其他窗口的提交（穿插在上述提交中）

| 窗口 | 提交范围 | 内容 |
|---|---|---|
| Codex | `585f784` + v3.1 期间的修复 | 股票系统 scanner/decision_support bug 修复（9 处）、volume 单位统一 |
| 前端 GLM | `aa76a1c`→`c598565`（4 个） | WebUI 主题/图标/任务中心前端/情绪工作台 |
| WorkBuddy | `33ce95c` | 前端基线 checkpoint |
| 情绪 | `3b0ba02` + `d408a8d` + `aeeff78` | wealth-sentiment v1/v1.1、social-sentiment 合规模块 |

---

## 三、当前工作区状态

### 已提交（可安全推送）

全部 Wealth Lab 后端代码、契约、Mock、测试——见第二节。

### 未提交修改（需要你收口）

**47 个 modified 文件**——主要是 Codex 的股票系统修复（scanner/decision_support/data_hub/providers），已在独立核验报告 `reports/wealth_independent_20261024/` 中确认过正确性。另有少量路由白名单和配置调整。

**171 个 untracked 文件**——主要是：
- `frontend/` 目录（GLM 前端全部代码，未跟踪）
- `research/` 目录（研究脚本）
- 各种 `.bak`、日志、临时文件

### 推送前必须处理

1. **`frontend/`**：由 GLM 决定是否提交（当前整个目录 untracked）
2. **竞赛 Word 文档**（`~$杯AIQUANT...docx` 等）：确认不在 `git add` 范围内
3. **临时/日志文件**：清理或加 `.gitignore`

---

## 四、关键架构决策（审查时需了解）

### 4.1 数据库隔离

| 库 | 用途 | 访问方式 |
|---|---|---|
| `database/hermes_opc.duckdb` | 股票系统主库 | Router 单 Owner（`connection_manager` 守卫） |
| `database/wealth_lab.duckdb` | 财富管理独立库 | Wealth Lab 模块直连（模块锁+每请求连接） |

**两个库完全隔离**，无交叉引用。Wealth Lab 不写入主库，股票系统不读取财富库。

### 4.2 AI 三模型固定分工

```
PLANNER    → DeepSeek V4 Flash  (deepseek-flash)    # 实验模式规划，≤1次
RESEARCHER → Qwen 3.8 Max 0902  (qwen3.8-max-0902)  # 金融研究，≤1次
REVIEWER   → DeepSeek V4 Pro    (deepseek-v4-pro)    # 独立复核，≤1次
```

- model_id 全部来自已验证配置，**禁止猜测或替换**
- 状态机固定：PLANNING→VALIDATION→APPROVAL→BACKTESTING→HARD_RISK_CHECK→RESEARCH→REVIEW→VERIFICATION→COMPLETED
- 硬 VETO 在 AI 调用之前（任何模型不能解除）
- 数值核验失败在决策包生成之前终止
- 审计表：`wealth_research_calls`（不含 API Token）

### 4.3 策略工厂

| 策略 | 说明 | 防未来函数 |
|---|---|---|
| S0 固定权重 | core4 基线（1587 日） | N/A |
| S2 横截面动量 | 126 日动量 top-2 | decision_data_through 审计字段 |
| S4 波动率倒数 | 63 日逆波动（60% 上限迭代触顶） | 同上 |
| S8 HRP | 层次风险平价（Lopez de Prado 独立实现） | 估计窗口=252 日滚动 |

所有策略共用 `run_strategy_backtest` 引擎：份额模型+现金+成本（Σ|Δw|×bps/1e4）。

### 4.4 核心冻结基线（不可修改）

| 基线 | 值 | 验证方式 |
|---|---|---|
| core4 S4 总收益 | 45.9470% | v2 通道复跑逐位一致 |
| core4 S4 最大回撤 | -5.3351% | 同上 |
| S2/S3 修正值 | 41.2098% / 56.1974% | 独立复算 3.56e-15 吻合 |
| 回测区间 | 2020-04-07~2026-10-23 (1587 日) | 口径裁定文档 |

---

## 五、任务控制中心（白名单操作）

```
update_fund_basic     更新基金基础资料
update_market_data    增量更新行情（官方优先→代理降级）
update_adj_factors    复权因子更新（缺失即失败）
run_quality_check     质量审计重跑（SSE 日历分母）
rebuild_pit           PIT 资格重评（12 个月初锚点）
run_backtest          策略回测
run_ai_research       AI 研究（confirm=true 必须）
generate_report       存档报告
refresh_wealth_news   情绪新闻采集（其他窗口添加）
rebuild_wealth_sentiment_factor  情绪因子重算（其他窗口添加）
```

安全规则：白名单外 422 拒绝、串行单 worker、幂等去重、进度真实（非假定时器）。

---

## 六、安全扫描清单（推送前 Codex 逐项确认）

| 项目 | 状态 | 说明 |
|---|---|---|
| `.env` 文件 | ✅ 已忽略 | 含 DASHSCOPE_API_KEY、TUSHARE_TOKEN、代理 token、Flash model id |
| `*.duckdb` | ✅ 已忽略 | 主库+财富库+情绪库 |
| `logs/` | ✅ 已忽略 | 运行日志 |
| API Key 在代码中 | ✅ 无泄露 | grep "sk-ws-"/"947c5eda" 在跟踪文件中零命中 |
| API Key 在历史中 | ✅ 无泄露 | git log -S 扫描零命中 |
| 竞赛 Word 文档 | ⚠️ 不在跟踪中 | 工作区有 `~$` 临时文件，确认 git add 时排除 |
| `frontend/` | ⚠️ 未跟踪 | 由 GLM 决定是否纳入提交 |
| 模型原始请求 | ✅ 不落盘 | 审计只存 prompt_version + hash，不存原文 |
| 企业微信 Webhook | ✅ 无硬编码 | 全走 `.env` |

**推送前额外检查**：`git diff origin/HEAD..HEAD --stat` 逐文件过目，确认无意外文件混入。

---

## 七、全量回归测试（推送前运行）

### 已验证通过（ZCode 冻结时）

```
批次1: wealth-lab 核心 (8 文件)          → 86 passed
批次2: scanner + API 路由白名单           → 86 passed
批次3: decision packets + root cause     → 36 passed
合计后端核心: 208 passed, 0 failed
```

### 已知失败（跨窗口，需要你解决）

| 文件 | 测试 | 原因 |
|---|---|---|
| test_sentiment_v1.py | 3 个 | Qwen key 轮换后测试 mock 未更新 |
| test_black_swan_rules.py | 1 个 | 依赖 codex 未提交的 event_extractor 修改 |
| test_experiment_evaluation_v1.py | 1 个 | 同上 |
| test_fundamental_modes.py | 1 个 | 同上 |

### 推送前最低验证命令

```bash
cd E:/hermes-opc
./.venv/Scripts/python.exe -m pytest tests/test_wealth_lab_research.py \
  tests/test_wealth_lab_jobs.py tests/test_wealth_lab_v3.py \
  tests/test_wealth_lab_v2.py tests/test_wealth_lab_backtest.py \
  tests/test_wealth_lab_p0_verification.py \
  tests/test_wealth_sentiment_v11.py tests/test_wealth_social_sentiment.py \
  -p no:cacheprovider --tb=short
# 预期: 86 passed

curl -s http://127.0.0.1:8765/health | python -m json.tool
# 预期: status=ok, database_owner=router
```

---

## 八、禁止事项

1. **禁止修改** `trading/wealth_lab/backtest.py` 中的冻结基线数字
2. **禁止放宽** `WealthRiskPolicy` 中的风控阈值
3. **禁止删除** core4-v1 资产池或旧存档回测记录
4. **禁止替换** 三模型 model_id 为其他模型
5. **禁止跳过** `confirm_paid_calls` 检查
6. **禁止将** 情绪分数注入 PIT 历史回测（`trading/wealth_lab/backtest.py` 与 `strategies/` 中无 sentiment 引用——保持为零）
7. **禁止** 未经用户确认推送 `frontend/` 目录

---

## 九、快速参考

```
服务启动:    powershell -File scripts/start_hermes_opc_venv.ps1
服务停止:    powershell -File scripts/stop_hermes_opc.ps1
数据回填:    PYTHONPATH=. ./.venv/Scripts/python.exe -m scripts.wealth_lab_backfill
质量审计:    PYTHONPATH=. ./.venv/Scripts/python.exe -m scripts.wealth_lab_v3_backfill
分类修正:    PYTHONPATH=. ./.venv/Scripts/python.exe -m scripts.wealth_lab_v31_reclassify
契约目录:    docs/contracts/
Mock 目录:   docs/contracts/mocks/
交付报告:    docs/wealth_lab/
```

---

## 十、联系信息

后端冻结后如有问题，回溯本文档。核心设计决策的完整推理链在各版本的交付报告中（`docs/wealth_lab/wealth_lab_v*_delivery_*.md`）。
