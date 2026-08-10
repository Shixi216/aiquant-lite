# 全市场异动扫描器与自然语言选股入口 v1

扫描器用于生成研究候选，不生成交易建议、正式动作、订单或仓位。

## 执行边界

- `SCREENING`：全市场本地批量扫描；网络、模型、DecisionPacket均为0。
- `RESEARCH`：最多接收30只候选，默认AI深度分析最多10只；扫描器本身不补抓。
- `DECISION`：必须显式选择股票和确认；仍由原正式技术60% + 基本面40%及硬VETO控制。
- `SENTIMENT`、`POLICY_NEWS`、`CAPITAL_FLOW`及`SHADOW_COMPOSITE`不进入正式动作。

## 查询路径

中文文本先由确定性本地解析器转换为 `ScannerQueryPlan`，再经过字段、运算符、类型和
数值白名单校验。查询不直接进入SQL，不支持任意Python表达式、函数或数据库列名。
存在单位或字段歧义时返回 `clarification_required`。

解析模型只在用户明确允许且本地解析失败时最多调用一次。模型只能返回查询计划JSON，
不能返回SQL、买卖动作、仓位或目标价；核心扫描阶段模型调用始终为0。

## 批量数据路径

冷扫描使用一个DuckDB会话和一个集合式查询，批量读取股票池、最新市场快照、行业、
最近61根RAW日线及已存因子快照。均线、RSI、MACD代理、量额比、波动率、突破和异动
在内存中批量计算。全市场阶段只保留轻量原因；详细证据只为最终10至30只候选生成。
相同数据库文件状态和 `data_cutoff` 使用有界内存缓存。

## 接口

- `POST /v1/scanner/parse`
- `POST /v1/scanner/scan`
- `GET /v1/scanner/runs/{run_id}`
- `GET /v1/scanner/runs/{run_id}/candidates`
- `GET /v1/scanner/symbols/{symbol}`
- `POST /v1/scanner/evaluate`

CLI：

```text
uv run python -m scripts.scanner_cli parse "价格10到20元，成交额大于5亿"
uv run python -m scripts.scanner_cli scan --query "放量上涨且站上20日均线" --top-n 20
uv run python -m scripts.scanner_cli benchmark --query "全A股前20只"
```

运行审计只有显式 `--persist-run` 才写入0110新增表。评价仅使用扫描时点之后的数据，
追加写入，不反向修改历史排名。

## 实测

2026-07-30同机5,534只验收：

- 冷扫描13.962秒，峰值88,454,626 bytes；
- 同快照热扫描1.624秒，峰值23,396,266 bytes；
- 冷扫描一个数据库会话、一个查询；热扫描数据库查询0；
- 网络请求0，扫描阶段模型调用0，Decision调用0。

这些结果只说明本地筛选性能和可审计性，不说明可以预测涨停或稳定提高收益。
