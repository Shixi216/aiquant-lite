from __future__ import annotations

import csv
import inspect
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from config.settings import settings
from database.db import get_connection, initialize_database
from desktop.application import MainWindow, PAGE_TITLES
from desktop.paths import AppPaths
from desktop.workspace.backtests import (
    BacktestWorkspaceConfig,
    BacktestWorkspaceService,
)
from desktop.workspace.manual_trading import ManualTradeDesktopController
from desktop.workspace.refresh import MarketRefreshController
from desktop.workspace.reviews import DesktopReviewService
from desktop.workspace.safe_executor import SafeScheduledTaskExecutor
from desktop.workspace.scheduler import (
    Frequency,
    LocalScheduler,
    ScheduleRepository,
    ScheduleType,
)
from desktop.workspace.state import DesktopStateRepository
from desktop.workspace.trading_pages import (
    BacktestPage,
    ManualTradingPage,
    PaperTradingPage,
    ReviewPage,
    SchedulerPage,
    WatchlistPage,
)
from desktop.workspace.watchlists import WatchlistRepository
from trading.schemas import (
    AnalysisMode,
    ManualTradeCorrectionRequest,
    ManualTradeReplacement,
    ManualTradeSide,
)
from trading.simulation.persistence import TradingAuditStore
from trading.simulation.service import SimulationService


@pytest.fixture
def state(tmp_path: Path) -> DesktopStateRepository:
    return DesktopStateRepository(tmp_path / "desktop-state.sqlite3")


@pytest.fixture
def watchlists(state: DesktopStateRepository) -> WatchlistRepository:
    return WatchlistRepository(state)


@pytest.fixture
def schedules(state: DesktopStateRepository) -> ScheduleRepository:
    return ScheduleRepository(state)


