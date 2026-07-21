# Hermes integration

The repository includes a dry-run-first installer for Hermes 0.18. It connects the local
Finance Data MCP server, restricts it to the six documented tools, configures the WeCom home
channel, and moves the `自选股收盘日报` cron job away from generic web search.

## Preview

Run this from an existing checkout after `uv sync --dev`:

```powershell
$env:HERMES_HOME = "E:\hermes"
uv run python scripts/configure_hermes_integration.py
```

The preview prints only non-sensitive metadata. It never prints the discovered WeCom channel
value or values from Hermes `.env`.

## Apply

```powershell
uv run python scripts/configure_hermes_integration.py --apply
```

Before applying, the installer copies `config.yaml`, `.env`, and `cron/jobs.json` to a
timestamped folder under `<HERMES_HOME>/backups/`. If any update fails, all three files are
restored. Existing YAML comments and unrelated dotenv entries are preserved.

The cron job is configured with the raw MCP server name `finance_data`, which Hermes resolves
to its `mcp-finance_data` toolset. Delivery uses `wecom` and therefore requires a single
discoverable WeCom channel, or an explicit `--wecom-channel` argument.

## Verify

```powershell
hermes mcp test finance_data
hermes mcp list
hermes cron list
```

Reload MCP discovery in an interactive Hermes session with `/reload-mcp`. If the gateway was
already running when configuration changed, restart it so scheduled delivery reads the new
environment value.

Do not commit the Hermes home directory, generated backups, `.env`, databases, logs, or fetched
provider documents. The project `.gitignore` excludes the equivalent local artifacts inside
this checkout.
