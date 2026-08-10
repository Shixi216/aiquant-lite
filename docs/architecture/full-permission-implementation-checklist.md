# AIQUANT-LITE 全权限决策支持升级 — 实施检查表

来源：企业微信智能文档《AIQUANT-LITE / Hermes-OPC 全权限决策支持升级任务书》
创建时间：2026-08-01
状态标记：待办 / 进行中 / 已完成 / 已验收

## 阶段0：规范固化与真实盘点

| 编号 | 需求内容 | 当前状态 | 已有实现文件 | 缺失内容 | 计划修改文件 | 需DB迁移 | 需API | 需Skill | 验收测试 | 最终结果 |
|------|---------|:---:|------------|---------|-------------|:---:|:---:|:---:|---------|---------|
| 0.1-a | 保存正式任务书 | 待办 | — | 文档 | docs/architecture/full-permission-decision-support-v1.md | 否 | 否 | 否 | 文件存在且完整 | — |
| 0.1-b | 创建实施检查表 | 待办 | — | 检查表 | docs/architecture/full-permission-implementation-checklist.md | 否 | 否 | 否 | 文件存在 | — |
| 0.2 | 只读盘点（真实代码确认） | 待办 | 多个 | 盘点报告 | docs/architecture/phase0-inventory.md | 否 | 否 | 否 | 证据齐全 | — |
| 0.3 | 数据库保护（备份+SHA256+迁移清单+git status+测试基线） | 待办 | — | 备份 | database/backups/ | 否 | 否 | 否 | 备份可恢复 | — |

## 阶段1：决策基础设施

| 编号 | 需求内容 | 当前状态 | 已有实现文件 | 缺失内容 | 计划修改文件 | 需DB迁移 | 需API | 需Skill | 验收测试 | 最终结果 |
|------|---------|:---:|------------|---------|-------------|:---:|:---:|:---:|---------|---------|
| 1.1 | 统一 TaskContext（15字段） | 待办 | — | 全部 | trading/decision_support/context.py | 否 | 否 | 否 | 单测 | — |
| 1.2 | 用户隔离基础（user_profiles/external_user_bindings/user_permissions） | 待办 | — | 全部 | database/migrations + trading/users/ | 是 | 否 | 否 | 三用户隔离测试 | — |
| 1.3 | PermissionService（LEVEL1-4） | 待办 | — | 全部 | trading/users/permission_service.py | 否 | 否 | 否 | 权限测试 | — |
| 1.4 | 数据状态统一枚举（FRESH/DEGRADED/STALE/FAILED） | 待办 | freshness_guard | 枚举统一 | trading/decision_support/data_status.py | 否 | 否 | 否 | 状态测试 | — |
| 1.5 | Action枚举（10动作） | 待办 | — | 全部 | trading/decision_support/action.py | 否 | 否 | 否 | 动作测试 | — |
| 1.6 | 决策优先级（VETO>数据状态>持仓>60/40） | 待办 | — | 全部 | trading/decision_support/priority.py | 否 | 否 | 否 | VETO覆盖测试 | — |
| 1.7 | DecisionActionResolver（阈值配置化） | 待办 | — | 全部 | trading/decision_support/action_resolver.py + config | 否 | 否 | 否 | 动作测试 | — |
| 1.8 | PositionSizer（仓位计算） | 待办 | — | 全部 | trading/decision_support/position_sizer.py | 否 | 否 | 否 | 持仓差异测试 | — |
| 1.9 | 正式评分/增强层边界（60/40不变） | 待办 | orchestrator | 增强观察字段 | trading/decision_support/ | 否 | 否 | 否 | 60/40不变测试 | — |
| 1.10 | DecisionResponse DTO | 待办 | — | 全部 | trading/decision_support/response.py | 否 | 否 | 否 | 单测 | — |

## 阶段1验收（专项测试）

| 编号 | 测试项 | 状态 | 测试文件 | 结果 |
|------|--------|:---:|---------|------|
| T1 | 动作测试（11种动作） | 待办 | tests/test_actions.py | — |
| T2 | 优先级测试（VETO覆盖/STALE禁止/FAILED停止/DEGRADED降仓/低置信） | 待办 | tests/test_priority.py | — |
| T3 | 持仓差异测试（无仓/轻仓/重仓/高成本亏损/低成本盈利） | 待办 | tests/test_position_diff.py | — |
| T4 | 用户隔离基础测试（三用户互不可见/缺local_user_id拒绝） | 待办 | tests/test_user_isolation.py | — |
| T5 | 权限测试（LEVEL1-4） | 待办 | tests/test_permissions.py | — |
| T6 | 实盘禁用测试（全项目搜索无真实订单链路） | 待办 | tests/test_live_trading_disabled.py | — |

## 本轮完成报告（20项）

1. 保存的规范文档路径
2. 当前功能真实盘点
3. 修改文件列表
4. 数据库迁移列表
5. 新增表和字段
6. TaskContext结构
7. 用户隔离实现
8. 权限矩阵
9. Action枚举
10. 动作阈值配置
11. PositionSizer逻辑
12. DecisionResponse结构
13. VETO优先级测试
14. 数据状态测试
15. 三用户隔离基础测试
16. 实盘禁用测试
17. 新增测试数
18. 全部通过数
19. 失败测试
20. 未解决问题
