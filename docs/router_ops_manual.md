# Router 运维手册（AIQUANT-L / Hermes OPC）

> 适用系统：`E:\hermes-opc`（A股投研 Router）。本文所有结论均标注代码出处；未核实的写"待核实"。
> 生成日期：2026-09-28。

## 1. 启动与端口

- **标准启动命令**：`pythonw -m scripts.run_router_api`（工作目录必须是 `E:\hermes-opc`，pythonw 取自 `E:\hermes-opc\.venv\Scripts\pythonw.exe`）。
  - 出处：`E:\hermes\scripts\router_watchdog.py`（`_start_router()`，`PYW = OPC_DIR/.venv/Scripts/pythonw.exe`，`subprocess.Popen([PYW, "-m", "scripts.run_router_api"], cwd=OPC_DIR, ...)`，并带 `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` 分离启动）。
- **监听地址**：`127.0.0.1:8765`（仅本机）。
  - 出处：`scripts/run_router_api.py` 第 13–14 行 `HOST = "127.0.0.1"; PORT = 8765`；`docs/operations.md` 第 5 行 "Router: `127.0.0.1:8765`"。
- 启动时设置数据库属主环境变量：`HERMES_DB_OWNER_PROCESS=router`、`HERMES_DB_ENFORCE_OWNER=1`。
  - 出处：`scripts/run_router_api.py` main() 中 `os.environ[...]` 两行。`router/api/app.py` 第 9 行同样设置。
- 相邻服务边界：Data Hub `127.0.0.1:8766`；Finance MCP 默认本地 stdio。
  - 出处：`docs/operations.md`。

## 2. 看门狗机制（"呆猫八条"）

- **调度**：Hermes cron 任务"呆猫八条"，`*/2 * * * *`（每 2 分钟），`no_agent=true`，正常时静默。
  - 出处：`E:\hermes\cron\jobs.json`（id `7f0c09f33db6`，script `router_watchdog.py`）。
- **三层健康检查**（2026-08-30 重构版）：
  - L1 进程健康：`GET /health`（超时 5s），status != ok 即失败。
  - L2 内部就绪：`/health` 的 `database_owner`（数据库 SELECT 1）+ `/v1/roles` 核心路由（超时 8s）。
  - L3 Provider 健康：`/v1/providers/health`（Jinniu/Tushare/BaoStock 熔断器状态，超时 8s），**只记录降级，不触发重启**。
  - **重启决策**：仅当 L1/L2 连续失败（首次失败等 10 秒重试一次仍失败）才重启 Router；L3 全部 Provider 熔断打开 = 外部数据源故障，Router 本身没问题，**绝不杀 Router**（2026-09-01 用户要求 L3 降级静默）。
  - 出处：`E:\hermes\scripts\router_watchdog.py` 文件头 docstring 及 `main()`；`HEALTH_URL/ROLES_URL/PROVIDERS_URL` 常量。
- **重启动作**：通过 `netstat` 找到 `:8765 LISTENING` 的 PID → `_kill_tree()` 先杀父进程（启动器 pythonw）再杀本进程 → 用 `_start_router()` 分离式重新拉起。
  - 出处：`E:\hermes\scripts\router_watchdog.py`（`_find_router_pids` / `_kill_tree` / `_start_router`）。

## 3. 数据库锁与 `snapshot_refresh.lock` 维护锁

- **数据库属主模型**：只有 `HERMES_DB_OWNER_PROCESS=router` 的进程（即 Router）可写主库；其他进程默认只读。
  - 出处：`database/db.py` 第 313、328 行（`owner_process = os.environ.get("HERMES_DB_OWNER_PROCESS") == "router"`，`read_only=(read_only and not owner_process)`）。
- **维护锁文件**：`E:\hermes-opc\cache\snapshot_refresh.lock`，由离线快照刷新任务（`scripts/refresh_snapshot_standalone.py` 通过 `RefreshTaskLock`）写入，运行期间 `running=True` 并周期写 `last_heartbeat`。
  - 出处：`E:\hermes\scripts\router_watchdog.py` `_refresh_in_progress()`（`lock_file = OPC_DIR/"cache"/"snapshot_refresh.lock"`）；`scripts/refresh_snapshot_standalone.py` 第 54–88 行（`RefreshTaskLock` + `lock.heartbeat(...)`）。
