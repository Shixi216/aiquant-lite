from __future__ import annotations

from database.db import open_database

import importlib
import os
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from config.settings import PROJECT_ROOT, settings
from router.config import router_settings
from router.integration.schemas import (
    ComponentHealth,
    DoctorReport,
    HealthStatus,
    SystemHealth,
)


MIGRATION_MODULES = tuple(
    f"database.migrations.v{number:04d}_{suffix}"
    for number, suffix in (
        (100, "unified_data"),
        (101, "backfill_audit"),
        (102, "fundamental_point_in_time"),
        (103, "sentiment_v1"),
        (104, "policy_news_v1"),
        (105, "capital_flow_v1"),
        (106, "full_market_data_foundation"),
        (107, "historical_market_expansion"),
        (108, "trade_date_history_shards"),
        (109, "five_factor_orchestration"),
        (110, "market_scanner_v1"),
        (111, "experiment_evaluation_v1"),
    )
)


def migration_checksum_state(database_path: Path | None = None) -> dict[str, Any]:
    expected: dict[str, str] = {}
    for module_name in MIGRATION_MODULES:
        module = importlib.import_module(module_name)
        expected[str(module.MIGRATION_ID)] = str(module._checksum())
    path = database_path or settings.opc_database_path
    with open_database(str(path), read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT migration_id, checksum
            FROM schema_migrations
            WHERE migration_id >= '0100_' AND migration_id < '0112_'
            ORDER BY migration_id
            """
        ).fetchall()
    actual = {str(row[0]): str(row[1]) for row in rows}
    mismatches = sorted(
        migration_id
        for migration_id, checksum in expected.items()
        if actual.get(migration_id) != checksum
    )
    unexpected = sorted(set(actual) - set(expected))
    return {
        "ok": not mismatches and not unexpected and len(actual) == 12,
        "expected_count": len(expected),
        "applied_count": len(actual),
        "mismatch_ids": mismatches,
        "unexpected_ids": unexpected,
    }


def _database_state() -> tuple[bool, datetime | None, dict[str, int]]:
    # Router repositories use DuckDB's default connection configuration.
    # Opening the same file with a different read_only configuration in the
    # same process can fail even though every statement below is read-only.
    with open_database(str(settings.opc_database_path)) as connection:
        if connection.execute("SELECT 1").fetchone() != (1,):
            return False, None, {}
        row = connection.execute(
            "SELECT max(snapshot_time) FROM market_snapshot_runs"
        ).fetchone()
        coverage = {
            "fundamental_symbols": int(
                connection.execute(
                    "SELECT count(DISTINCT symbol) FROM canonical_financial_records"
                ).fetchone()[0]
            ),
            "sentiment_symbols": int(
                connection.execute(
                    "SELECT count(DISTINCT symbol) FROM sentiment_symbol_snapshots"
                ).fetchone()[0]
            ),
            "policy_news_symbols": int(
                connection.execute(
                    "SELECT count(DISTINCT symbol) FROM policy_news_symbol_snapshots"
                ).fetchone()[0]
            ),
            "capital_flow_symbols": int(
                connection.execute(
                    "SELECT count(DISTINCT symbol) FROM capital_flow_symbol_snapshots"
                ).fetchone()[0]
            ),
        }
    return True, None if row is None else row[0], coverage


def _data_hub_reachable() -> bool:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("http://127.0.0.1:8766/health", timeout=1.5) as response:
            payload = __import__("json").load(response)
        return payload.get("status") == "ok"
    except (
        OSError,
        TimeoutError,
        urllib.error.URLError,
        ValueError,
    ):
        return False


def _wecom_configured() -> bool:
    channel = bool((os.getenv("WECOM_HOME_CHANNEL") or "").strip())
    credential = any(
        bool((os.getenv(name) or "").strip())
        for name in ("WECOM_SECRET", "WECOM_WEBHOOK", "WECOM_ACCESS_TOKEN")
    )
    return channel and credential


class SystemHealthService:
    def collect(self, *, now: datetime | None = None) -> SystemHealth:
        checked_at = now or datetime.now().astimezone()
        env_file_exists = (PROJECT_ROOT / ".env").is_file()
        components: list[ComponentHealth] = [
            ComponentHealth(
                name="router",
                status=HealthStatus.HEALTHY,
                detail="local API process is responding",
                required=True,
            )
        ]
        try:
            database_ok, snapshot_time, coverage = _database_state()
        except Exception:
            database_ok, snapshot_time, coverage = False, None, {}
        components.append(
            ComponentHealth(
                name="database",
                status=(
                    HealthStatus.HEALTHY
                    if database_ok
                    else HealthStatus.FAILED
                ),
                detail=(
                    "DuckDB read-only query succeeded"
                    if database_ok
                    else "DuckDB read-only query failed"
                ),
                required=True,
            )
        )
        try:
            migrations_ok = bool(migration_checksum_state()["ok"])
        except Exception:
            migrations_ok = False
        components.append(
            ComponentHealth(
                name="migrations",
                status=(
                    HealthStatus.HEALTHY
                    if migrations_ok
                    else HealthStatus.FAILED
                ),
                detail=(
                    "0100-0111 checksums match"
                    if migrations_ok
                    else "0100-0111 checksum validation failed"
                ),
                required=True,
            )
        )
        data_hub_ok = _data_hub_reachable()
        components.append(
            ComponentHealth(
                name="data_hub",
                status=(
                    HealthStatus.HEALTHY
                    if data_hub_ok
                    else HealthStatus.NOT_READY
                ),
                detail=(
                    "port 8766 health endpoint is ready"
                    if data_hub_ok
                    else "port 8766 health endpoint is not ready"
                ),
                required=True,
            )
        )
        components.append(
            ComponentHealth(
                name="finance_mcp",
                status=HealthStatus.HEALTHY,
                detail="local stdio tool registry is available",
                required=False,
            )
        )
        stale = (
            snapshot_time is None
            or snapshot_time < checked_at - timedelta(minutes=15)
        )
        components.append(
            ComponentHealth(
                name="scanner",
                status=(
                    HealthStatus.DEGRADED if stale else HealthStatus.HEALTHY
                ),
                detail=(
                    "latest market snapshot is stale or missing"
                    if stale
                    else "latest market snapshot is fresh"
                ),
                required=False,
            )
        )
        components.append(
            ComponentHealth(
                name="formal_evaluation",
                status=HealthStatus.DEGRADED,
                detail="formal 60/40 evaluation has insufficient fundamental coverage",
                required=False,
            )
        )
        for name, key in (
            ("fundamental_coverage", "fundamental_symbols"),
            ("sentiment_coverage", "sentiment_symbols"),
            ("policy_news_coverage", "policy_news_symbols"),
            ("capital_flow_coverage", "capital_flow_symbols"),
        ):
            count = coverage.get(key, 0)
            components.append(
                ComponentHealth(
                    name=name,
                    status=(
                        HealthStatus.HEALTHY
                        if count >= 5000
                        else HealthStatus.DEGRADED
                    ),
                    detail=f"persisted symbol coverage count={count}",
                    required=False,
                )
            )
        wecom_ready = _wecom_configured()
        components.append(
            ComponentHealth(
                name="wecom",
                status=(
                    HealthStatus.HEALTHY
                    if wecom_ready
                    else HealthStatus.NOT_READY
                ),
                detail=(
                    "credentials are configured; delivery remains disabled"
                    if wecom_ready
                    else "NOT_CONFIGURED; local simulation remains available"
                ),
                required=False,
            )
        )
        components.append(
            ComponentHealth(
                name="live_trading",
                status=HealthStatus.NOT_READY,
                detail="NOT_SUPPORTED",
                required=False,
            )
        )
        if not database_ok or not migrations_ok:
            overall = HealthStatus.FAILED
        elif not data_hub_ok:
            overall = HealthStatus.NOT_READY
        elif stale:
            overall = HealthStatus.DEGRADED
        else:
            overall = HealthStatus.DEGRADED
        provider_configured = {
            "tushare": bool((settings.tushare_token or "").strip()),
            "longcat": router_settings.longcat_ready,
            "deepseek": router_settings.deepseek_ready,
            "qwen": router_settings.qwen_ready,
            "mimo": router_settings.mimo_ready,
            "wecom": wecom_ready,
        }
        return SystemHealth(
            overall_status=overall,
            components=components,
            database_read_only_ok=database_ok,
            migration_checksums_ok=migrations_ok,
            env_file_exists=env_file_exists,
            provider_configured=provider_configured,
            research_coverage=coverage,
            disk_free_bytes=shutil.disk_usage(PROJECT_ROOT).free,
            latest_snapshot_time=snapshot_time,
            snapshot_stale=stale,
            formal_strategy_status="INSUFFICIENT_COVERAGE",
        )

    def doctor(self) -> DoctorReport:
        health = self.collect()
        recommendations: list[str] = []
        status_by_name = {
            component.name: component.status
            for component in health.components
        }
        if status_by_name["data_hub"] == HealthStatus.NOT_READY:
            recommendations.append(
                "Start the Data Hub on 127.0.0.1:8766 and rerun health."
            )
        if health.snapshot_stale:
            recommendations.append(
                "Refresh the market snapshot before interpreting scanner rankings."
            )
        if not status_by_name["wecom"] == HealthStatus.HEALTHY:
            recommendations.append(
                "Configure WeCom only if message delivery is required; simulation is available."
            )
        recommendations.append(
            "Increase point-in-time fundamental coverage before formal 60/40 evaluation."
        )
        return DoctorReport(
            health=health,
            recommendations=recommendations,
        )


__all__ = [
    "MIGRATION_MODULES",
    "SystemHealthService",
    "migration_checksum_state",
]
