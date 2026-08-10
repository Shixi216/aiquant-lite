from __future__ import annotations

import sys

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QWidget,
    QWizard,
)

from desktop.api_client import DesktopApiClient
from desktop.credentials import (
    CredentialStore,
    WindowsCredentialStore,
    child_process_environment,
)
from desktop.first_run import FirstRunWizard
from desktop.health import DesktopHealthService
from desktop.pages import (
    AboutPage,
    HomePage,
    LogsPage,
    SettingsPage,
    SystemStatusPage,
)
from desktop.paths import AppPaths
from desktop.runtime import (
    activate_configured_credentials,
    activate_runtime_paths,
)
from desktop.service_supervisor import ServiceSupervisor
from desktop.settings import DesktopSettings
from desktop.workspace.pages import ConversationPage, SkillCenterPage
from desktop.workspace.model_research import RouterResearchClient
from desktop.workspace.reviews import DesktopReviewService
from desktop.workspace.safe_executor import SafeScheduledTaskExecutor
from desktop.workspace.scheduler import LocalScheduler, ScheduleRepository
from desktop.workspace.service import TaskCenterService
from desktop.workspace.state import DesktopStateRepository
from desktop.workspace.stage12d_pages import (
    CandidateDetailPage,
    DecisionSupportPage,
    ExperimentReportPage,
    ManualPositionsPage,
    MarketScanPage,
    PositionRiskPage,
    TaskSkillPage,
)
from desktop.workspace.trading_pages import (
    BacktestPage,
    ManualTradingPage,
    PaperTradingPage,
    ReviewPage,
    SchedulerPage,
    WatchlistPage,
)
from desktop.workspace.watchlists import WatchlistRepository


PAGE_TITLES = (
    "首页",
    "对话任务中心",
    "技能中心",
    "市场扫描",
    "候选详情",
    "个股研究",
    "正式决策",
    "自选股",
    "人工成交",
    "人工持仓",
    "持仓风险",
    "Paper Trading",
    "回测",
    "每日复盘",
    "盘前简报",
    "调度中心",
    "实验报告",
    "系统状态",
    "日志",
    "设置",
    "关于与限制",
)


def initial_window_size(
    available_width: int,
    available_height: int,
) -> QSize:
    """Fit the initial window inside the usable screen at high DPI."""
    width = min(1280, max(560, int(available_width * 0.86)))
    height = min(800, max(320, int(available_height * 0.86)))
    return QSize(width, height)


class MainWindow(QMainWindow):
    def __init__(
        self,
        paths: AppPaths,
        *,
        api_client: DesktopApiClient | None = None,
        credential_store: CredentialStore | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.desktop_settings = DesktopSettings.load(
            paths.config_dir / "desktop-settings.json"
        )
        self.credential_store = credential_store or WindowsCredentialStore()
        client = api_client or DesktopApiClient()
        self.supervisor = ServiceSupervisor(paths, api_client=client)
        self.health_service = DesktopHealthService(paths, api_client=client)
        self.workspace_repository = DesktopStateRepository(
            paths.desktop_state_path
        )
        self.task_center_service = TaskCenterService(
            self.workspace_repository,
            research_model_client=RouterResearchClient(
                router_url=client.router_url,
            ),
        )
        self.watchlist_repository = WatchlistRepository(
            self.workspace_repository
        )
        self.schedule_repository = ScheduleRepository(
            self.workspace_repository
        )
        self.review_service = DesktopReviewService(
            database_path=paths.database_path,
            watchlists=self.watchlist_repository,
            schedules=self.schedule_repository,
        )
        self.scheduler = LocalScheduler(
            self.schedule_repository,
            SafeScheduledTaskExecutor(
                watchlists=self.watchlist_repository,
                reviews=self.review_service,
            ),
        )
        self.setWindowTitle("Hermes OPC")
        screen = QGuiApplication.primaryScreen()
        available = (
            screen.availableGeometry().size()
            if screen is not None
            else QSize(1280, 800)
        )
        self.resize(
            initial_window_size(available.width(), available.height())
        )
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self.navigation = QListWidget()
        self.navigation.setMinimumWidth(120)
        self.navigation.setMaximumWidth(180)
        self.stack = QStackedWidget()
        for title in PAGE_TITLES:
            item = QListWidgetItem(title)
            item.setSizeHint(QSize(120, 42))
            self.navigation.addItem(item)
            self.stack.addWidget(self._page(title))
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.navigation.setCurrentRow(0)
        layout.addWidget(self.navigation)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(container)

    def _page(self, title: str) -> QWidget:
        if title == "首页":
            return HomePage(self.health_service)
        if title == "对话任务中心":
            return ConversationPage(
                self.workspace_repository,
                self.task_center_service,
                exports_dir=self.paths.exports_dir,
            )
        if title == "技能中心":
            return SkillCenterPage(
                self.workspace_repository,
                self.task_center_service,
            )
        if title == "市场扫描":
            return MarketScanPage(exports_dir=self.paths.exports_dir)
        if title == "候选详情":
            return CandidateDetailPage()
        if title == "个股研究":
            return TaskSkillPage(
                title="个股研究：读取本地点时五维证据",
                prompt_prefix="分析 ",
                service=self.task_center_service,
                repository=self.workspace_repository,
                placeholder="输入股票名称或代码",
            )
        if title == "正式决策":
            return DecisionSupportPage(
                self.task_center_service,
                self.workspace_repository,
            )
        if title == "自选股":
            return WatchlistPage(
                self.watchlist_repository,
                exports_dir=self.paths.exports_dir,
            )
        if title == "人工成交":
            return ManualTradingPage()
        if title == "人工持仓":
            return ManualPositionsPage()
        if title == "持仓风险":
            return PositionRiskPage()
        if title == "Paper Trading":
            return PaperTradingPage()
        if title == "回测":
            return BacktestPage()
        if title == "每日复盘":
            return ReviewPage(self.review_service, premarket=False)
        if title == "盘前简报":
            return ReviewPage(self.review_service, premarket=True)
        if title == "调度中心":
            return SchedulerPage(
                self.schedule_repository,
                self.scheduler,
            )
        if title == "实验报告":
            return ExperimentReportPage(self.paths.reports_dir)
        if title == "系统状态":
            return SystemStatusPage(
                self.supervisor,
                environment_provider=self._child_environment,
            )
        if title == "日志":
            return LogsPage(self.paths)
        if title == "设置":
            return SettingsPage(
                self.paths,
                self.supervisor,
                self.desktop_settings,
                self.credential_store,
            )
        if title == "关于与限制":
            return AboutPage()
        raise KeyError(f"unmapped desktop page: {title}")

    def _child_environment(self) -> dict[str, str]:
        configured = [
            name
            for name, metadata in self.desktop_settings.credentials.items()
            if metadata.configured
        ]
        try:
            return child_process_environment(
                self.credential_store,
                configured,
            )
        except OSError:
            return dict(__import__("os").environ)


def main(*, paths: AppPaths | None = None) -> int:
    app = QApplication(sys.argv)
    paths = paths or AppPaths.resolve()
    paths.ensure_user_directories()
    activate_runtime_paths(paths)
    settings_path = paths.config_dir / "desktop-settings.json"
    settings = DesktopSettings.load(settings_path)
    if not settings.first_run_completed:
        wizard = FirstRunWizard(
            paths,
            settings,
            WindowsCredentialStore(),
        )
        if wizard.exec() != QWizard.Accepted:
            return 0
        paths = wizard.paths
        activate_runtime_paths(paths)
        activate_configured_credentials(paths)
    window = MainWindow(paths)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