- **为什么看门狗要读这个锁**：离线刷新进程持主库写锁期间，`HERMES_DB_OWNER_PROCESS=router` 的写操作会阻塞 Router 的 health 探测，看门狗会误判 Router 挂了。检测到 `running=True` 时**跳过重启**。
- **心跳超时规则**：锁文件心跳超过 **10 分钟（600 秒）** 未更新 → 视为崩溃残留，不视为维护中（看门狗照常处理）。
  - 出处：`E:\hermes\scripts\router_watchdog.py` `_refresh_in_progress()` 中 `> 600` 判断。
- **运维要点**：若刷新任务异常崩溃留下残留锁，可人工确认后删除/置 `running=false`（出处同上，逻辑上心跳超 10 分钟自动失效）。

## 4. Jinniu 熔断与自恢复

- **熔断器**：每个第三方 Provider 独立一个 `CircuitBreaker`，状态机 `HEALTHY → DEGRADED → OPEN → HALF_OPEN → HEALTHY`。
  - 出处：`data_hub/services/circuit_breaker.py` 文件头 docstring 与类定义。
- **参数**：默认连续失败 3 次进入 OPEN（`failure_threshold=3`）；熔断窗口 300 秒（`open_timeout_seconds=300.0`）；HALF_OPEN 放行 1 次试探请求（`success_threshold=1`），成功→恢复 HEALTHY，失败→回到 OPEN 并刷新窗口。**即熔断后约 5 分钟自动试探自恢复，无需人工干预。**
  - 出处：`data_hub/services/circuit_breaker.py` `CircuitBreaker.__init__` 默认参数。
- 设计动机：外部 Provider 连续故障时不应每请求都重撞（东财 SSL 连续故障实例：每次浪费 3~25 秒）；OPEN 状态直接短路跳过。出处：同文件 docstring。
- 熔断状态可通过 `GET /v1/providers/health` 查看。出处：`E:\hermes\scripts\router_watchdog.py` `PROVIDERS_URL`。

## 5. 数据源优先级（日线主链）

- **当前主链（jinniu_primary stage）**：`Jinniu(主) → Tushare(fallback) → BaoStock(校验)`；shadow stage 则为 `Tushare(主) → BaoStock/Jinniu(校验)`。AKShare 已移出日线主链（保留在公告/新闻等非 K 线能力）。
  - 出处：`data_hub/services/daily_bars_service.py` 文件头 docstring（"P1 主链校正"段落，读取 `historical_route_policy` 的 ROUTE_TABLE）；路由策略实现见 `data_hub/services/historical_route_policy.py`。
- 上层统一按 `ProviderOutcome` 处理：成功→使用 / 失败→fallback / 超时→fallback / 熔断→skip。出处同上。
- Jinniu 连接配置：`config/settings.py` 第 22–28 行（`jinniu_base_url=https://jinniu.lingxideai.com`、超时 30s、重试 3 次、`jinniu_history_route_enabled` 等）。

## 6. 改 data_hub 代码须重启 Router

- Router 以 uvicorn 启动且 `reload=False`，**代码修改不会热加载**，必须重启 Router 进程才能生效。
  - 出处：`scripts/run_router_api.py` `uvicorn.run(..., reload=False)`。
- 手动重启方式（择一）：
  1. 停止现有进程后，在 `E:\hermes-opc` 下运行 `pythonw -m scripts.run_router_api`（同看门狗拉起方式）。
  2. 等看门狗（每 2 分钟）自动拉起——仅适用于进程已死的场景；进程健康时看门狗不会重启它，需先手动结束 `:8765` 监听进程。
  - 出处：`E:\hermes\scripts\router_watchdog.py`（`_start_router`、`_find_router_pids`）。
- 注意：重启前确认无 `snapshot_refresh.lock` 维护任务在运行，避免打断全市场快照刷新（出处：同文件 `_refresh_in_progress()` 设计意图）。

## 7. 常用运维入口

- PowerShell 辅助脚本：`scripts\start_hermes_opc.ps1` / `status_hermes_opc.ps1` / `health_hermes_opc.ps1` / `doctor_hermes_opc.ps1` / `stop_hermes_opc.ps1`（出处：`docs/operations.md`）。
- Doctor 只打印建议，不修改配置/不回填/不删数据/不跑迁移/不改 Git 状态（出处：`docs/operations.md`）。
- 网络直连豁免（绕代理）：`config/network.py` 将 `settings.opc_no_proxy`（默认 `.eastmoney.com,.tushare.pro,.baostock.com`，见 `config/settings.py` 第 53 行）合并进 NO_PROXY。
