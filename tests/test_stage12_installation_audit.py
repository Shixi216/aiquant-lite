from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from config.settings import settings
from desktop.entrypoint import _isolated_smoke_paths
from desktop.entrypoint import main as desktop_entrypoint
from desktop.credentials import MemoryCredentialStore
from desktop.first_run import FirstRunWizard
from desktop.pages import SettingsPage
from desktop.paths import AppPaths
from desktop.service_supervisor import ServiceSupervisor
from desktop.runtime import activate_runtime_paths
from desktop.runtime import activate_configured_credentials
from desktop.settings import DesktopSettings
from desktop.setup_service import SetupCheck
from desktop.setup_service import SetupService
from router.config import router_settings
from desktop.workspace.stage12d_pages import MarketScanPage
from desktop.workspace.pages import ConversationPage, SkillCenterPage
from desktop.workspace.service import TaskCenterService
from desktop.workspace.state import DesktopStateRepository
from desktop.workspace.trading_pages import PaperTradingPage, WatchlistPage
from desktop.workspace.watchlists import WatchlistRepository
from desktop.workspace.backtests import (
    BacktestWorkspaceConfig,
    BacktestWorkspaceService,
)
from router.integration.health import _database_state


def _installed_paths(tmp_path: Path) -> AppPaths:
    return AppPaths.resolve(
        development=False,
        application_root=tmp_path / "program",
        user_data_override=tmp_path / "user-data",
        env={"LOCALAPPDATA": str(tmp_path / "local")},
    )


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class _GateSetupService:
    api_client = object()
    model_probe = None

    def __init__(self, *, local_ok: bool) -> None:
        self.local_ok = local_ok

    def test_services(self) -> tuple[SetupCheck, SetupCheck]:
        return (
            SetupCheck("router", False, "NOT_READY", "local"),
            SetupCheck("data_hub", False, "NOT_READY", "local"),
        )

    def local_checks(
        self, *, database_path: Path | None = None
    ) -> list[SetupCheck]:
        del database_path
        return [
            SetupCheck("database", self.local_ok, "HEALTHY", "read-only"),
            SetupCheck("migrations", self.local_ok, "HEALTHY", "checksums"),
            SetupCheck("disk", True, "HEALTHY", "space"),
            SetupCheck("ports", True, "HEALTHY", "ports"),
            SetupCheck("no_live", True, "HEALTHY", "contract"),
        ]

    def test_tushare(self, token: str) -> SetupCheck:
        return SetupCheck("tushare", bool(token), "HEALTHY", "minimal")

    def test_model(self, provider: str, credential: str) -> SetupCheck:
        return SetupCheck(provider, bool(credential), "HEALTHY", "minimal")


