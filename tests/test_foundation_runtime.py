from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from config.settings import Settings
from config.version import (
    PROJECT_VERSION,
    ROUTER_GENERATOR_VERSION,
    read_project_version,
)
from data_hub.api.app import SERVICE_VERSION as DATA_HUB_VERSION
from router.api.app import SERVICE_VERSION as ROUTER_VERSION
from router.api.app import app
from router.config import RouterSettings
from scripts.preflight_check import (
    ServiceHealth,
    collect_presence_checks,
    render_report,
    run_preflight,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _offline_service_probe(service: str, url: str) -> ServiceHealth:
    return ServiceHealth(
        service=service,
        url=url,
        reachable=False,
        healthy=False,
        error="offline test",
    )


def _healthy_service_probe(service: str, url: str) -> ServiceHealth:
    if service == "Router":
        return ServiceHealth(
            service=service,
            url=url,
            reachable=True,
            healthy=True,
            version=PROJECT_VERSION,
            registered_roles=6,
            enabled_roles=2,
        )
    return ServiceHealth(
        service=service,
        url=url,
        reachable=True,
        healthy=True,
        version=PROJECT_VERSION,
    )


def test_pyproject_is_the_single_runtime_version_source(tmp_path: Path) -> None:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
        project_document = tomllib.load(pyproject_file)

    assert project_document["project"]["version"] == "0.10.0"
    assert PROJECT_VERSION == "0.10.0"
    assert ROUTER_VERSION == PROJECT_VERSION
    assert DATA_HUB_VERSION == PROJECT_VERSION
    assert app.version == PROJECT_VERSION
    assert app.openapi()["info"]["version"] == PROJECT_VERSION
    assert ROUTER_GENERATOR_VERSION == f"router-{PROJECT_VERSION}"

    alternate = tmp_path / "pyproject.toml"
    alternate.write_text(
        '[project]\nname = "version-test"\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    assert read_project_version(alternate) == "9.8.7"


def test_preflight_never_exposes_secret_values(tmp_path: Path) -> None:
    secret_value = "do-not-print-this-secret"
    app_settings = Settings(
        _env_file=None,
        tushare_token=secret_value,
        opc_database_path=tmp_path / "preflight.duckdb",
    )
    model_settings = RouterSettings(
        _env_file=None,
        LONGCAT_API_KEY=secret_value,
        DEEPSEEK_API_KEY=secret_value,
        DASHSCOPE_API_KEY=secret_value,
        DASHSCOPE_BASE_URL="https://qwen.example.invalid/v1",
        XIAOMI_API_KEY=secret_value,
        XIAOMI_BASE_URL="https://mimo.example.invalid/v1",
    )

    report = run_preflight(
        service_probe=_healthy_service_probe,
        app_settings=app_settings,
        model_settings=model_settings,
    )
    rendered = render_report(report)

    assert report.ok is True
    assert all(item.present for item in report.variables)
    assert secret_value not in rendered
    assert secret_value not in str(report.to_dict())
    assert report.registered_roles == 6
    assert report.enabled_roles == 2
    assert report.role_count_source == "Router /health"


def test_missing_model_keys_do_not_block_local_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 清空模型密钥和数据源令牌，纯规则本地启动不应依赖外部密钥。
    for var in [
        "OPC_LONGCAT_API_KEY", "LONGCAT_API_KEY", "LONGCAT_KEY",
        "OPC_QWEN_API_KEY", "QWEN_API_KEY", "DASHSCOPE_API_KEY",
        "OPC_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY",
        "OPC_MIMO_API_KEY", "MIMO_API_KEY",
        "OPC_LONGCAT_BASE_URL", "LONGCAT_BASE_URL",
        "OPC_LONGCAT_MODEL", "LONGCAT_MODEL",
    ]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    app_settings = Settings(
        _env_file=None,
        opc_database_path=tmp_path / "local-only.duckdb",
    )
    model_settings = RouterSettings(_env_file=None)

    report = run_preflight(
        service_probe=_offline_service_probe,
        app_settings=app_settings,
        model_settings=model_settings,
    )

    assert report.ok is True
    assert report.database_writable is True
    # 模型相关变量应缺失（无 key 也能启动）
    model_vars = [
        item for item in report.variables
        if "API_KEY" in item.variable and item.variable != "TUSHARE_TOKEN"
    ]
    assert not any(item.present for item in model_vars), (
        f"模型变量应为缺失: {[v.variable for v in model_vars if v.present]}"
    )
    # 模型角色不可用（无 key）
    assert not any(role.available for role in report.roles)
    # Tushare 令牌仅在实际调用数据源时需要，不应阻塞纯规则启动。
    tushare = next(item for item in report.variables if item.variable == "TUSHARE_TOKEN")
    assert tushare.present is False
    assert not any("TUSHARE_TOKEN" in error for error in report.fatal_errors)


def test_unhealthy_services_only_block_when_explicitly_required(
    tmp_path: Path,
) -> None:
    app_settings = Settings(
        _env_file=None,
        opc_database_path=tmp_path / "service-check.duckdb",
    )
    model_settings = RouterSettings(_env_file=None)

    report = run_preflight(
        require_services=True,
        service_probe=_offline_service_probe,
        app_settings=app_settings,
        model_settings=model_settings,
    )

    assert report.ok is False
    assert "Data Hub health check failed" in report.fatal_errors
    assert "Router health check failed" in report.fatal_errors


def test_database_failure_is_always_fatal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "scripts.preflight_check.check_database_writable",
        lambda _: (False, "permission denied"),
    )
    app_settings = Settings(
        _env_file=None,
        opc_database_path=tmp_path / "blocked.duckdb",
    )

    report = run_preflight(
        service_probe=_offline_service_probe,
        app_settings=app_settings,
        model_settings=RouterSettings(_env_file=None),
    )

    assert report.ok is False
    assert report.database_writable is False
    assert report.fatal_errors == ("Core DuckDB path is not writable",)


def test_presence_check_uses_only_requested_public_names() -> None:
    checks = collect_presence_checks(
        Settings(_env_file=None),
        RouterSettings(_env_file=None),
    )

    assert {item.variable for item in checks} == {
        "TUSHARE_TOKEN",
        "LONGCAT_API_KEY",
        "DEEPSEEK_API_KEY",
        "DASHSCOPE_API_KEY",
        "XIAOMI_API_KEY",
    }


def test_windows_start_script_uses_module_mode_and_preflight() -> None:
    source = (PROJECT_ROOT / "scripts" / "start_hermes_opc.ps1").read_text(
        encoding="utf-8"
    )

    assert 'ProjectRoot = "E:\\hermes-opc"' in source
    assert '".uv-cache"' in source
    assert "scripts.preflight_check" in source
    assert '"scripts.run_data_api"' in source
    assert '"scripts.run_router_api"' in source
    assert "http://127.0.0.1:8766/health" in source
    assert "http://127.0.0.1:8765/health" in source
    assert "scripts/run_data_api.py" not in source
    assert "scripts/run_router_api.py" not in source


def test_windows_stop_script_only_uses_recorded_processes() -> None:
    source = (PROJECT_ROOT / "scripts" / "stop_hermes_opc.ps1").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()

    assert "hermes_opc_state.json" in source
    assert "pid_start_time" in source
    assert "Stop-Process" in source
    assert "taskkill" not in lowered
    assert "/im" not in lowered
    assert "python.exe" not in lowered
    assert "pythonw.exe" not in lowered


def test_windows_status_script_reports_health_version_and_roles() -> None:
    source = (PROJECT_ROOT / "scripts" / "status_hermes_opc.ps1").read_text(
        encoding="utf-8"
    )

    assert "Data Hub" in source
    assert "Router" in source
    assert "PID:" in source
    assert "Port:" in source
    assert "Health:" in source
    assert "Version:" in source
    assert "/v1/roles" in source
    assert "Enabled model role names:" in source
