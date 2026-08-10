# 阶段0.3 数据库保护基线

创建时间：2026-08-01

## 备份

- 备份文件：`database/backups/hermes_opc_20260801_phase0.duckdb`
- 原库 SHA-256：`7105da25540decc9e0f31c48b1826ef6014f2048f4e81bb1ec33fee7526b84cb`
- 备份 SHA-256：`7105da25540decc9e0f31c48b1826ef6014f2048f4e81bb1ec33fee7526b84cb`（一致）
- 数据库大小：446,705,664 字节（446 MB）

## 迁移清单（schema_migrations 表，12 个）

| migration_id | applied_at |
|------|-----------|
| 0111_experiment_evaluation_v1 | 2026-07-30 12:31 |
| 0110_market_scanner_v1 | 2026-07-30 03:53 |
| 0109_five_factor_orchestration | 2026-07-30 02:22 |
| 0108_trade_date_history_shards | 2026-07-29 22:16 |
| 0107_historical_market_expansion | 2026-07-29 13:54 |
| 0106_full_market_data_foundation | 2026-07-29 11:53 |
| 0105_capital_flow_v1 | 2026-07-29 10:47 |
| 0104_policy_news_v1 | 2026-07-29 09:58 |
| （更早 4 个省略） | — |

## git 状态

- 仓库：E:\hermes-opc（git）
- 最近提交：
  - `7c02b41` Reserve disabled CITIC QMT adapter（QMT适配器已禁用——符合实盘禁止）
  - `af57f30` Add auditable trading simulation engine
  - `42c753a` Document project status and runtime
- 工作区有大量未提交修改（本阶段开始前状态）

## 测试基线

- 现有测试目录：E:\hermes-opc\tests\
- 已有测试文件（部分）：test_decision_packets.py / test_capital_flow_v1.py / test_foundation_runtime.py / test_full_market_data_foundation.py / test_fundamental_modes.py / test_manual_position_risk_reviews.py 等
