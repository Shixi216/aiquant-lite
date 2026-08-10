# 阶段0.2 只读盘点报告（真实代码核查）

创建时间：2026-08-01
方法：grep 真实代码 + 数据库 PRAGMA 检查，非文件名推断

## 盘点结果

| 项目 | 状态 | 证据 |
|------|:---:|------|
| 统一用户模型（local_user_id） | ❌ 未实现 | 全项目 grep `local_user_id` 无结果；业务表无 user 字段 |
| 企业微信用户绑定 | ❌ 未实现 | 无 external_user_bindings 相关代码 |
| 权限服务（LEVEL1-4） | ❌ 未实现 | 无 permission_service |
| 人工持仓表 | ✅ 已有 | `manual_trades` 表（18列，含 portfolio_id/side/quantity/price/user_confirmed） |
| 人工成交账本 | ✅ 已有 | manual_trades 即成交记录表；trading/review/ 有相关服务 |
| Paper账户 | ⚠️ 部分 | `paper_accounts` 表存在（3列，account_json 存整包），experiments/ 有回测；但非完整模拟交易 |
| DecisionPacket | ✅ 已有 | `decision_packets` 表（14列）+ trading/decision_support/decision_packets/ 完整模块 |
| TradePlan | ❌ 未实现 | 无 trade_plan 表/模块 |
| 正式动作枚举（STRONG_BUY等） | ❌ 未实现 | grep 无结果 |
| 仓位计算器（PositionSizer） | ❌ 未实现 | 无 position_sizer |
| VETO实现 | ✅ 已有 | `risk_vetoes` 表（5列）+ trading/decision_support/risk_rules/rules.py |
| Scanner Skill | ⚠️ 部分 | 代码链路完整（parser/guard/filter/ranker），但 Hermes skill 目录无 market_scanner 技能（只有 stock-analysis） |
| stock_research/stock_decision Skill | ❌ 未实现 | skill 目录无 |
| 数据新鲜度守卫 | ✅ 已有 | trading/scanner/freshness_guard.py（Tushare主源） |
| snapshot_id/data_cutoff 传递 | ✅ 已有 | scanner repository chosen_snapshot + plan.data_cutoff |
| 后台刷新任务锁 | ⚠️ 部分 | cron 每日16:00 刷新，但无任务锁防重复 |
| 实盘接口/疑似实盘代码 | ✅ 无实盘链路 | git 提交 `7c02b41 Reserve disabled CITIC QMT adapter`（QMT适配器已禁用）；grep qmt/xtquant/easytrader/vnpy 仅 renderer.py 文字引用 |

## 数据库现有表（关键）

- decision_packets / decision_evidence / decision_traces / decision_factor_outputs
- manual_trades / risk_vetoes / paper_accounts / trading_orders
- market_snapshot_runs / market_snapshot_items
- scanner_runs / scanner_candidates / scanner_query_plans
- sentiment_* / policy_news_* / capital_flow_*（五维快照）
- stock_universe / stock_aliases / tasks / model_calls / schema_migrations

## 关键结论

1. **决策基础设施已有地基**（DecisionPacket/VETO/人工成交/Paper表），但**缺少统一用户隔离层**（local_user_id 未贯穿）
2. **动作枚举、仓位计算器、TradePlan 完全缺失**——阶段1核心工作
3. **Skill 未正式化**（Hermes 只有 stock-analysis，缺 15 个统一 Skill）
4. **无实盘交易链路**（QMT 适配器已禁用，符合硬边界）
5. 所有业务表**无 local_user_id** → 阶段1.2 需要数据库迁移

## 对阶段1的影响

- 1.1 TaskContext：全新实现
- 1.2 用户隔离：需新增 user_profiles/external_user_bindings/user_permissions 三表 + 迁移
- 1.3 PermissionService：全新实现
- 1.4 数据状态枚举：基于 freshness_guard 提取统一
- 1.5 Action枚举：全新
- 1.6-1.8 决策优先级/解析器/仓位：全新
- 1.9-1.10 边界/DTO：基于 orchestrator 扩展