def test_settings_page_manages_provider_credentials_without_plaintext(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = _installed_paths(tmp_path)
    paths.ensure_user_directories()
    settings = DesktopSettings(first_run_completed=True)
    store = MemoryCredentialStore()
    page = SettingsPage(
        paths,
        ServiceSupervisor(paths),
        settings,
        store,
        setup_service=_GateSetupService(local_ok=True),  # type: ignore[arg-type]
    )

    value = "synthetic-longcat-credential"
    page.provider_edits["longcat"].setText(value)
    page.save_credential("longcat")
    assert store.read("longcat") == value
    assert page.provider_edits["longcat"].text() == ""
    assert settings.credentials["longcat"].configured is True
    payload = page.settings_path.read_text(encoding="utf-8")
    assert value not in payload

    page.test_credential("longcat")
    assert settings.credentials["longcat"].last_test_status == "HEALTHY"
    page.delete_credential("longcat")
    assert store.read("longcat") is None
    assert settings.credentials["longcat"].configured is False
    assert page.provider_status["longcat"].text() == "NOT_CONFIGURED"
    page.close()


def test_runtime_activation_updates_environment_and_cached_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _installed_paths(tmp_path)
    original = settings.opc_database_path
    monkeypatch.setattr(settings, "opc_database_path", original)
    monkeypatch.setenv(
        "HERMES_OPC_USER_DATA_DIR", str(tmp_path / "previous-user-data")
    )
    monkeypatch.setenv(
        "OPC_DATABASE_PATH", str(tmp_path / "previous.duckdb")
    )

    activate_runtime_paths(paths)

    assert os.environ["HERMES_OPC_USER_DATA_DIR"] == str(paths.user_data_dir)
    assert os.environ["OPC_DATABASE_PATH"] == str(paths.database_path)
    assert settings.opc_database_path == paths.database_path


def test_headless_smoke_uses_isolated_paths(tmp_path: Path) -> None:
    configured = _installed_paths(tmp_path)
    isolated = _isolated_smoke_paths(configured, tmp_path / "smoke")

    assert isolated.application_root == configured.application_root
    assert isolated.user_data_dir != configured.user_data_dir
    assert isolated.database_path != configured.database_path
    assert isolated.desktop_state_path != configured.desktop_state_path
    assert isolated.database_path.is_relative_to(tmp_path / "smoke")


def test_packaged_runtime_includes_timezone_dependency() -> None:
    spec = (
        Path(__file__).resolve().parents[1]
        / "build"
        / "pyinstaller"
        / "hermes_opc.spec"
    ).read_text(encoding="utf-8")

    assert '"pytz"' in spec


def test_initial_window_size_fits_small_and_high_dpi_screens() -> None:
    from desktop.application import initial_window_size

    desktop = initial_window_size(1920, 1080)
    small = initial_window_size(1366, 768)
    high_dpi = initial_window_size(683, 384)

    assert desktop.width() == 1280
    assert desktop.height() == 800
    assert small.width() < 1366
    assert small.height() < 768
    assert high_dpi.width() < 683
    assert high_dpi.height() < 384
    assert high_dpi.width() >= 560
    assert high_dpi.height() >= 320


def test_configured_model_credential_updates_desktop_and_router_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _installed_paths(tmp_path)
    paths.ensure_user_directories()
    desktop_settings = DesktopSettings()
    desktop_settings.mark_credential("qwen", "qwen", configured=True)
    desktop_settings.save(paths.config_dir / "desktop-settings.json")
    store = MemoryCredentialStore()
    store.write("qwen", "test-qwen-placeholder")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "previous-placeholder")
    monkeypatch.setattr(router_settings, "qwen_api_key", None)

    activated = activate_configured_credentials(paths, store=store)

    assert activated == ("qwen",)
    assert router_settings.qwen_api_key is not None
    assert (
        router_settings.qwen_api_key.get_secret_value()
        == "test-qwen-placeholder"
    )


def test_setup_service_has_real_model_probe_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _installed_paths(tmp_path)
    monkeypatch.setattr(
        "desktop.setup_service._minimal_model_probe",
        lambda provider, credential: (
            provider == "qwen" and credential == "placeholder"
        ),
    )
    service = SetupService(paths)

    result = service.test_model("qwen", "placeholder")

    assert result.ok is True
    assert result.status == "HEALTHY"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ModuleNotFoundError("pytz"), "COMPONENT_UNAVAILABLE"),
        (OSError("database unavailable"), "IO_ERROR"),
        (ValueError("bad request"), "INVALID_INPUT"),
        (RuntimeError("unexpected"), "TASK_FAILED"),
    ],
)
def test_workspace_errors_have_stable_sanitized_codes(
    error: Exception,
    expected: str,
) -> None:
    code, reason = TaskCenterService._classify_error(error)

    assert code == expected
    assert reason
    assert "pytz" not in reason


