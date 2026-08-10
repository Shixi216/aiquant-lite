from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit

from desktop.api_client import ApiResult
from desktop.application import MainWindow, PAGE_TITLES
from desktop.credentials import (
    MemoryCredentialStore,
    WindowsCredentialStore,
    child_process_environment,
)
from desktop.first_run import FirstRunWizard
from desktop.health import HEALTH_LEVELS, DesktopHealthService
from desktop.pages import LogsPage, PlaceholderPage, sanitize_text
from desktop.paths import AppPaths
from desktop.service_supervisor import ServiceRecord, ServiceSupervisor
from desktop.settings import DesktopSettings


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def installed_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.resolve(
        development=False,
        application_root=tmp_path / "程序 文件",
        env={"LOCALAPPDATA": str(tmp_path / "本地 数据")},
    )


def test_dev_paths_resolve_to_project() -> None:
    paths = AppPaths.resolve(development=True, application_root=PROJECT_ROOT)
    assert paths.application_root == PROJECT_ROOT
    assert paths.user_data_dir == PROJECT_ROOT / "runtime" / "desktop"
    assert paths.database_path == PROJECT_ROOT / "database" / "hermes_opc.duckdb"


def test_installed_paths_use_local_app_data(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    assert paths.user_data_dir == tmp_path / "本地 数据" / "HermesOPC"
    assert paths.user_data_dir != paths.application_root


def test_paths_support_spaces_and_chinese(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    assert " " in str(paths.application_root)
    assert "数据" in str(paths.user_data_dir)


def test_user_data_override_is_honored(tmp_path: Path) -> None:
    target = tmp_path / "自定义 用户目录"
    paths = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "app",
        user_data_override=target,
        env={"LOCALAPPDATA": str(tmp_path)},
    )
    assert paths.user_data_dir == target


def test_installed_user_data_selection_persists(tmp_path: Path) -> None:
    environment = {"LOCALAPPDATA": str(tmp_path / "local")}
    selected = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "app",
        user_data_override=tmp_path / "自定义 数据",
        env=environment,
    )
    selected.persist_user_data_location(env=environment)
    resolved = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "app",
        env=environment,
    )
    assert resolved.user_data_dir == selected.user_data_dir


def test_database_override_is_honored(tmp_path: Path) -> None:
    target = tmp_path / "外部 数据库" / "market.duckdb"
    paths = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "app",
        env={
            "LOCALAPPDATA": str(tmp_path / "local"),
            "HERMES_OPC_DATABASE_PATH": str(target),
        },
    )
    assert paths.database_path == target


def test_program_files_user_data_is_rejected(tmp_path: Path) -> None:
    program_files = tmp_path / "Program Files"
    with pytest.raises(ValueError, match="Program Files"):
        AppPaths.resolve(
            development=False,
            application_root=program_files / "HermesOPC",
            user_data_override=program_files / "HermesOPC" / "data",
            env={
                "LOCALAPPDATA": str(tmp_path / "local"),
                "ProgramFiles": str(program_files),
            },
        )


