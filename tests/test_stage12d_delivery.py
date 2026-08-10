from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import pytest
from PySide6.QtWidgets import QApplication

from build.scripts.package_policy import verify_package_tree
from desktop.application import MainWindow, PAGE_TITLES
from desktop.backup import DatabaseBackupService
from desktop.build_info import DEFAULT_BUILD_INFO, load_build_info
from desktop.entrypoint import main as desktop_entrypoint
from desktop.paths import AppPaths
from desktop.safety_contract import SAFETY_CONTRACT, packaged_no_live_check
from desktop.service_supervisor import ServiceStatus
from database.db import SCHEMA_STATEMENTS
from database.migrations import run_migrations
from desktop.workspace.stage12d_pages import (
    CandidateDetailPage,
    DecisionSupportPage,
    ExperimentReportPage,
    ManualPositionsPage,
    MarketScanPage,
    PositionRiskPage,
    TaskSkillPage,
)
from desktop.workspace.trading_pages import ManualTradingPage


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PORTABLE_ROOT = PROJECT_ROOT / "dist" / "portable" / "HermesOPC"
INSTALLER = (
    PROJECT_ROOT / "dist" / "installer" / "HermesOPC-0.10.0-Setup.exe"
)


@pytest.fixture(scope="module")
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def installed_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.resolve(
        development=False,
        application_root=tmp_path / "Program Files" / "Hermes OPC",
        user_data_override=tmp_path / "用户 数据",
        env={"LOCALAPPDATA": str(tmp_path / "Local AppData")},
    )


class StubServiceState:
    def __init__(self, state: str = "STOPPED") -> None:
        self.state = state

    def status(self, service: str) -> ServiceStatus:
        return ServiceStatus(name=service, state=self.state)


