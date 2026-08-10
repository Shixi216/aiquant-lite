from __future__ import annotations

import argparse
import json
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import SecretStr

from config.settings import Settings, settings
from config.version import PROJECT_VERSION
from router.config import RouterSettings, router_settings
from router.registry import list_roles


DATA_HUB_HEALTH_URL = "http://127.0.0.1:8766/health"
ROUTER_HEALTH_URL = "http://127.0.0.1:8765/health"


@dataclass(frozen=True)
class PresenceCheck:
    variable: str
    present: bool
    required_for: tuple[str, ...]
    startup_required: bool = False


@dataclass(frozen=True)
class RoleAvailability:
    role: str
    available: bool
    reason: str


@dataclass(frozen=True)
class ServiceHealth:
    service: str
    url: str
    reachable: bool
    healthy: bool
    version: str | None = None
    registered_roles: int | None = None
    enabled_roles: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class PreflightReport:
    version: str
    database_path: str
    database_writable: bool
    database_error: str | None
    variables: tuple[PresenceCheck, ...]
    roles: tuple[RoleAvailability, ...]
    services: tuple[ServiceHealth, ...]
    registered_roles: int
    enabled_roles: int
    role_count_source: str
    fatal_errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.fatal_errors

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ok"] = self.ok
        return payload


def _has_value(value: str | SecretStr | None) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value().strip())
    return bool(value and value.strip())


def collect_presence_checks(
    app_settings: Settings = settings,
    model_settings: RouterSettings = router_settings,
) -> tuple[PresenceCheck, ...]:
    return (
        PresenceCheck(
            variable="TUSHARE_TOKEN",
            present=_has_value(app_settings.tushare_token),
            required_for=("Tushare data source",),
        ),
        PresenceCheck(
            variable="LONGCAT_API_KEY",
            present=_has_value(model_settings.longcat_api_key),
            required_for=("news_processor",),
        ),
        PresenceCheck(
            variable="DEEPSEEK_API_KEY",
            present=_has_value(model_settings.deepseek_api_key),
            required_for=("risk_controller",),
        ),
        PresenceCheck(
            variable="DASHSCOPE_API_KEY",
            present=_has_value(model_settings.qwen_api_key),
            required_for=("announcement_verifier",),
        ),
        PresenceCheck(
            variable="XIAOMI_API_KEY",
            present=_has_value(model_settings.mimo_api_key),
            required_for=("vision_reader", "complex_vision"),
        ),
    )


def collect_role_availability(
    model_settings: RouterSettings = router_settings,
) -> tuple[RoleAvailability, ...]:
    return (
        RoleAvailability(
            role="news_processor",
            available=model_settings.longcat_ready,
            reason=(
                "LongCat configuration is ready"
                if model_settings.longcat_ready
                else "LONGCAT_API_KEY is missing or LongCat configuration is incomplete"
            ),
        ),
        RoleAvailability(
            role="risk_controller",
            available=model_settings.deepseek_ready,
            reason=(
                "DeepSeek configuration is ready"
                if model_settings.deepseek_ready
                else "DEEPSEEK_API_KEY is missing or DeepSeek configuration is incomplete"
            ),
        ),
        RoleAvailability(
            role="announcement_verifier",
            available=model_settings.qwen_ready,
            reason=(
                "Qwen configuration is ready"
                if model_settings.qwen_ready
                else "DASHSCOPE_API_KEY is missing or Qwen configuration is incomplete"
            ),
        ),
        RoleAvailability(
            role="vision_reader",
            available=model_settings.mimo_ready,
            reason=(
                "MiMo configuration is ready"
                if model_settings.mimo_ready
                else "XIAOMI_API_KEY is missing or MiMo configuration is incomplete"
            ),
        ),
        RoleAvailability(
            role="complex_vision",
            available=model_settings.mimo_ready,
            reason=(
                "MiMo configuration is ready"
                if model_settings.mimo_ready
                else "XIAOMI_API_KEY is missing or MiMo configuration is incomplete"
            ),
        ),
    )


def check_database_writable(database_path: Path) -> tuple[bool, str | None]:
    try:
        resolved = database_path.expanduser().resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)

        if resolved.exists():
            if not resolved.is_file():
                return False, "database path exists but is not a file"
            with resolved.open("r+b"):
                pass

        with tempfile.NamedTemporaryFile(
            prefix=".hermes-opc-db-write-check-",
            suffix=".tmp",
            dir=resolved.parent,
            delete=True,
        ):
            pass

        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


