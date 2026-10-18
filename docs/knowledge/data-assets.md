# 数据资产与覆盖边界

**项目已经形成百万级数据与研究特征的本地积累。** 以下为 2026-10-19 只读盘点，不是安装仓库即可获得的数据包，也不是当前实时数据承诺。

| 数据对象 / 物理表 | 行数 | 已核查范围与边界 |
|---|---:|---|
| 原始结构化记录 · `data_records` | 2,793,854 | 多类业务记录；不是同数量股票或交易日 |
| 标准化历史日线 · `canonical_historical_bars` | **2,782,220** | 与原始记录存在血缘关系，不能相加 |
| 标准化财务 · `canonical_financial_records` | 33,708 | 行数不等于公司数或完整财务覆盖率 |
| 历史风险 · `historical_risk_events` | 135,347 | 风险事件行数，不能当作独立证券覆盖 |
| Replay 特征包 · `replay_feature_packets` | **2,625,560** | `trade_date`：2024-08-01—2026-08-13 |
| Strategy Lab 对齐输入 · `aligned_historical_packets` | **2,238,764** | `trade_date`：2024-08-01—2026-07-29；多张派生表共享样本 |

Strategy Lab 另保留 **202 个候选定义、808 行候选×成本指标、144 行 Walk-Forward 指标**。成本场景为 0 / 20 / 40 / 60bp。候选、成本设置和折间指标是不同计数单位；这些数字不代表同数量的有效策略或独立实验。

## 覆盖、新鲜度、路径分别核对

盘点时最新市场快照的元数据为：5,565 个有效证券，缺失 0，覆盖率 1.0，状态 `COMPLETE`；接口同时返回 **`stale=true`**，快照时间为 2026-10-16T18:02:17+09:00。完整覆盖与当前新鲜度分别判断。本次没有逐条重验全量 payload 的业务交易日，生成时间也不能代替交易日。

Replay 使用 `database/sentiment_v3_replay.duckdb`。本地另有同名根目录文件，但仅 12KB、零表；查到同名文件不足以认定读到了研究数据。未来交易日历中的日期也不构成行情或特征的实际覆盖。

## 核查方法

主库统计通过已有 Router 的 `GET /v1/internal/database-owner/consistency` 读取，未从外部连接在线主 DuckDB。该次 Owner 表级统计没有被本报告认定为跨表冻结事务快照。Replay 与研究结果库使用离线只读连接，读取表级行数和业务日期边界。

在拥有合法数据、且目标研究侧车没有写入者的环境中，可用如下查询复核对应库：

```sql
-- 在 database/sentiment_v3_replay.duckdb 的只读连接中执行
SELECT COUNT(*) AS rows, MIN(trade_date), MAX(trade_date)
FROM replay_feature_packets;

-- 在 Strategy_Lab_Results.duckdb 的只读连接中分别执行
SELECT COUNT(*) FROM candidate_definitions;
SELECT cost_bp, COUNT(*) FROM candidate_metrics GROUP BY cost_bp ORDER BY cost_bp;
SELECT COUNT(*) FROM walk_forward_fold_metrics;
```

公开的是汇总、数据字典与方法。第三方行情、原始数据库和个人状态数据保留在本地；源码许可不自动授予数据再分发权。

证据：[日期、单位与来源摘要](evidence-snapshot-2026-10-19.json) · [数据管线](../data-pipeline.md) · [Strategy Lab 数据构建](../../trading/research/strategy_lab/dataset_builder.py) · [成本评价实现](../../trading/research/strategy_lab/evaluation.py)。

[返回研究证据库](README.md)
