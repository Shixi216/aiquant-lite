from __future__ import annotations

import os
from pathlib import Path

HOST = os.environ.get("WEBUI_HOST", "127.0.0.1")
PORT = int(os.environ.get("WEBUI_PORT", "8767"))
ROUTER_URL = os.environ.get("ROUTER_URL", "http://127.0.0.1:8765")
SERVICE_NAME = "Hermes OPC Web UI"

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIM_DAILY_ROOT = PROJECT_ROOT / "reports" / "sim_daily"
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# ---------------------------------------------------------------------------
# Whitelisted read-only sidecar databases (chart data plane).
# The WebUI must never open the main project database file directly.
# ---------------------------------------------------------------------------
REPLAY_DB_PATH = PROJECT_ROOT / "database" / "sentiment_v3_replay.duckdb"

# Temporary backup snapshot: default OFF; only used when explicitly enabled.
BACKUP_DB_PATH = PROJECT_ROOT / "database" / "hermes_opc.duckdb.bak_financial_20260831"
ENABLE_BACKUP_BAR_FALLBACK = os.environ.get(
    "WEBUI_ENABLE_BACKUP_BAR_FALLBACK", ""
) == "1"

# Dev fixtures (contract samples): default OFF and never in production.
DEV_FIXTURES = os.environ.get("WEBUI_DEV_FIXTURES", "") == "1"
HERMES_ENV = os.environ.get("HERMES_ENV", "development")
FIXTURE_MODE = DEV_FIXTURES and HERMES_ENV != "production"

FIXTURES_DIR = PROJECT_ROOT / "webui" / "dev_fixtures"

# Tail-fill provider selection: Tushare only (its domain is in NO_PROXY).
QUOTE_TAIL_TTL_SECONDS = 300
QUOTE_TAIL_MAX_DAYS = 30

# 量化面板当日快照缓存
QUANT_SNAPSHOT_TTL_SECONDS = 900
