# aiquant-lite

`aiquant-lite` is a local-first, evidence-aware A-share research workspace. It separates
structured market data from model reasoning so that an LLM never needs to invent prices,
financial figures, or announcement metadata.

> Status: early public prototype. Research and simulated-analysis use only; it does not place
> orders and must not be treated as investment advice.

## Architecture

```text
Hermes / another MCP client
          |
          v
Finance Data MCP  ---->  Agent Router
          |                 |-- LongCat: news processing
          |                 `-- Qwen: announcement verification
          v
Local Data Hub
  |-- Tushare Pro
  |-- AKShare / public exchange sources
  `-- BaoStock
          |
          v
DuckDB audit and evidence store
```

The repository currently contains:

- a FastAPI data service for stock basics, daily bars, quotes, financial statements,
  announcements, and finance news;
- cross-source daily-bar verification and source-attributed records;
- a specialist model router with audited LongCat and Qwen pipelines;
- a Finance Data MCP server exposing exactly six approved read-only research tools;
- deterministic market-fact verification against persisted evidence.

## Finance Data MCP tools

| Tool | Purpose |
|---|---|
| `get_realtime_quote` | Return a realtime quote or a verified latest-close fallback |
| `get_daily_bars` | Fetch and cross-check daily bars from structured providers |
| `get_financial_statement` | Return the three major statements for a reporting period |
| `list_announcements` | List source-attributed CNInfo announcements |
| `search_finance_news` | Search media reports while preserving unverified status |
| `verify_market_fact` | Compare a claimed value with persisted, attributed evidence |

## Quick start

Requirements: Python 3.11 and [uv](https://docs.astral.sh/uv/).

```powershell
git clone https://github.com/Shixi216/aiquant-lite.git
cd aiquant-lite
uv sync --dev
Copy-Item .env.example .env
```

Add only the provider credentials you intend to use to `.env`. Never commit that file.

Run the REST services:

```powershell
uv run python scripts/run_data_api.py
uv run python scripts/run_router_api.py
```

Run the MCP server over stdio (the default and recommended local Hermes transport):

```powershell
uv run aiquant-lite-mcp
```

For local Streamable HTTP instead:

```powershell
$env:OPC_MCP_TRANSPORT = "streamable-http"
uv run aiquant-lite-mcp
```

The endpoint is then `http://127.0.0.1:8767/mcp`. Do not bind it to a public interface without
adding authentication and network controls.

## Verification policy

`verify_market_fact` never calls an LLM. A structured fact is verified only when at least two
independent stored sources agree within the configured tolerance. A single verified official
announcement is sufficient for facts directly contained in that announcement. Conflicts and
missing evidence are returned explicitly instead of being silently resolved.

## Security and privacy

- `.env`, credential backups, databases, runtime logs, downloaded documents, and caches are
  excluded from Git.
- `.env.example` contains names only and no usable credentials.
- MCP defaults to local-only binding and exposes a small allowlist of read-only tools.
- Provider output is treated as untrusted data; media reports are not promoted to facts.
- Before every public release, run the tests and scan the staged diff for secrets.

If you discover a security issue, follow [SECURITY.md](SECURITY.md) instead of opening a public
issue containing sensitive details.

## Development

```powershell
uv run pytest
uv run ruff check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution expectations.

## Roadmap

- connect the Finance Data MCP server to Hermes;
- add risk-level and budget-aware model routing;
- add MiMo/DeepSeek specialist roles and high-risk escalation;
- migrate scheduled reports from generic web search to the local data and evidence layer;
- collect 30-day reliability, latency, cost, coverage, and human-review metrics.

## License

MIT. See [LICENSE](LICENSE).