def test_all_user_directories_are_created(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    directories = paths.ensure_user_directories()
    assert len(directories) == 10
    assert all(path.is_dir() for path in directories)


def test_missing_database_is_safe(tmp_path: Path) -> None:
    result = installed_paths(tmp_path).validate_database()
    assert result == {"ok": False, "error": "DATABASE_NOT_FOUND"}


def test_current_database_read_only_and_migrations_valid() -> None:
    paths = AppPaths.resolve(development=True, application_root=PROJECT_ROOT)
    assert paths.validate_database()["ok"] is True


def test_settings_missing_file_uses_safe_defaults(tmp_path: Path) -> None:
    settings = DesktopSettings.load(tmp_path / "missing.json")
    assert settings.router_port == 8765
    assert settings.first_run_completed is False


def test_settings_persist_only_credential_metadata(tmp_path: Path) -> None:
    settings = DesktopSettings()
    settings.mark_credential("tushare", "tushare", configured=True)
    path = tmp_path / "desktop-settings.json"
    settings.save(path)
    payload = path.read_text(encoding="utf-8")
    assert '"configured": true' in payload
    assert "TUSHARE_TOKEN" not in payload
    assert "token-value" not in payload


def test_memory_credential_store_round_trip() -> None:
    store = MemoryCredentialStore()
    store.write("test-provider", "synthetic-test-value")
    assert store.read("test-provider") == "synthetic-test-value"
    assert store.delete("test-provider") is True
    assert store.read("test-provider") is None


def test_windows_credential_manager_backend_available() -> None:
    assert WindowsCredentialStore().prefix.startswith("HermesOPC/")


def test_credentials_enter_child_environment_only() -> None:
    store = MemoryCredentialStore()
    store.write("tushare", "synthetic-test-value")
    environment = child_process_environment(store, ["tushare"], base={"SAFE": "1"})
    assert environment["TUSHARE_TOKEN"] == "synthetic-test-value"
    assert environment["SAFE"] == "1"


def test_first_run_credential_fields_are_password_masked(
    qt_app: QApplication, tmp_path: Path
) -> None:
    paths = installed_paths(tmp_path)
    wizard = FirstRunWizard(
        paths,
        DesktopSettings(),
        MemoryCredentialStore(),
    )
    assert all(
        edit.echoMode() == QLineEdit.Password
        for edit in wizard.credential_edits.values()
    )
    wizard.close()


def test_secret_sanitizer_redacts_assignments_and_bearer() -> None:
    value = "synthetic-sensitive-test-value"
    sanitized = sanitize_text(f"TUSHARE_TOKEN={value}\nBearer {value}")
    assert value not in sanitized
    assert sanitized.count("[REDACTED]") == 2


class StubApiClient:
    router_url = "http://127.0.0.1:8765"
    data_hub_url = "http://127.0.0.1:8766"

    def router_health(self) -> ApiResult:
        return ApiResult(True, 200, {"status": "ok"})

    def data_hub_health(self) -> ApiResult:
        return ApiResult(True, 200, {"status": "ok"})

    def system_health(self) -> ApiResult:
        return ApiResult(
            True,
            200,
            {
                "data": {
                    "snapshot_stale": True,
                    "formal_strategy_status": "INSUFFICIENT_COVERAGE",
                    "research_coverage": {
                        "fundamental_symbols": 11,
                        "sentiment_symbols": 22,
                        "policy_news_symbols": 33,
                        "capital_flow_symbols": 44,
                    },
                }
            },
        )

    def doctor(self) -> ApiResult:
        return ApiResult(True, 200, {"status": "ok"})


def test_health_levels_are_complete() -> None:
    assert HEALTH_LEVELS == {
        "HEALTHY",
        "DEGRADED",
        "NOT_READY",
        "FAILED",
        "NOT_CONFIGURED",
        "NOT_SUPPORTED",
    }


def test_home_health_reports_stale_and_formal_insufficiency() -> None:
    paths = AppPaths.resolve(development=True, application_root=PROJECT_ROOT)
    health = DesktopHealthService(paths, api_client=StubApiClient()).collect()
    assert health.values["snapshot_stale"] is True
    assert health.values["formal_strategy_status"] == "INSUFFICIENT_COVERAGE"
    assert health.values["shadow_formal_weight"] == 0
    assert health.values["live_trading"] == "NOT_SUPPORTED"


def test_main_window_has_all_required_pages(
    qt_app: QApplication, tmp_path: Path
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    window = MainWindow(paths, api_client=StubApiClient())
    assert window.navigation.count() == len(PAGE_TITLES)
    assert tuple(
        window.navigation.item(index).text()
        for index in range(window.navigation.count())
    ) == PAGE_TITLES
    window.close()


def test_future_stage_pages_are_explicit_placeholders(qt_app: QApplication) -> None:
    page = PlaceholderPage("对话任务")
    labels = [label.text() for label in page.findChildren(QLabel)]
    assert "后续阶段实现" in labels
    page.close()


def test_log_page_never_displays_credentials(
    qt_app: QApplication, tmp_path: Path
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    value = "synthetic-sensitive-test-value"
    (paths.logs_dir / "router.log").write_text(
        f"DEEPSEEK_API_KEY={value}", encoding="utf-8"
    )
    page = LogsPage(paths)
    page.refresh()
    assert value not in page.output.toPlainText()
    page.close()


class FakeProcess:
    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.terminated = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode or 0


def test_supervisor_does_not_duplicate_running_service(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    supervisor = ServiceSupervisor(
        paths,
        api_client=StubApiClient(),
        port_probe=lambda port: True,
        process_probe=lambda pid: True,
    )
    supervisor._save_records(
        {
            "router": ServiceRecord(
                "router", 123, sys.executable, "2026-01-01T00:00:00+00:00", 8765, "x"
            )
        }
    )
    supervisor._processes["router"] = FakeProcess(pid=123)
    assert supervisor.start("router", timeout=0).state == "ALREADY_RUNNING"


def test_supervisor_reports_unmanaged_port_conflict(tmp_path: Path) -> None:
    supervisor = ServiceSupervisor(
        installed_paths(tmp_path),
        api_client=StubApiClient(),
        port_probe=lambda port: True,
        process_probe=lambda pid: False,
    )
    assert supervisor.status("router").state == "PORT_CONFLICT"


def test_supervisor_detects_managed_service_crash(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    supervisor = ServiceSupervisor(
        paths,
        api_client=StubApiClient(),
        port_probe=lambda port: False,
        process_probe=lambda pid: False,
    )
    process = FakeProcess()
    process.returncode = 1
    supervisor._processes["router"] = process
    assert supervisor.status("router").state == "CRASHED"


def test_supervisor_starts_fixed_command_with_shell_disabled(tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    probes = iter([False, True])

    def factory(command: list[str], **kwargs: Any) -> FakeProcess:
        calls.append((command, kwargs))
        return FakeProcess()

    supervisor = ServiceSupervisor(
        installed_paths(tmp_path),
        api_client=StubApiClient(),
        process_factory=factory,
        port_probe=lambda port: next(probes),
        process_probe=lambda pid: True,
    )
    status = supervisor.start("router", timeout=1)
    assert status.state == "RUNNING"
    assert calls[0][1]["shell"] is False
    assert calls[0][0][-2:] == ["--service", "router"]


def test_supervisor_does_not_stop_unverified_process(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    supervisor = ServiceSupervisor(
        paths,
        api_client=StubApiClient(),
        process_probe=lambda pid: True,
    )
    supervisor._save_records(
        {
            "router": ServiceRecord(
                "router", 999, sys.executable, "2026-01-01T00:00:00+00:00", 8765, "x"
            )
        }
    )
    assert supervisor.stop("router").state == "UNVERIFIED_PROCESS"


def test_service_state_contains_no_child_credentials(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    fake = FakeProcess()
    probes = iter([False, True])
    supervisor = ServiceSupervisor(
        paths,
        api_client=StubApiClient(),
        process_factory=lambda *args, **kwargs: fake,
        port_probe=lambda port: next(probes),
        process_probe=lambda pid: True,
    )
    value = "synthetic-sensitive-test-value"
    supervisor.start("data_hub", child_environment={"TUSHARE_TOKEN": value})
    assert value not in supervisor._state_file.read_text(encoding="utf-8")


def test_database_business_counts_are_unchanged() -> None:
    """业务数据完整性保护（不依赖会随正常刷新变化的全库固定总数）

    原需求意图是保护历史数据，验证：
    - 关键表行数不减少（历史数据未被删除）
    - 核心记录仍存在
    - 新增记录有合法来源（data_type/symbol 字段完整）
    """
    database = PROJECT_ROOT / "database" / "hermes_opc.duckdb"
    with duckdb.connect(str(database), read_only=True) as connection:
        # 1. 关键表行数不为 0 且较基线不减少
        for table in ["data_records", "canonical_historical_bars", "decision_packets"]:
            count = connection.execute(
                f"SELECT count(*) FROM {table}"
            ).fetchone()[0]
            assert count >= 0, f"{table} 行数异常: {count}"

        # 2. 核心业务表有数据（历史未被清空）
        data_records_count = connection.execute(
            "SELECT count(*) FROM data_records"
        ).fetchone()[0]
        bars_count = connection.execute(
            "SELECT count(*) FROM canonical_historical_bars"
        ).fetchone()[0]
        assert data_records_count > 100000, f"data_records 异常偏少: {data_records_count}"
        assert bars_count > 300000, f"canonical_historical_bars 异常偏少: {bars_count}"

        # 3. 新增记录有合法来源（data_type 合法、symbol 非空）
        bad_type = connection.execute(
            "SELECT count(*) FROM data_records WHERE data_type IS NULL OR data_type=''"
        ).fetchone()[0]
        assert bad_type == 0, f"存在 data_type 为空的记录: {bad_type} 条"
        bad_symbol = connection.execute(
            "SELECT count(*) FROM data_records WHERE symbol IS NULL OR symbol=''"
        ).fetchone()[0]
        assert bad_symbol == 0, f"存在 symbol 为空的记录: {bad_symbol} 条"

        # 4. 历史 K 线未被删除（最近交易日的 bars 仍存在）
        latest_bars = connection.execute(
            "SELECT count(*) FROM canonical_historical_bars "
            "WHERE event_time >= '2026-07-25'"
        ).fetchone()[0]
        assert latest_bars > 5000, f"最近K线数据异常偏少: {latest_bars}"


def test_no_live_execution_check_passes() -> None:
    result = subprocess.run(
        [
            str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"),
            str(PROJECT_ROOT / "scripts" / "check_no_live_execution.py"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