def probe_service(service: str, url: str, timeout: float = 2.0) -> ServiceHealth:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            payload = json.load(response)
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return ServiceHealth(
            service=service,
            url=url,
            reachable=False,
            healthy=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    status = str(payload.get("status") or "")
    reported_version = (
        str(payload["version"])
        if payload.get("version") is not None
        else None
    )
    healthy = status == "ok" and reported_version == PROJECT_VERSION
    if status != "ok":
        error = f"reported status: {status or 'unknown'}"
    elif reported_version != PROJECT_VERSION:
        error = (
            f"version mismatch: expected {PROJECT_VERSION}, "
            f"reported {reported_version or 'unknown'}"
        )
    else:
        error = None

    return ServiceHealth(
        service=service,
        url=url,
        reachable=True,
        healthy=healthy,
        version=reported_version,
        registered_roles=_optional_int(payload.get("registered_roles")),
        enabled_roles=_optional_int(payload.get("enabled_roles")),
        error=error,
    )


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    return int(value) if isinstance(value, int) else None


def run_preflight(
    *,
    database_path: Path | None = None,
    require_services: bool = False,
    service_probe: Callable[[str, str], ServiceHealth] = probe_service,
    app_settings: Settings = settings,
    model_settings: RouterSettings = router_settings,
) -> PreflightReport:
    effective_database_path = database_path or app_settings.opc_database_path
    database_writable, database_error = check_database_writable(
        effective_database_path
    )
    services = (
        service_probe("Data Hub", DATA_HUB_HEALTH_URL),
        service_probe("Router", ROUTER_HEALTH_URL),
    )

    local_roles = list_roles()
    router_health = services[1]
    if (
        router_health.registered_roles is not None
        and router_health.enabled_roles is not None
    ):
        registered_roles = router_health.registered_roles
        enabled_roles = router_health.enabled_roles
        role_count_source = "Router /health"
    else:
        registered_roles = len(local_roles)
        enabled_roles = sum(1 for role in local_roles if role.enabled)
        role_count_source = "local role registry"

    fatal_errors: list[str] = []
    if not database_writable:
        fatal_errors.append("Core DuckDB path is not writable")
    if require_services:
        fatal_errors.extend(
            f"{service.service} health check failed"
            for service in services
            if not service.healthy
        )

    return PreflightReport(
        version=PROJECT_VERSION,
        database_path=str(effective_database_path.expanduser().resolve()),
        database_writable=database_writable,
        database_error=database_error,
        variables=collect_presence_checks(app_settings, model_settings),
        roles=collect_role_availability(model_settings),
        services=services,
        registered_roles=registered_roles,
        enabled_roles=enabled_roles,
        role_count_source=role_count_source,
        fatal_errors=tuple(fatal_errors),
    )


def render_report(report: PreflightReport) -> str:
    lines = [
        f"Hermes-OPC preflight {report.version}",
        (
            f"Database: {'OK' if report.database_writable else 'BLOCKED'} "
            f"({report.database_path})"
        ),
        "Environment variables:",
    ]
    for item in report.variables:
        lines.append(
            f"  [{'PRESENT' if item.present else 'MISSING'}] "
            f"{item.variable} -> {', '.join(item.required_for)}"
        )

    lines.append("Model roles:")
    for role in report.roles:
        lines.append(
            f"  [{'AVAILABLE' if role.available else 'UNAVAILABLE'}] "
            f"{role.role}: {role.reason}"
        )

    lines.append("Services:")
    for service in report.services:
        if service.healthy:
            state = "HEALTHY"
        elif service.reachable:
            state = "UNHEALTHY"
        else:
            state = "UNAVAILABLE"
        version = f", version={service.version}" if service.version else ""
        lines.append(f"  [{state}] {service.service}: {service.url}{version}")
        if service.error:
            lines.append(f"    {service.error}")

    lines.extend(
        [
            (
                f"Roles: registered={report.registered_roles}, "
                f"enabled={report.enabled_roles} "
                f"(source: {report.role_count_source})"
            ),
            f"Result: {'PASS' if report.ok else 'BLOCKED'}",
        ]
    )
    for error in report.fatal_errors:
        lines.append(f"  FATAL: {error}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Hermes-OPC startup readiness")
    parser.add_argument(
        "--require-services",
        action="store_true",
        help="Fail when Data Hub or Router is not healthy",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable status without secret values",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = run_preflight(require_services=args.require_services)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_report(report))
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()
