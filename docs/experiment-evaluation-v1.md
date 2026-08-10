# 第10步：实验、回测与未来收益评价 v1

## 定位

本模块只用于研究评价。所有实验固定：

- `research_only=true`
- `production_weight_update=false`
- `trade_execution_enabled=false`
- `profitability_proven=false`

实验、未来收益或回测结果不会修改正式 60/40 权重、BUY/SELL/HOLD、硬风险
VETO、DecisionPacket、真实订单或 Paper Trading 账本。

## 组成

- `ExperimentDefinition`：稳定 `config_hash` 的预注册定义。
- `ExperimentRun`：代码、Git、Migration、数据库和数据集快照。
- `ExperimentObservation`：append-only 的原始信号观察。
- `ForwardReturnLabel`：与观察分离的 1/3/5/20 交易日 RAW 收益标签。
- `HistoricalReplayService`：基于 `data_available_time` 的批量 point-in-time
  价格/成交量重放。
- `PortfolioBacktestService`：研究用多股票组合聚合，不创建订单。
- `EvaluationMetricService`：信号、数据质量和切片指标。

0111 新表只承载实验和评价结构，不改 0100–0110，不写正式
`FactorOutput`。

## 未来收益口径

- 信号在收盘后公开数据可用时生成。
- 默认入场价是下一交易日开盘价。
- N 日退出价是信号日之后第 N 个交易日收盘价。
- 停牌或下一交易日无开盘价标记 `UNTRADABLE`，不自动顺延。
- 缺少退出价标记 `MISSING_EXIT`。
- 尚未到期标记 `INSUFFICIENT_FUTURE_DATA`。
- 仅使用 `RAW_PRICE_RETURN`，不混入前复权或后复权。
- 当前没有公司行动/复权因子表，因此结果保留
  `CORPORATE_ACTION_RISK`。

## point-in-time 重放

首版只使用当前历史库真实具备的字段：

- OHLC
- 成交量、成交额
- 1 日涨跌
- 5/10/20 日均线
- 5/20 日均量
- 20 日动量与突破

SQL 在每个信号日只读取当日 16:00 前已经公开的 RAW 日线，不读当前市场
快照，不用 `fetched_at` 代替公开时间，也不使用当前活跃股票池过滤历史
候选。

由于没有历史换手率、历史量比、完整历史股票池版本、指数基准、公司行动和
复权因子，重放必须标记：

- `PARTIAL_REPLAY`
- `HISTORICAL_FEATURE_UNAVAILABLE`
- `SURVIVORSHIP_BIAS_RISK`
- `CORPORATE_ACTION_RISK`

## 当前真实窗口

- 历史范围：2026-04-30 至 2026-07-29
- 交易日：61
- 最大特征回看：20 日
- 最大未来标签：20 日
- warmup：1 日
- 最低所需窗口：41 日
- 最早信号日：2026-06-01
- 1 日标签最晚信号日：2026-07-28
- 3 日标签最晚信号日：2026-07-24
- 5 日标签最晚信号日：2026-07-22
- 20 日标签最晚信号日：2026-07-01
- 同时具备完整 20 日回看和 20 日未来数据的信号日：22

这只够受控短窗口验收，不足以得出长期或稳定收益结论。

## 基准与比较

首版固定比较：

- `SCANNER_ONLY`
- `TECHNICAL_ONLY`
- `AMOUNT_TOP20`
- `MOMENTUM_20D_TOP20`
- 固定种子 `RANDOM_TOP20`
- 同一历史可用股票池的等权收益基准

正式 60/40 只接纳技术面和基本面都真实可用的完整样本。基本面缺失不会填
0，也不会改成 100% 技术面；当前覆盖不足时返回
`INSUFFICIENT_COVERAGE`。

`SHADOW_COMPOSITE` 保持 `shadow_mode=true`、
`formal_strategy_weight=0`，只作为独立研究分组。

## API

- `POST /v1/experiments`
- `GET /v1/experiments/{experiment_id}`
- `POST /v1/experiments/{experiment_id}/run`
- `GET /v1/experiment-runs/{run_id}`
- `POST /v1/evaluations/forward-returns/update`
- `GET /v1/experiment-runs/{run_id}/metrics`
- `GET /v1/experiment-runs/{run_id}/observations`
- `GET /v1/experiment-runs/{run_id}/report`
- `POST /v1/backtests/run`

写入请求默认不持久化，必须显式 `persist=true`。

## CLI

`scripts/experiment_cli.py` 支持：

- `create_experiment`
- `run_experiment`
- `replay_historical_scanner`
- `update_forward_returns`
- `run_portfolio_backtest`
- `show_experiment`
- `show_experiment_metrics`
- `export_experiment_report`
- `list_pending_labels`

写入数据库必须显式 `--apply`；导出文件必须显式 `--persist`。

本阶段不创建 Windows 计划任务，也不部署企业微信。

## 验收报告

- `reports/experiments/stage10-acceptance.json`
- `reports/experiments/stage10-acceptance.md`
- `reports/experiments/history-extension-plan.json`

报告中的收益只是带明确数据缺口和偏差风险的历史关联结果，不是交易建议，
也不是稳定盈利证明。
