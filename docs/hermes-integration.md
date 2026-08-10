# Hermes integration

## Stage 11 tool and workflow contract

The approved Finance MCP toolset now covers stock basics, the persisted market snapshot, quotes,
historical daily bars, financial statements, announcements, finance news, fact verification,
scanner parse and scan, bounded candidate research, one explicitly confirmed decision
evaluation, and existing experiment reports. Tool schemas and risk metadata are available at
`GET /v1/integration/tools`.

The intended chain is deterministic:

1. parse natural language into `ScannerQueryPlan`;
2. confirm the interpreted conditions;
3. scan locally for 10–30 research candidates;
4. explicitly select no more than 30 symbols for research; deep analysis is capped at ten;
5. explicitly select one symbol and send `CONFIRM_DECISION:<symbol>`;
6. show formal 60/40 and shadow results separately, with hard VETO final.

Scanner output never enters DECISION automatically. The decision tool creates no order and, in
this integration workflow, no `DecisionPacket`. Raw SQL and arbitrary table access are not MCP
capabilities. WeCom delivery is not deployed by this stage; only the local adapter described in
`docs/wecom-integration.md` is verified.

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

## Router 0.10.0 non-live API calls

Hermes jobs that call the Router directly should use the classified namespaces:

- decision support: `POST /v1/decisions/from-data`;
- backtests and Paper Trading: `/v1/simulations/*`;
- daily reviews: `/v1/reviews/*`.

For example, a structured daily review can be requested without using a legacy route:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/v1/reviews/daily" `
  -ContentType "application/json" `
  -Body '{"date":"2026-07-27"}'
```

The local `scripts/generate_daily_review.py` entry point uses the same `ReviewService` as both the
new and deprecated HTTP routes. Existing `/v1/trading/*` calls receive `Deprecation` and `Sunset`
headers and should be migrated before the next Router minor version.

## Manual-trade chat boundary

Hermes may normalize a message such as "I bought this stock externally" into a manual-trade
candidate, but it must call `POST /v1/manual-trades/previews` first. The preview response is not a
ledger entry. Hermes must show the standardized payload and ask the same authenticated user to
reply exactly:

```text
确认录入 <confirmation_id>
```

Only that explicit reply may call the confirmation endpoint. The Router compares the
`X-Authenticated-User` and `X-Authenticated-Channel` identity asserted by the trusted local
gateway with the preview creator. Decision agents, risk agents, Paper Trading, and cron jobs must
never call the confirmation endpoint. Keep the Router local; these identity headers are a trusted
gateway boundary and are not a substitute for public-network authentication.

## Manual-position risk review boundary

After a manual trade has been confirmed by the user, Hermes may request an advisory position
risk review with:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://127.0.0.1:8765/v1/manual-positions/<position_id>/risk-reviews" `
  -Headers @{
    "X-Authenticated-User" = "<authenticated-user>"
    "X-Authenticated-Channel" = "<trusted-channel>"
  } `
  -ContentType "application/json" `
  -Body '{"lookback_days":365,"allow_model_review":true}'
```

This call refreshes research evidence and appends an advisory audit. It cannot create or confirm
a manual trade, change the manual-position projection, write the simulation account, or invoke an
external trading application. Scheduled daily reviews may read these audit records, but must not
call the manual-trade confirmation endpoint.