@pytest.fixture
def review_database(tmp_path: Path) -> Path:
    path = tmp_path / "review.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE market_snapshot_runs(
                snapshot_time TIMESTAMPTZ,
                valid_symbol_count BIGINT,
                completeness_status VARCHAR
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE manual_trades(
                portfolio_id VARCHAR,
                symbol VARCHAR,
                side VARCHAR,
                quantity BIGINT
            )
            """
        )
        connection.execute("CREATE TABLE paper_accounts(account_id VARCHAR)")
        connection.execute(
            "CREATE TABLE decision_packets(created_at TIMESTAMPTZ)"
        )
    return path


@pytest.fixture(scope="module")
def manual_database(tmp_path_factory: pytest.TempPathFactory):
    previous = settings.opc_database_path
    settings.opc_database_path = (
        tmp_path_factory.mktemp("stage12c-manual") / "manual.duckdb"
    )
    initialize_database()
    try:
        yield
    finally:
        settings.opc_database_path = previous


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    QThreadPool.globalInstance().waitForDone(5_000)
    app.closeAllWindows()
    app.processEvents()


def test_watchlist_crud(watchlists: WatchlistRepository) -> None:
    created = watchlists.create("高关注")
    renamed = watchlists.rename(created.watchlist_id, "风险观察")
    assert renamed.name == "风险观察"
    assert watchlists.delete(created.watchlist_id) is True
    assert watchlists.list_watchlists() == []


def test_watchlist_item_metadata(watchlists: WatchlistRepository) -> None:
    pool = watchlists.create("观察池")
    item = watchlists.add_symbol(
        pool.watchlist_id,
        "600000.sh",
        tags=["银行", "低波"],
        note="等待财报",
        source_task_id="task-local",
    )
    assert item.symbol == "600000.SH"
    assert item.tags == ("银行", "低波")
    assert item.source_task_id == "task-local"
    assert item.last_researched_at is None
    assert item.freshness == "MISSING"


def test_watchlist_item_update_and_research_state(
    watchlists: WatchlistRepository,
) -> None:
    pool = watchlists.create("研究池")
    watchlists.add_symbol(pool.watchlist_id, "300750.SZ")
    updated = watchlists.update_symbol(
        pool.watchlist_id,
        "300750.SZ",
        tags=["新能源"],
        note="跟踪公告",
    )
    watchlists.update_research_state(
        pool.watchlist_id,
        updated.symbol,
        researched_at=datetime.now().astimezone(),
        decision_status="INSUFFICIENT_COVERAGE",
        risk_flags=["FUNDAMENTAL_MISSING"],
        freshness="STALE",
    )
    final = watchlists.items(pool.watchlist_id)[0]
    assert final.note == "跟踪公告"
    assert final.risk_flags == ("FUNDAMENTAL_MISSING",)
    assert final.last_decision_status == "INSUFFICIENT_COVERAGE"


def test_watchlist_batch_research_limit(
    watchlists: WatchlistRepository,
) -> None:
    pool = watchlists.create("批量研究")
    for index in range(31):
        watchlists.add_symbol(
            pool.watchlist_id,
            f"{index:06d}.SZ",
        )
    assert len(watchlists.scan_symbols(pool.watchlist_id)) == 31
    with pytest.raises(ValueError, match="at most 30"):
        watchlists.research_symbols(pool.watchlist_id)


def test_watchlist_export_is_separate_csv(
    watchlists: WatchlistRepository,
    tmp_path: Path,
) -> None:
    pool = watchlists.create("导出池")
    watchlists.add_symbol(pool.watchlist_id, "430047.BJ", tags=["北交所"])
    destination = watchlists.export_csv(
        pool.watchlist_id,
        tmp_path / "exports" / "watchlist.csv",
    )
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["symbol"] == "430047.BJ"
    assert "position" not in rows[0]


def test_builtin_schedules_are_disabled(
    schedules: ScheduleRepository,
) -> None:
    builtins = [
        item for item in schedules.list() if item.schedule_id.startswith("builtin_")
    ]
    assert len(builtins) == 6
    assert all(not item.enabled for item in builtins)


@pytest.mark.parametrize(
    "payload",
    [
        {"decision": True},
        {"nested": {"real_order": {"symbol": "600000.SH"}}},
        {"items": [{"database_restore": True}]},
    ],
)
def test_scheduler_rejects_forbidden_operations(
    schedules: ScheduleRepository,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        schedules.create(
            name="unsafe",
            task_type=ScheduleType.SYSTEM_HEALTH,
            frequency=Frequency.ONCE,
            payload=payload,
        )


def test_schedule_requires_explicit_enable(
    schedules: ScheduleRepository,
) -> None:
    schedule = schedules.create(
        name="disabled",
        task_type=ScheduleType.SYSTEM_HEALTH,
        frequency=Frequency.ONCE,
    )
    runner = LocalScheduler(schedules, lambda *_: {"ok": True})
    with pytest.raises(ValueError, match="explicitly enabled"):
        runner.run_now(schedule.schedule_id)


def test_schedule_result_forces_trading_boundaries(
    schedules: ScheduleRepository,
) -> None:
    schedule = schedules.create(
        name="safe",
        task_type=ScheduleType.SYSTEM_HEALTH,
        frequency=Frequency.ONCE,
        enabled=True,
    )
    runner = LocalScheduler(
        schedules,
        lambda *_: {
            "decision_called": True,
            "manual_trade_written": True,
            "real_order_created": True,
        },
    )
    result = runner.run_now(schedule.schedule_id)
    assert result.status == "SUCCESS"
    assert result.result["decision_called"] is False
    assert result.result["manual_trade_written"] is False
    assert result.result["real_order_created"] is False


def test_schedule_runs_are_idempotent(
    schedules: ScheduleRepository,
) -> None:
    calls = 0

    def execute(*_):
        nonlocal calls
        calls += 1
        return {"ok": True}

    schedule = schedules.create(
        name="idempotent",
        task_type=ScheduleType.SYSTEM_HEALTH,
        frequency=Frequency.DAILY,
        enabled=True,
    )
    runner = LocalScheduler(schedules, execute)
    due = datetime.now().astimezone().replace(microsecond=0)
    first = runner.run_now(schedule.schedule_id, scheduled_for=due)
    second = runner.run_now(schedule.schedule_id, scheduled_for=due)
    assert first.run_id == second.run_id
    assert calls == 1


def test_run_due_once_disables_schedule(
    schedules: ScheduleRepository,
) -> None:
    now = datetime.now().astimezone()
    schedule = schedules.create(
        name="one time",
        task_type=ScheduleType.SYSTEM_HEALTH,
        frequency=Frequency.ONCE,
        enabled=True,
        next_run_at=now - timedelta(minutes=1),
    )
    runs = LocalScheduler(
        schedules, lambda *_: {"ok": True}
    ).run_due(now=now)
    assert len(runs) == 1
    updated = schedules.get(schedule.schedule_id)
    assert updated.enabled is False
    assert updated.next_run_at is None


def test_run_due_daily_advances_next_time(
    schedules: ScheduleRepository,
) -> None:
    now = datetime.now().astimezone()
    schedule = schedules.create(
        name="daily",
        task_type=ScheduleType.SYSTEM_HEALTH,
        frequency=Frequency.DAILY,
        enabled=True,
        next_run_at=now - timedelta(days=2),
    )
    LocalScheduler(schedules, lambda *_: {"ok": True}).run_due(now=now)
    assert schedules.get(schedule.schedule_id).next_run_at > now


def test_failed_schedule_can_retry(
    schedules: ScheduleRepository,
) -> None:
    attempts = 0

    def execute(*_):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("offline")
        return {"ok": True}

    schedule = schedules.create(
        name="retry",
        task_type=ScheduleType.SYSTEM_HEALTH,
        frequency=Frequency.ONCE,
        enabled=True,
    )
    runner = LocalScheduler(schedules, execute)
    failed = runner.run_now(schedule.schedule_id)
    retried = runner.retry_failed(failed.run_id)
    assert failed.status == "FAILED"
    assert failed.error_code == "SCHEDULE_EXECUTION_FAILED"
    assert retried.status == "SUCCESS"


def test_schedule_persists_across_repository_restart(
    state: DesktopStateRepository,
) -> None:
    first = ScheduleRepository(state)
    saved = first.create(
        name="persisted",
        task_type=ScheduleType.DAILY_REVIEW,
        frequency=Frequency.DAILY,
    )
    reopened_state = DesktopStateRepository(state.path)
    reopened = ScheduleRepository(reopened_state)
    assert reopened.get(saved.schedule_id).name == "persisted"


def test_refresh_requires_explicit_confirmation() -> None:
    controller = MarketRefreshController(lambda: {"rows": 5524})
    with pytest.raises(ValueError, match="explicit"):
        controller.refresh(explicitly_confirmed=False)


def test_refresh_is_batch_only_and_keeps_old_snapshot() -> None:
    result = MarketRefreshController(lambda: {"rows": 5524}).refresh(
        explicitly_confirmed=True
    )
    assert result.status == "SUCCESS"
    assert result.batch_interface_used is True
    assert result.fallback_symbol_requests == 0
    assert result.old_snapshot_retained is True


def test_refresh_failure_is_safely_classified() -> None:
    def fail() -> dict[str, object]:
        raise OSError("network unavailable")

    result = MarketRefreshController(fail).refresh(explicitly_confirmed=True)
    assert result.status == "FAILED"
    assert result.error_code == "NETWORK_ERROR"
    assert result.old_snapshot_retained is True
    assert result.fallback_symbol_requests == 0


def test_backtest_workspace_marks_research_boundaries() -> None:
    config = BacktestWorkspaceConfig(
        experiment_id="exp-1",
        symbols=["600000.SH"],
        start_date=date(2026, 1, 1),
        end_date=date(2026, 6, 30),
    )
    result = BacktestWorkspaceService().summarize_existing(
        config,
        metrics={"return": 0.01},
        equity_curve=[("2026-01-01", 1.0), ("2026-01-02", 0.9)],
        positions=[],
        sample_count=2,
    )
    assert result.research_only is True
    assert result.profitability_proven is False
    assert result.manual_ledger_written is False
    assert result.paper_trading_written is False
    assert set(result.risk_flags) == {
        "RAW_PRICE_RISK",
        "CORPORATE_ACTION_RISK",
        "SURVIVORSHIP_BIAS_RISK",
    }
    assert result.data_quality["sample_count"] == 2


def test_backtest_cancel_does_not_write_other_ledgers() -> None:
    service = BacktestWorkspaceService()
    service.cancel()
    result = service.summarize_existing(
        BacktestWorkspaceConfig(
            experiment_id="exp-2",
            symbols=["600000.SH"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 2),
        ),
        metrics={},
        equity_curve=[],
        positions=[],
        sample_count=0,
    )
    assert result.status == "CANCELLED"
    assert result.manual_ledger_written is False
    assert result.paper_trading_written is False


def test_manual_trade_controller_requires_exact_second_confirmation(
    manual_database,
) -> None:
    controller = ManualTradeDesktopController()
    with get_connection() as connection:
        before = int(
            connection.execute(
                "SELECT count(*) FROM manual_trades"
            ).fetchone()[0]
        )
    view = controller.preview(
        symbol="600000.SH",
        direction="BUY",
        quantity=100,
        price=10,
        trade_time=datetime.now().astimezone(),
        fee=5,
        account="manual-stage12c",
        client_trade_id="stage12c-manual-1",
    )
    assert view.impact.position_quantity_delta == 100
    assert view.impact.cash_delta == -1005
    assert view.impact.statement == "仅记录人工成交，不会向券商发送订单"
    with pytest.raises(ValueError, match="exact"):
        controller.confirm(view.preview.confirmation_id, "confirm")
    controller.confirm(
        view.preview.confirmation_id,
        view.required_confirmation,
    )
    with get_connection() as connection:
        after = int(
            connection.execute(
                "SELECT count(*) FROM manual_trades"
            ).fetchone()[0]
        )
    assert after == before + 1


def test_manual_trade_correction_preserves_original(
    manual_database,
) -> None:
    controller = ManualTradeDesktopController()
    original_view = controller.preview(
        symbol="000001.SZ",
        direction="BUY",
        quantity=100,
        price=12,
        trade_time=datetime.now().astimezone(),
        fee=5,
        account="manual-correction",
        client_trade_id="stage12c-correction-original",
    )
    original = controller.confirm(
        original_view.preview.confirmation_id,
        original_view.required_confirmation,
    ).trade
    correction = controller.correction_preview(
        original.trade_id,
        ManualTradeCorrectionRequest(
            correction_type="CORRECTION",
            reason="数量录入修正",
            replacement=ManualTradeReplacement(
                client_trade_id="stage12c-correction-replacement",
                side=ManualTradeSide.BUY,
                quantity=200,
                price=12,
                fees=5,
                taxes=0,
                traded_at=original.traded_at,
                notes="修正后数量",
            ),
        ),
    )
    corrected = controller.confirm(
        correction.confirmation_id,
        f"CONFIRM_MANUAL_TRADE:{correction.confirmation_id}",
    ).trade
    assert corrected.correction_of_trade_id == original.trade_id
    assert controller.service.get_trade(original.trade_id) == original
    assert controller.positions(portfolio_id="manual-correction")[0].quantity == 200


def test_manual_trade_reversal_preserves_audit_chain(
    manual_database,
) -> None:
    controller = ManualTradeDesktopController()
    original_view = controller.preview(
        symbol="600519.SH",
        direction="BUY",
        quantity=100,
        price=1000,
        trade_time=datetime.now().astimezone(),
        fee=5,
        account="manual-reversal",
        client_trade_id="stage12c-reversal-original",
    )
    original = controller.confirm(
        original_view.preview.confirmation_id,
        original_view.required_confirmation,
    ).trade
    reversal = controller.correction_preview(
        original.trade_id,
        ManualTradeCorrectionRequest(
            correction_type="REVERSAL",
            reason="成交归属错误",
            client_trade_id="stage12c-reversal-event",
        ),
    )
    reversed_trade = controller.confirm(
        reversal.confirmation_id,
        f"CONFIRM_MANUAL_TRADE:{reversal.confirmation_id}",
    ).trade
    trades = controller.trades(portfolio_id="manual-reversal")
    assert len(trades) == 2
    assert reversed_trade.correction_of_trade_id == original.trade_id
    assert controller.positions(portfolio_id="manual-reversal")[0].quantity == 0


def test_manual_trade_does_not_change_paper_account(
    manual_database,
) -> None:
    simulation = SimulationService(audit_store=TradingAuditStore())
    before = simulation.paper_account()
    controller = ManualTradeDesktopController()
    preview = controller.preview(
        symbol="002594.SZ",
        direction="BUY",
        quantity=100,
        price=100,
        trade_time=datetime.now().astimezone(),
        fee=5,
        account="manual-paper-isolation",
        client_trade_id="stage12c-paper-isolation",
    )
    controller.confirm(
        preview.preview.confirmation_id,
        preview.required_confirmation,
    )
    assert simulation.paper_account() == before


def test_daily_review_has_required_sections(
    review_database: Path,
    watchlists: WatchlistRepository,
    schedules: ScheduleRepository,
) -> None:
    service = DesktopReviewService(
        database_path=review_database,
        watchlists=watchlists,
        schedules=schedules,
    )
    report = service.daily_review(date.today())
    assert {
        "market_overview",
        "major_anomalies",
        "watchlist_performance",
        "manual_position_performance",
        "paper_account_performance",
        "scanner_tasks",
        "research_tasks",
        "decision_tasks",
        "veto_records",
        "missing_data",
        "stale_data",
        "risk_reminders",
        "next_day_todos",
    } <= set(report.sections)
    assert report.order_created is False
    assert report.trade_instruction_created is False


def test_premarket_missing_snapshot_is_explicit(
    review_database: Path,
    watchlists: WatchlistRepository,
    schedules: ScheduleRepository,
) -> None:
    report = DesktopReviewService(
        database_path=review_database,
        watchlists=watchlists,
        schedules=schedules,
    ).premarket_brief()
    assert set(report.risk_flags) == {"MISSING_DATA", "REFRESH_REQUIRED"}
    assert report.sections["system_status"] == "DEGRADED"


def test_premarket_stale_snapshot_is_not_realtime(
    review_database: Path,
    watchlists: WatchlistRepository,
    schedules: ScheduleRepository,
) -> None:
    with duckdb.connect(str(review_database)) as connection:
        connection.execute(
            "INSERT INTO market_snapshot_runs VALUES (?, 5524, 'COMPLETE')",
            [datetime.now().astimezone() - timedelta(days=1)],
        )
    report = DesktopReviewService(
        database_path=review_database,
        watchlists=watchlists,
        schedules=schedules,
    ).premarket_brief()
    assert set(report.risk_flags) == {"STALE_DATA", "REFRESH_REQUIRED"}
    assert report.sections["data_freshness"]["stale"] is True


def test_safe_executor_keeps_scanner_local(
    watchlists: WatchlistRepository,
    schedules: ScheduleRepository,
    review_database: Path,
) -> None:
    captured = {}

    class FakeScanner:
        def scan(self, request):
            captured["request"] = request
            return SimpleNamespace(
                model_dump=lambda **_: {
                    "run_id": "local",
                    "returned_count": 1,
                }
            )

    executor = SafeScheduledTaskExecutor(
        watchlists=watchlists,
        reviews=DesktopReviewService(
            database_path=review_database,
            watchlists=watchlists,
            schedules=schedules,
        ),
        scanner_factory=FakeScanner,
    )
    result = executor(
        ScheduleType.MARKET_SCAN,
        {"query": "成交额超过5亿元，排除ST", "top_n": 20},
    )
    request = captured["request"]
    assert request.analysis_mode == AnalysisMode.SCREENING
    assert request.allow_parser_model is False
    assert result["network_request_count"] == 0
    assert result["model_call_count"] == 0


def test_safe_executor_unconfigured_wecom_is_irrelevant(
    watchlists: WatchlistRepository,
    schedules: ScheduleRepository,
    review_database: Path,
) -> None:
    executor = SafeScheduledTaskExecutor(
        watchlists=watchlists,
        reviews=DesktopReviewService(
            database_path=review_database,
            watchlists=watchlists,
            schedules=schedules,
        ),
    )
    result = executor(ScheduleType.SYSTEM_HEALTH, {})
    assert result["status"] == "READ_ONLY_LOCAL_TASK"
    assert "wecom" not in result


def test_main_window_exposes_real_stage12c_pages(
    qt_app: QApplication,
    tmp_path: Path,
) -> None:
    paths = AppPaths.resolve(
        development=False,
        application_root=tmp_path / "Program Files" / "Hermes OPC",
        user_data_override=tmp_path / "用户 数据",
        env={"LOCALAPPDATA": str(tmp_path / "Local AppData")},
    )
    paths.ensure_user_directories()
    window = MainWindow(paths)
    widgets = [window.stack.widget(index) for index in range(window.stack.count())]
    assert len(PAGE_TITLES) == 21
    assert any(isinstance(item, WatchlistPage) for item in widgets)
    assert any(isinstance(item, ManualTradingPage) for item in widgets)
    assert any(isinstance(item, PaperTradingPage) for item in widgets)
    assert any(isinstance(item, BacktestPage) for item in widgets)
    assert sum(isinstance(item, ReviewPage) for item in widgets) == 2
    assert any(isinstance(item, SchedulerPage) for item in widgets)
    window.close()


def test_stage12c_adds_no_market_database_migration() -> None:
    migration_names = {
        path.name
        for path in (Path(__file__).resolve().parents[1] / "database" / "migrations").glob(
            "*.py"
        )
    }
    assert not any("0112" in name for name in migration_names)


def test_stage12c_pages_contain_no_live_order_surface() -> None:
    source = inspect.getsource(
        __import__(
            "desktop.workspace.trading_pages",
            fromlist=["trading_pages"],
        )
    ).casefold()
    assert "qmt" not in source
    assert "xtquant" not in source
    assert "submit_live" not in source
    assert "real_order" not in source
