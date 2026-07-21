# aiquant-lite

简体中文 | [English](README.md)

`aiquant-lite` 是一个本地优先、重视证据链的 A 股投研工作区。它将结构化市场数据与
模型推理分离，避免大语言模型凭空生成价格、财务数字或公告信息。

> 当前状态：早期开源原型。仅用于研究与模拟分析，不执行交易，也不构成投资建议。

## 架构

```text
Hermes / 其他 MCP 客户端
          |
          v
Finance Data MCP  ---->  Agent Router
          |                 |-- LongCat：新闻处理
          |                 `-- Qwen：公告核验
          v
本地 Data Hub
  |-- Tushare Pro
  |-- AKShare / 交易所公开来源
  `-- BaoStock
          |
          v
DuckDB 审计与证据存储
```

仓库目前包括：

- 股票基础信息、日线、实时行情、财务报表、公告与财经新闻的 FastAPI 数据服务；
- 跨来源日线核验和带来源标记的记录；
- 使用 LongCat、Qwen 的专业模型路由与审计流水线；
- 仅开放六个只读研究工具的 Finance Data MCP 服务；
- 基于已持久化证据的确定性市场事实核验；
- 支持备份、回滚、MCP 白名单和企业微信定时任务迁移的 Hermes 安装器；
- 可审计的风险等级与模型调用预算控制。

## Finance Data MCP 工具

| 工具 | 用途 |
|---|---|
| `get_realtime_quote` | 返回 A 股实时行情，或经过核验的最近收盘价回退结果 |
| `get_daily_bars` | 获取并交叉核对多个结构化来源的日线数据 |
| `get_financial_statement` | 返回指定报告期的三大财务报表 |
| `list_announcements` | 列出带来源信息的巨潮资讯公告 |
| `search_finance_news` | 搜索财经媒体报道，并保留“未经核验”状态 |
| `verify_market_fact` | 将一个事实声明与已保存、可追溯的证据进行比较 |

## 快速开始

环境要求：Python 3.11 和 [uv](https://docs.astral.sh/uv/)。

```powershell
git clone https://github.com/Shixi216/aiquant-lite.git
cd aiquant-lite
uv sync --dev
Copy-Item .env.example .env
```

只在 `.env` 中填写你实际使用的数据源或模型凭据，绝对不要提交该文件。

启动 REST 服务：

```powershell
uv run python scripts/run_data_api.py
uv run python scripts/run_router_api.py
```

通过 stdio 启动 MCP 服务，这是本地 Hermes 集成的默认推荐方式：

```powershell
uv run aiquant-lite-mcp
```

先预览、再连接现有 Hermes 0.18 安装：

```powershell
$env:HERMES_HOME = "E:\hermes"
uv run python scripts/configure_hermes_integration.py
uv run python scripts/configure_hermes_integration.py --apply
```

备份、验证与投递说明见 [Hermes 集成文档](docs/hermes-integration.md)。Hermes 本机配置和
凭据始终保留在本仓库之外。

如需在本机启用 Streamable HTTP：

```powershell
$env:OPC_MCP_TRANSPORT = "streamable-http"
uv run aiquant-lite-mcp
```

此时端点为 `http://127.0.0.1:8767/mcp`。在没有认证和网络访问控制的情况下，不要将其
绑定到公网接口。

## 核验策略

`verify_market_fact` 不调用大语言模型。只有至少两个相互独立的已存储来源在允许误差内
一致时，结构化事实才会被判定为已核验。对于直接来自官方公告的事实，一份经过核验的
官方公告即可作为充分证据。证据不足或来源冲突时，系统会明确返回对应状态，不会静默
选择一个结果。

## 风险与预算路由

通用 Router 请求支持以下参数：

- `risk_level`：`low`、`medium`、`high` 或 `critical`；
- `budget_tier`：`economy`、`standard` 或 `premium`。

策略完全确定，并在任何模型调用之前执行：

- 预算等级限制单次输出 Token 数和物理模型调用总次数；
- 风险等级限制采样温度；
- `high` 和 `critical` 结果会标记为需要人工复核，并在 `risk_controller` 可用后请求升级；
- 不兼容的组合会提前拒绝，例如 `critical` 风险不能使用 `standard` 预算，从而避免消耗
  Provider 配额或凭据资源。

最终决策通过 `RouterInvokeResponse.routing` 返回，并和 Agent 结果一起写入审计记录。
可通过 `GET /v1/routing/policy` 查看不含凭据的公开策略矩阵。

## 安全与隐私

- `.env`、凭据备份、数据库、运行日志、下载的文档和缓存均被 Git 排除；
- `.env.example` 只提供变量名，不包含可用凭据；
- MCP 默认仅绑定本机，并只暴露小型只读工具白名单；
- Provider 输出一律视为不可信输入，媒体报道不会被自动升级为事实；
- 每次公开发布前都应运行测试，并扫描暂存差异中的敏感信息。

如发现安全问题，请按照 [SECURITY.md](SECURITY.md) 的说明私下报告，不要创建包含敏感
细节的公开 Issue。

## 开发

```powershell
uv run pytest
uv run ruff check .
```

贡献规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 路线图

- 接入 MiMo/DeepSeek 专业角色与高风险自动升级；
- 对迁移后的定时日报进行完整交易周验证；
- 累积 30 天可靠性、延迟、成本、覆盖率和人工复核指标。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
