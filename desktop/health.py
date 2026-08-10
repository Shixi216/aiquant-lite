from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import duckdb

from desktop.api_client import DesktopApiClient
from config.utf8 import UTF8_ENCODING, UTF8_ERRORS
from desktop.paths import AppPaths
from config.version import PROJECT_VERSION
from router.integration.health import migration_checksum_state


HEALTH_LEVELS = {
    "HEALTHY",
    "DEGRADED",
    "NOT_READY",
    "FAILED",
    "NOT_CONFIGURED",
    "NOT_SUPPORTED",
}


@dataclass(frozen=True, slots=True)
class DesktopHealth:
    checked_at: str
    overall_status: str
    values: dict[str, Any]


class DesktopHealthService:
    def __init__(
        self,
        paths: AppPaths,
        *,
        api_client: DesktopApiClient | None = None,
    ) -> None:
        self.paths = paths
        self.api_client = api_client or DesktopApiClient()

    def _working_tree_dirty(self) -> bool | None:
        if not self.paths.development:
            return None
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.paths.application_root,
                check=True,
                capture_output=True,
                text=True,
                encoding=UTF8_ENCODING,
                errors=UTF8_ERRORS,
                timeout=3,
            )
            return bool(result.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            return None

    def collect(self) -> DesktopHealth:
        now = datetime.now().astimezone()
        router = self.api_client.router_health()
        data_hub = self.api_client.data_hub_health()
        system = self.api_client.system_health()
        values: dict[str, Any] = {
            "router": "HEALTHY" if router.ok else "NOT_READY",
            "data_hub": "HEALTHY" if data_hub.ok else "NOT_READY",
            "finance_mcp": "ON_DEMAND",
            "database": "FAILED",
            "migrations": "FAILED",
            "latest_snapshot_time": None,
            "snapshot_stale": True,
            "realtime_coverage": None,
            "history_20d": None,
            "history_60d": None,
            "industry_coverage": None,
            "fundamental_coverage": None,
            "sentiment_coverage": None,
            "policy_news_coverage": None,
            "capital_flow_coverage": None,
            "formal_strategy": "TECHNICAL 60% + FUNDAMENTAL 40%",
            "formal_strategy_status": "INSUFFICIENT_COVERAGE",
            "shadow_formal_weight": 0,
            "live_trading": "NOT_SUPPORTED",
            "version": PROJECT_VERSION,
            "working_tree_dirty": self._working_tree_dirty(),
        }
        try:
            with duckdb.connect(
                str(self.paths.database_path), read_only=True
            ) as connection:
                connection.execute("SELECT 1").fetchone()
                values["database"] = "HEALTHY"
                values["data_records"] = int(
                    connection.execute("SELECT count(*) FROM data_records").fetchone()[0]
                )
                values["canonical_historical_bars"] = int(
                    connection.execute(
                        "SELECT count(*) FROM canonical_historical_bars"
                    ).fetchone()[0]
                )
        except Exception:
            pass
        try:
            values["migrations"] = (
                "HEALTHY"
                if migration_checksum_state(self.paths.database_path)["ok"]
                else "FAILED"
            )
        except Exception:
            pass
        if system.ok and system.data:
            payload = system.data.get("data", system.data)
            if isinstance(payload, dict):
                values["latest_snapshot_time"] = payload.get("latest_snapshot_time")
                values["snapshot_stale"] = payload.get("snapshot_stale", True)
                values["formal_strategy_status"] = payload.get(
                    "formal_strategy_status", "INSUFFICIENT_COVERAGE"
                )
                coverage = payload.get("research_coverage", {})
                if isinstance(coverage, dict):
                    values["fundamental_coverage"] = coverage.get(
                        "fundamental_symbols"
                    )
                    values["sentiment_coverage"] = coverage.get(
                        "sentiment_symbols"
                    )
                    values["policy_news_coverage"] = coverage.get(
                        "policy_news_symbols"
                    )
                    values["capital_flow_coverage"] = coverage.get(
                        "capital_flow_symbols"
                    )
        required_ok = (
            values["database"] == "HEALTHY"
            and values["migrations"] == "HEALTHY"
        )
        if not required_ok:
            overall = "FAILED"
        elif values["router"] != "HEALTHY" or values["data_hub"] != "HEALTHY":
            overall = "NOT_READY"
        else:
            overall = "DEGRADED"
        return DesktopHealth(now.isoformat(), overall, values)
