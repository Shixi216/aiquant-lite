# Cron 任务盘点（Hermes 调度器）

> 数据来源：`E:\hermes\cron\jobs.json`（jobs 定义）。所有任务 `no_agent=true`（纯脚本，不经 LLM）。快照时间：2026-09-28。
> 脚本分两层：hermes 侧启动器（`E:\hermes\scripts\`，负责环境/token 加载）转发到 hermes-opc 真实脚本（`E:\hermes-opc\scripts\`）。

## 一、Router / 基础设施

| 任务名 | 频率(cron) | 作用 | 输出位置 | 证据 |
|---|---|---|---|---|
| 呆猫八条 | `*/2 * * * *` | Router 三层健康看门狗（L1 /health、L2 数据库+/v1/roles、L3 Provider 熔断），L1/L2 连续失败自动重启 Router，正常静默 | 正常静默；异常时推送（origin: wecom） | `E:\hermes\cron\jobs.json` id `7f0c09f33db6`；`E:\hermes\scripts\router_watchdog.py` |
| 全市场快照定时刷新 | `0 16 * * 1-5` | 盘后全市场快照刷新（无人值守） | 本地（deliver: local），写入主库；持 `cache/snapshot_refresh.lock` | jobs.json id `a26aaaca1380`；`E:\hermes\scripts\refresh_snapshot.py` → `E:\hermes-opc\scripts\refresh_snapshot_standalone.py` |

## 二、模拟盘日内系列（均执行 `run_daily_sim.py`，按运行时刻自动判定时点）

启动器 `E:\hermes\scripts\run_daily_sim.py` → 真实脚本 `E:\hermes-opc\scripts\daily_sim_snapshot.py`，报告目录 `E:\hermes-opc\reports\sim_daily\`（证据：`daily_sim_snapshot.py` 第 30 行 `REPORTS_DIR = .../reports/sim_daily`）。全部 `deliver: origin`（wecom 推送，origin ZhangTianYi）。

| 任务名 | 频率 | 对应时点 |
|---|---|---|
| 模拟盘-0800盘前准备 | `0 8 * * 1-5` | pre |
| 模拟盘-V1盘前冻结 | `10 9 * * 1-5` | v1 |
| 模拟盘-0915竞价观察 | `15 9 * * 1-5` | bid |
| 模拟盘-V2竞价后 | `25 9 * * 1-5` | v2 |
| 模拟盘-V3最5分钟 | `35 9 * * 1-5` | v3 |
| 模拟盘-盘中复核1000 | `0 10 * * 1-5` | c10 |
| 模拟盘-趋势复核1030 | `30 10 * * 1-5` | c1030 |
| 模拟盘-上午收盘快照 | `30 11 * * 1-5` | am |
| 模拟盘-午后最5分钟 | `5 13 * * 1-5` | pm |
| 模拟盘-午后趋势复核1400 | `0 14 * * 1-5` | pm14 |
| 模拟盘-尾盘复核1430 | `30 14 * * 1-5` | pm1430 |
| 模拟盘-尾盘冻结1450 | `50 14 * * 1-5` | vfinal |
| 模拟盘-收盘快照1500 | `0 15 * * 1-5` | close |
| 模拟盘-盘后复盘1530 | `30 15 * * 1-5` | review |

证据：jobs.json 各 id（`64782c842e32`…`85f4d92a4370`）；时点映射见 `E:\hermes\scripts\run_daily_sim.py` 的 `TIMEPOINT_BY_TIME`。

## 三、选股 / 推送

| 任务名 | 频率 | 作用 | 输出位置 | 证据 |
|---|---|---|---|---|
| 每日好股自动推送 | `30 8 * * 1-5` | 盘前模式：screen_top5(全市场动量)+sector_rotate --force-eod，用前一交易日定稿数据；硬过滤（四维≥0.22 且正式动作为建仓类 且非禁止追高）；无达标票静默 | 推送 feishu（origin oc_413f...）；cron 正式调用写"当日已推送"状态文件做去重 | jobs.json id `61f97c806f29`；`E:\hermes\scripts\daily_good_picks.py` 文件头 |
| 盘中好股自动推送 | `30 10 * * 1-5` | 盘中模式：sector_rotate --force-live（腾讯实时板块），当日去重，无票静默 | 同上 | jobs.json id `3814ebfeab74`；同脚本 |
| 候选票价格监控 | `*/30 9-14 * * 1-5` | 基于 analyze_one 的建仓区间/止损价监控候选股（华微电子/昭衍新药/金域医学/双星新材），无触发静默 | 触发时推送 feishu | jobs.json id `0065fc00af96`；`E:\hermes\scripts\watch_candidates.py` |

## 四、个股盘中监控（无 LLM，脚本直接输出推送，feishu）

| 任务名 | 频率 | 作用 | 证据 |
|---|---|---|---|
| 伟星股份盘中监控 | `*/10 9-14 * * 1-5` | 002003：进入建仓区[10.97,11.25]/跌破止损9.69/突破前高12.23 触发提醒，qt.gtimg.cn 实时价 | jobs.json id `800ca1672d0a`；`E:\hermes\scripts\monitor_weixing_002003.py` |
| 张裕A监控 | `*/10 9-14 * * 1-5` | 000869：回踩[15.85-16.25]可进 / 跌破15.77止损 | jobs.json id `5d54db87a64d`；`E:\hermes\scripts\monitor_zhangyu_000869.py` |

## 备注

- `E:\hermes\scripts\` 下还有未列入 cron 的脚本（`monitor_duofuoduo.py`、`monitor_jnhg.py`、`monitor_mklp.py`、`monitor_myrs.py`、`monitor_xldz.py`、`monitor_xldz2.py`、`jiheng_position_monitor.py`、`daily_sim_snapshot.py` 等），属历史/手动脚本，当前 jobs.json 中未启用。
- "已推送"去重状态文件：`E:\hermes\state\pushed_picks_{日期}.json`，仅 cron 正式调用写入（手动跑不污染，防 2026-09-21 事故复发）。证据：`E:\hermes\scripts\daily_good_picks.py` 第 20、48、71–72 行。
- 管理入口：jobs.json 由 Hermes 调度器维护，`E:\hermes\cron\output\` 存放任务输出（待核实具体结构）。
