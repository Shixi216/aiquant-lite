# 数据源故障排查手册（Hermes OPC）

> 生成日期：2026-09-28。所有结论附文件路径证据；未核实处标"待核实"。

## 1. Tushare 积分权限边界

- 系统按 **2200 积分权限**设计 Tushare 盘后主数据源分工，盘后按交易日期批量获取：`trade_cal, stock_basic, daily, daily_basic, moneyflow, stk_limit, income, balancesheet, cashflow, fina_indicator, margin_detail` 等。
  - 出处：`docs/architecture/full-permission-decision-support-v1.md` 第 296–308 行（"五、数据源正式分工 → Tushare：盘后主数据源，使用2200积分权限"）。
- **历史教训（积分/权限不足的故障模式）**：曾出现 `PERMISSION_DENIED`（22:20 任务审计）与旧验证记录（21:29 显示批量日线可用）不一致的情况；结论是"需要先确认当前凭据对应账号的接口权限或积分状态"，且积分/权限未通过门禁时 60 日批量回填不可执行。
  - 出处：`reports/history/0107.2/final-acceptance.md` 第 125–140 行（"十九、是否具备执行完整 60 日批量回填条件：否…… 二十、未解决问题 1/2"）。
- 排查建议：
  1. Token 加载自 `E:\hermes-opc\.env` 的 `TUSHARE_TOKEN`（出处：`E:\hermes\scripts\refresh_snapshot.py` 与 `run_daily_sim.py` 的 `_load_token` 逻辑）。
  2. Tushare 在日线主链中是 **fallback**（Jinniu 主 → Tushare 备 → BaoStock 校验），单点权限故障会被路由降级承接（出处：`data_hub/services/daily_bars_service.py` docstring）。
  3. 权限具体阈值（如 120/2000/5000 分对应接口清单）：**待核实**（代码中未找到完整映射表）。

## 2. 腾讯实时接口（qt.gtimg.cn）指数代码前后缀规则

- **核心规则：按交易所后缀（.SH/.SZ/.BJ）定前缀（sh/sz/bj），不能只看代码首字母。**
  - 典型坑：上证指数 `000001.SH` 首字母是 0，若按"6→sh 否则→sz"的股票规则会误判成 `sz000001`（平安银行），正确应为 `sh000001`。该 bug 于 2026-09-24 修复。
  - 出处：`data_hub/services/realtime_quote_service.py` 第 48–56 行 `_tencent_market_prefix()` docstring 及实现（`{"SH":"sh","SZ":"sz","BJ":"bj"}.get(ex, "sh")`）；第 131 行注释"修正指数代码误判"。
- 对比：**全市场快照**（tencent_snapshot）用的是另一套规则——只处理股票：6 开头→sh，4/8 开头→bj，其余→sz（该路径不涉及指数）。
  - 出处：`data_hub/providers/tencent_snapshot.py` 第 23–29 行 `_market_prefix()` 注释。
- 盘中监控脚本直接用带前缀代码请求 `http://qt.gtimg.cn/q=sz002003` 等。
  - 出处：`E:\hermes\scripts\monitor_zhangyu_000869.py`（`SYMBOL = "sz000869"`）。

## 3. Finance MCP 新闻噪声问题

- Finance MCP（`mcp_servers/finance_data/server.py`）的 `search_finance_news` 只是转发到 Router：`GET /v1/stocks/{symbol}/finance-news`（出处：`mcp_servers/finance_data/server.py` 第 139–161 行）。
- 新闻服务本体在 `data_hub/services/finance_news_service.py`，接入了 `EventClusterService` 做事件聚类（出处：该文件 import 与调用）。
- **噪声问题的具体表现/过滤阈值：待核实**——代码库与 docs 中未检索到明确的"新闻噪声"描述或投诉记录（`docs/project-status.zh-CN.md` 仅提到"新闻工具目前主要通过 AKShare 获取东方财富个股新闻""仍需长期观察接口稳定性"）。如需排查，建议：
  1. 看 `data_hub/services/finance_news_service.py` 的去重（`_content_hash`）与聚类逻辑；
  2. 检查 `event_cluster_service.py` 的聚类参数；
  3. MCP 侧本身无过滤，问题在上游数据质量。

## 4. DNS / hosts 故障模式

- **已知 hosts pin**：`np-anotice-stock.eastmoney.com → 117.185.125.154`（东财公告接口域名被 pin 到固定 IP，直连可达）。
  - 出处：`docs/tasks/20260902_zhimingda_fix_taskbook.md` 第 54 行。
  - 排查含义：若东财公告拉取失败，先检查该 hosts 条目是否仍在、该 IP 是否仍有效；IP 变更需同步更新 hosts。
- **直连（绕代理）配置**：`config/network.py` 把 `settings.opc_no_proxy`（默认 `.eastmoney.com,.tushare.pro,.baostock.com`）合并进 NO_PROXY，保证这些域不走代理（出处：`config/network.py` 第 8–25 行；`config/settings.py` 第 53 行）。
  - 排查含义：这些域名故障时先确认代理环境变量（HTTP_PROXY/NO_PROXY）没被覆盖。
- 历史上还出现过东财 SSL 连续故障（促成了熔断器的设计，每次重撞浪费 3~25 秒）——这类外部故障应表现为熔断器 OPEN 而非 Router 重启（出处：`data_hub/services/circuit_breaker.py` docstring；`E:\hermes\scripts\router_watchdog.py` L3 逻辑）。
- DNS 解析失败（getaddrinfo/ENOTFOUND）在本仓库日志中的具体案例：**待核实**（logs/ 下未检索到匹配记录）。

## 5. 通用排查路径

1. `GET http://127.0.0.1:8765/health` → 进程 + 数据库属主 SELECT 1。
2. `GET http://127.0.0.1:8765/v1/providers/health` → 各 Provider 熔断状态。
3. 检查 `E:\hermes-opc\cache\snapshot_refresh.lock` 是否维护中（running + 心跳）。
4. 看 Router 日志（uvicorn info 级，access_log 开启；出处：`scripts/run_router_api.py`）。
5. PowerShell：`scripts\doctor_hermes_opc.ps1`（只读建议，出处：`docs/operations.md`）。