def test_desktop_has_all_21_delivery_pages(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    window = MainWindow(paths)
    assert len(PAGE_TITLES) == 21
    assert window.navigation.count() == 21
    assert window.stack.count() == 21
    window.close()


def test_delivery_navigation_contains_required_distinct_pages() -> None:
    assert {
        "市场扫描",
        "候选详情",
        "人工成交",
        "人工持仓",
        "持仓风险",
        "Paper Trading",
        "盘前简报",
        "调度中心",
        "关于与限制",
    } <= set(PAGE_TITLES)


def test_delivery_pages_are_not_placeholders(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    window = MainWindow(paths)
    class_names = {
        window.stack.widget(index).__class__.__name__
        for index in range(window.stack.count())
    }
    assert "PlaceholderPage" not in class_names
    window.close()


def test_scan_candidate_research_and_decision_pages_are_real(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    window = MainWindow(paths)
    widgets = [
        window.stack.widget(index)
        for index in range(window.stack.count())
    ]
    assert any(isinstance(item, MarketScanPage) for item in widgets)
    assert any(isinstance(item, CandidateDetailPage) for item in widgets)
    assert any(
        isinstance(item, TaskSkillPage)
        and not isinstance(item, DecisionSupportPage)
        for item in widgets
    )
    assert any(isinstance(item, DecisionSupportPage) for item in widgets)
    window.close()


def test_manual_trade_positions_and_risk_are_separate_pages(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    window = MainWindow(paths)
    widgets = [
        window.stack.widget(index)
        for index in range(window.stack.count())
    ]
    assert sum(isinstance(item, ManualTradingPage) for item in widgets) == 1
    assert sum(isinstance(item, ManualPositionsPage) for item in widgets) == 1
    assert sum(isinstance(item, PositionRiskPage) for item in widgets) == 1
    window.close()


def test_experiment_report_page_is_available(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = installed_paths(tmp_path)
    paths.ensure_user_directories()
    window = MainWindow(paths)
    assert any(
        isinstance(window.stack.widget(index), ExperimentReportPage)
        for index in range(window.stack.count())
    )
    window.close()


def test_packaged_safety_contract_has_no_live_capability() -> None:
    assert packaged_no_live_check() is True
    assert SAFETY_CONTRACT["live_trading_supported"] is False
    assert SAFETY_CONTRACT["broker_adapter_supported"] is False
    assert SAFETY_CONTRACT["qmt_supported"] is False
    assert SAFETY_CONTRACT["xtquant_supported"] is False
    assert SAFETY_CONTRACT["real_order_supported"] is False


def test_formal_weights_and_veto_are_fixed_in_delivery_contract() -> None:
    assert SAFETY_CONTRACT["formal_strategy_weights"] == {
        "TECHNICAL": 0.6,
        "FUNDAMENTAL": 0.4,
    }
    assert SAFETY_CONTRACT["shadow_composite_formal_weight"] == 0
    assert SAFETY_CONTRACT["hard_risk_veto_mutable"] is False


def test_build_info_contains_all_required_version_fields() -> None:
    assert {
        "application_version",
        "build_time",
        "git_head",
        "working_tree_dirty",
        "migration_version",
        "router_api_version",
        "scanner_version",
        "orchestration_version",
        "experiment_version",
        "desktop_version",
        "build_channel",
    } == set(DEFAULT_BUILD_INFO)


def test_candidate_build_is_not_claimed_as_stable() -> None:
    manifest = json.loads(
        (
            PROJECT_ROOT / "build" / "manifests" / "build-manifest.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["build_channel"] == "candidate"
    assert manifest["working_tree_dirty"] is True
    assert manifest["git_head"] == "7c02b41fb1b361de10e81f9be06b3b9bc7a273c7"


def test_development_build_info_loads_without_credentials() -> None:
    serialized = json.dumps(load_build_info(), ensure_ascii=False).casefold()
    assert "token" not in serialized
    assert "api_key" not in serialized
    assert "secret" not in serialized


def test_entrypoint_diagnostic_uses_user_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = tmp_path / "用户 数据"
    monkeypatch.setenv("HERMES_OPC_USER_DATA_DIR", str(target))
    assert desktop_entrypoint(["--diagnose-json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["user_data_dir_exists"] is True
    assert payload["python_required_for_user"] is False
    assert payload["uv_required_for_user"] is False
    assert payload["live_trading_supported"] is False


def test_entrypoint_version_is_project_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert desktop_entrypoint(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.10.0"


def test_service_entrypoint_routes_database_to_user_data() -> None:
    entrypoint_source = (PROJECT_ROOT / "desktop" / "entrypoint.py").read_text(
        encoding="utf-8"
    )
    runtime_source = (PROJECT_ROOT / "desktop" / "runtime.py").read_text(
        encoding="utf-8"
    )
    assert "prepare_runtime()" in entrypoint_source
    assert 'os.environ["OPC_DATABASE_PATH"] = str(paths.database_path)' in (
        runtime_source
    )
    paths_source = (PROJECT_ROOT / "desktop" / "paths.py").read_text(
        encoding="utf-8"
    )
    assert "\nfrom router.integration.health import" not in paths_source


def test_package_policy_rejects_dotenv(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("EXAMPLE=value\n", encoding="utf-8")
    result = verify_package_tree(tmp_path)
    assert result.ok is False
    assert result.forbidden_paths == (".env",)


def test_package_policy_allows_placeholder_env_example(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.example").write_text(
        "TUSHARE_TOKEN=\nDEEPSEEK_API_KEY=YOUR_API_KEY\n",
        encoding="utf-8",
    )
    assert verify_package_tree(tmp_path).ok is True


def test_package_policy_rejects_real_env_example_value(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env.example").write_text(
        "TUSHARE_TOKEN=non-placeholder-test-secret\n",
        encoding="utf-8",
    )
    result = verify_package_tree(tmp_path)
    assert result.ok is False
    assert result.secret_assignment_files == (".env.example",)


@pytest.mark.parametrize(
    "relative",
    [
        "database/hermes_opc.duckdb",
        "reports/real.json",
        "logs/router.log",
        "backups/db.bak",
        ".git/config",
        "__pycache__/module.pyc",
    ],
)
def test_package_policy_rejects_runtime_and_business_data(
    tmp_path: Path,
    relative: str,
) -> None:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test", encoding="utf-8")
    assert verify_package_tree(tmp_path).ok is False


def test_pyinstaller_spec_builds_gui_and_service_executables() -> None:
    source = (
        PROJECT_ROOT / "build" / "pyinstaller" / "hermes_opc.spec"
    ).read_text(encoding="utf-8")
    assert 'name="HermesOPC"' in source
    assert 'name="hermes-opc-service"' in source
    assert "console=False" in source
    assert "console=True" in source


def test_pyinstaller_spec_excludes_development_tools() -> None:
    source = (
        PROJECT_ROOT / "build" / "pyinstaller" / "hermes_opc.spec"
    ).read_text(encoding="utf-8")
    assert '"pytest"' in source
    assert '"ruff"' in source
    assert "hermes_opc.duckdb" not in source
    assert 'project_root / ".env"' not in source
    assert 'project_root / "reports"' not in source
    assert 'project_root / "logs"' not in source


def test_pyinstaller_is_reproducible_dev_dependency() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pyinstaller>=6.16,<7"' in pyproject.casefold()


def test_inno_setup_has_fixed_app_id_and_version() -> None:
    source = (
        PROJECT_ROOT / "build" / "inno" / "hermes-opc.iss"
    ).read_text(encoding="utf-8")
    assert "8F68E55B-586E-4BCF-B1D8-2F5D84903643" in source
    assert '#define MyAppVersion "0.10.0"' in source


def test_installer_is_per_user_and_has_optional_shortcut() -> None:
    source = (
        PROJECT_ROOT / "build" / "inno" / "hermes-opc.iss"
    ).read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in source
    assert r"DefaultDirName={localappdata}\Programs\HermesOPC" in source
    assert "desktopicon" in source
    assert "Flags: unchecked" in source


def test_uninstall_preserves_user_data_by_default() -> None:
    source = (
        PROJECT_ROOT / "build" / "inno" / "hermes-opc.iss"
    ).read_text(encoding="utf-8")
    assert "DeleteUserDataCheck.Checked := False" in source
    assert "DeleteUserDataCheck.Checked then" in source
    assert "DelTree(ExpandConstant('{localappdata}\\HermesOPC')" in source


def test_user_data_deletion_requires_second_confirmation() -> None:
    source = (
        PROJECT_ROOT / "build" / "inno" / "hermes-opc.iss"
    ).read_text(encoding="utf-8")
    assert "MsgBox(" in source
    assert "MB_YESNO" in source
    assert "IDYES" in source


def test_all_required_delivery_documents_exist() -> None:
    expected = {
        "first-run-guide.zh-CN.md",
        "user-manual.zh-CN.md",
        "skills-guide.zh-CN.md",
        "installation.zh-CN.md",
        "upgrade.zh-CN.md",
        "uninstall.zh-CN.md",
        "database-backup.zh-CN.md",
        "disaster-recovery.zh-CN.md",
        "security-and-limitations.zh-CN.md",
    }
    actual = {
        path.name
        for path in (PROJECT_ROOT / "docs" / "delivery").glob("*.md")
    }
    assert expected <= actual


def test_security_document_disclaims_profitability_and_live_trading() -> None:
    text = (
        PROJECT_ROOT
        / "docs"
        / "delivery"
        / "security-and-limitations.zh-CN.md"
    ).read_text(encoding="utf-8")
    assert "不支持实盘交易" in text
    assert "不能证明稳定盈利" in text
    assert "INSUFFICIENT_COVERAGE" in text


def test_upgrade_document_preserves_user_state() -> None:
    text = (
        PROJECT_ROOT / "docs" / "delivery" / "upgrade.zh-CN.md"
    ).read_text(encoding="utf-8")
    assert "不覆盖用户数据库" in text
    assert "会话或调度任务" in text
    assert "不自动回滚" in text


def test_recovery_document_requires_backup_and_confirmation() -> None:
    text = (
        PROJECT_ROOT / "docs" / "delivery" / "disaster-recovery.zh-CN.md"
    ).read_text(encoding="utf-8")
    assert "再备份当前数据库" in text
    assert "用户二次确认" in text
    assert "不提供默认“一键覆盖恢复”" in text


@pytest.mark.skipif(
    not (PORTABLE_ROOT / "HermesOPC.exe").is_file(),
    reason="portable artifact has not been built",
)
def test_portable_artifact_contains_both_executables() -> None:
    assert (PORTABLE_ROOT / "HermesOPC.exe").is_file()
    assert (PORTABLE_ROOT / "hermes-opc-service.exe").is_file()


@pytest.mark.skipif(
    not (PORTABLE_ROOT / "HermesOPC.exe").is_file(),
    reason="portable artifact has not been built",
)
def test_portable_artifact_passes_package_policy() -> None:
    result = verify_package_tree(PORTABLE_ROOT)
    assert result.ok is True
    assert result.forbidden_paths == ()
    assert result.secret_assignment_files == ()


@pytest.mark.skipif(
    not INSTALLER.is_file(),
    reason="installer artifact has not been built",
)
def test_installer_and_checksum_manifest_exist() -> None:
    assert INSTALLER.is_file()
    assert (
        PROJECT_ROOT / "dist" / "checksums" / "installer-sha256.txt"
    ).is_file()
    assert (
        PROJECT_ROOT / "dist" / "manifests" / "installer-manifest.json"
    ).is_file()


def test_installer_lifecycle_verifier_is_safely_scoped() -> None:
    source = (
        PROJECT_ROOT / "build" / "scripts" / "verify_installer.py"
    ).read_text(encoding="utf-8")
    assert '"dist" / "e2e" / "安装 升级 卸载"' in source
    assert "refusing to clean outside the installer test root" in source
    assert "upgrade_preserved_user_data" in source
    assert "default_uninstall_preserved_user_data" in source
    assert "reinstall_recognized_user_data" in source


def test_packaged_service_verifier_does_not_inherit_credentials() -> None:
    source = (
        PROJECT_ROOT / "build" / "scripts" / "verify_packaged_services.py"
    ).read_text(encoding="utf-8")
    assert 'blocked = ("token", "api_key", "secret", "password")' in source
    assert '"persist_run": False' in source
    assert "database_in_program_directory" in source
    assert '"scanner_empty_universe_handled": True' in source
    assert '"scanner_false_success": False' in source


def test_database_backup_has_sha_read_only_and_migration_validation(
    tmp_path: Path,
) -> None:
    paths = installed_paths(tmp_path)
    paths.database_path.parent.mkdir(parents=True)
    with duckdb.connect(str(paths.database_path)) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        run_migrations(connection)
    backup = DatabaseBackupService(paths, StubServiceState()).create()
    target = Path(backup.database_path)
    assert target.is_file()
    assert backup.size_bytes == paths.database_path.stat().st_size
    assert backup.read_only_open_ok is True
    assert backup.migration_checksums_match is True
    assert backup.retention_policy == "MANUAL_KEEP_ALL"
    with duckdb.connect(str(target), read_only=True) as connection:
        assert connection.execute("SELECT 1").fetchone() == (1,)
    metadata = Path(backup.metadata_path).read_text(encoding="utf-8")
    assert "TOKEN" not in metadata
    assert "API_KEY" not in metadata


def test_database_backup_refuses_running_services(tmp_path: Path) -> None:
    paths = installed_paths(tmp_path)
    paths.database_path.parent.mkdir(parents=True)
    paths.database_path.touch()
    service = DatabaseBackupService(paths, StubServiceState("RUNNING"))
    with pytest.raises(RuntimeError, match="BACKUP_REQUIRES_STOPPED_SERVICES"):
        service.create()