def test_watchlist_scan_reports_candidate_count(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    state = DesktopStateRepository(tmp_path / "desktop.sqlite3")
    page = WatchlistPage(
        WatchlistRepository(state),
        exports_dir=tmp_path / "exports",
    )

    page._batch_finished(
        "SCAN",
        SimpleNamespace(candidates=[object(), object()]),
    )

    assert page.status.text() == "SCAN_COMPLETED"
    assert "实际处理 2 只" in page.batch_result.text()
    page.close()


def test_paper_order_requires_exact_confirmation(
    qt_app: QApplication,
) -> None:
    class PaperService:
        def __init__(self) -> None:
            self.submitted = 0

        def paper_account(self):
            return SimpleNamespace(
                cash=1_000_000,
                equity=1_000_000,
                positions={},
                kill_switch=False,
            )

        def paper_orders(self):
            return []

        def submit_paper_order(self, intent, limits):
            del intent, limits
            self.submitted += 1
            return SimpleNamespace(
                status=SimpleNamespace(value="filled"),
            )

        def set_kill_switch(self, active: bool):
            del active

    service = PaperService()
    page = PaperTradingPage(service=service)  # type: ignore[arg-type]
    page.submit_order()
    assert page.status.text() == "PAPER_CONFIRMATION_REQUIRED"
    assert service.submitted == 0

    page.confirmation.setText("CONFIRM_PAPER_ORDER")
    page.submit_order()

    assert service.submitted == 1
    assert page.status.text() == "PAPER_FILLED"
    assert "真实订单能力：无" in page.output.toPlainText()
    page.close()


def test_existing_conversation_is_selected_after_desktop_restart(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    repository = DesktopStateRepository(tmp_path / "desktop.sqlite3")
    service = TaskCenterService(repository)
    conversation = service.new_conversation("existing")

    page = ConversationPage(
        repository,
        service,
        exports_dir=tmp_path / "exports",
    )

    assert page.current_conversation_id == conversation.conversation_id
    assert page.conversations.currentItem() is not None
    page.close()


def test_completed_conversation_keeps_visible_timeline(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    repository = DesktopStateRepository(tmp_path / "desktop.sqlite3")
    service = TaskCenterService(repository)
    page = ConversationPage(
        repository,
        service,
        exports_dir=tmp_path / "exports",
    )
    task = service.submit_message(
        page.current_conversation_id or "",
        "300750.SZ",
    )

    page._task_finished(task)

    assert "COMPLETED" in page.task_status.text()
    assert "STRUCTURED_RESULT" in page.timeline.toPlainText()
    assert page.current_task_id == task.task_id
    page.close()


def test_gui_entrypoint_activates_database_before_application_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _installed_paths(tmp_path)
    observed: dict[str, object] = {}
    fake_application = ModuleType("desktop.application")
    monkeypatch.setattr(
        settings, "opc_database_path", settings.opc_database_path
    )
    monkeypatch.setenv(
        "HERMES_OPC_USER_DATA_DIR", str(tmp_path / "previous-user-data")
    )
    monkeypatch.setenv(
        "OPC_DATABASE_PATH", str(tmp_path / "previous.duckdb")
    )

    def fake_main(*, paths: AppPaths) -> int:
        observed["paths"] = paths
        observed["database_environment"] = os.environ.get("OPC_DATABASE_PATH")
        observed["cached_database"] = settings.opc_database_path
        return 17

    fake_application.main = fake_main  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "desktop.application", fake_application)
    monkeypatch.setattr(AppPaths, "resolve", classmethod(lambda cls: paths))
    monkeypatch.setattr(
        "desktop.runtime.activate_configured_credentials",
        lambda selected: (),
    )

    assert desktop_entrypoint([]) == 17
    assert observed == {
        "paths": paths,
        "database_environment": str(paths.database_path),
        "cached_database": paths.database_path,
    }


def test_first_run_finish_is_blocked_until_critical_checks_pass(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _installed_paths(tmp_path)
    service = _GateSetupService(local_ok=True)
    wizard = FirstRunWizard(
        paths,
        DesktopSettings(),
        MemoryCredentialStore(),
        setup_service=service,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "desktop.first_run.SetupService",
        lambda *args, **kwargs: service,
    )

    assert wizard.diagnostics_page.isComplete() is False
    wizard._run_diagnostics()
    assert wizard.diagnostics_page.isComplete() is True
    wizard.database_import.setText(str(tmp_path / "changed.duckdb"))
    assert wizard.diagnostics_page.isComplete() is False
    wizard.close()


def test_first_run_finish_stays_blocked_when_database_check_fails(
    qt_app: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _installed_paths(tmp_path)
    service = _GateSetupService(local_ok=False)
    wizard = FirstRunWizard(
        paths,
        DesktopSettings(),
        MemoryCredentialStore(),
        setup_service=service,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "desktop.first_run.SetupService",
        lambda *args, **kwargs: service,
    )

    wizard._run_diagnostics()
    assert wizard.diagnostics_page.isComplete() is False
    wizard.close()


def test_market_scan_page_does_not_claim_empty_universe_completed(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    page = MarketScanPage(exports_dir=tmp_path)
    page._scan_finished(
        SimpleNamespace(
            scanned_count=0,
            universe_count=0,
            returned_count=0,
            matched_count=0,
            candidates=[],
        )
    )
    assert page.status.text() == "SCANNER_EMPTY_UNIVERSE"
    assert "没有成功" in page.summary.text()
    assert page.output.isVisible() is False
    page.close()


def test_router_health_reads_the_configured_database_with_repository_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import duckdb

    path = tmp_path / "health.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            "CREATE TABLE market_snapshot_runs(snapshot_time TIMESTAMPTZ)"
        )
        connection.execute(
            "CREATE TABLE canonical_financial_records(symbol VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE sentiment_symbol_snapshots(symbol VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE policy_news_symbol_snapshots(symbol VARCHAR)"
        )
        connection.execute(
            "CREATE TABLE capital_flow_symbol_snapshots(symbol VARCHAR)"
        )
        connection.execute(
            "INSERT INTO canonical_financial_records VALUES ('600000.SH')"
        )
    monkeypatch.setattr(settings, "opc_database_path", path)

    ok, snapshot_time, coverage = _database_state()

    assert ok is True
    assert snapshot_time is None
    assert coverage["fundamental_symbols"] == 1


def test_skill_center_uses_form_and_hides_raw_schema_by_default(
    qt_app: QApplication,
) -> None:
    page = SkillCenterPage()
    assert page.run_button.text() == "运行技能"
    assert page.query.isVisible() is False or page.query.isHidden() is False
    assert page.details.isHidden() is True
    assert not page.result.toPlainText().lstrip().startswith("{")
    page.close()


def test_backtest_workspace_executes_existing_run_without_persistence() -> None:
    class Repository:
        def latest_run(self, experiment_id: str):
            assert experiment_id == "exp_test"
            return SimpleNamespace(run_id="erun_test")

        def latest_experiment_id(self) -> str:
            return "exp_test"

    class Evaluation:
        def run_portfolio_backtest(self, request):
            assert request.persist is False
            assert request.run_id == "erun_test"
            return SimpleNamespace(
                cumulative_return=0.01,
                annualized_return=None,
                annualized_volatility=None,
                maximum_drawdown=-0.02,
                sharpe_ratio=None,
                turnover=0.3,
                net_return=0.008,
                benchmark_return=0.005,
                excess_return=0.003,
                active_days=12,
                positions=[],
                persisted=False,
            )

    service = BacktestWorkspaceService(
        repository=Repository(),  # type: ignore[arg-type]
        evaluation=Evaluation(),  # type: ignore[arg-type]
    )
    result = service.run_existing(
        BacktestWorkspaceConfig(
            experiment_id="exp_test",
            symbols=["300750.SZ"],
            start_date=__import__("datetime").date(2026, 1, 1),
            end_date=__import__("datetime").date(2026, 7, 1),
        )
    )

    assert result.status == "COMPLETED"
    assert result.metrics["net_return"] == 0.008
    assert result.data_quality["persisted"] is False
    assert result.manual_ledger_written is False
    assert result.paper_trading_written is False
