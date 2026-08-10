from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from desktop.health import DesktopHealthService
from desktop.build_info import load_build_info
from desktop.backup import DatabaseBackupService
from desktop.credentials import CredentialStore
from desktop.paths import AppPaths
from desktop.service_supervisor import ServiceSupervisor
from desktop.settings import DesktopSettings
from desktop.setup_service import SetupService
from desktop.task_runner import BackgroundTask


SECRET_PATTERN = re.compile(
    r"(?im)^(\s*[A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|PASSWORD)\s*=\s*).+$"
)
BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+\S+")


def sanitize_text(text: str) -> str:
    text = SECRET_PATTERN.sub(r"\1[REDACTED]", text)
    return BEARER_PATTERN.sub("Bearer [REDACTED]", text)


class PlaceholderPage(QWidget):
    def __init__(self, title: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        layout.addWidget(QLabel("后续阶段实现"))
        layout.addStretch(1)


class HomePage(QWidget):
    ORDERED_FIELDS = (
        "router",
        "data_hub",
        "finance_mcp",
        "database",
        "migrations",
        "latest_snapshot_time",
        "snapshot_stale",
        "realtime_coverage",
        "history_20d",
        "history_60d",
        "industry_coverage",
        "fundamental_coverage",
        "sentiment_coverage",
        "policy_news_coverage",
        "capital_flow_coverage",
        "formal_strategy",
        "formal_strategy_status",
        "shadow_formal_weight",
        "live_trading",
        "version",
        "working_tree_dirty",
    )

    def __init__(self, health_service: DesktopHealthService) -> None:
        super().__init__()
        self.health_service = health_service
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        heading = QLabel("首页")
        heading.setObjectName("pageTitle")
        self.overall = QLabel("NOT_READY")
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        bar.addWidget(heading)
        bar.addStretch(1)
        bar.addWidget(self.overall)
        bar.addWidget(refresh)
        layout.addLayout(bar)
        form = QFormLayout()
        self.labels: dict[str, QLabel] = {}
        for field in self.ORDERED_FIELDS:
            label = QLabel("—")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.labels[field] = label
            form.addRow(field, label)
        container = QWidget()
        container.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        layout.addWidget(scroll)

    def refresh(self) -> None:
        health = self.health_service.collect()
        self.overall.setText(health.overall_status)
        for name, label in self.labels.items():
            value = health.values.get(name)
            label.setText("—" if value is None else str(value))


class SystemStatusPage(QWidget):
    def __init__(
        self,
        supervisor: ServiceSupervisor,
        *,
        environment_provider: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self.supervisor = supervisor
        self.environment_provider = environment_provider
        self._tasks: set[BackgroundTask] = set()
        layout = QVBoxLayout(self)
        heading = QLabel("系统状态")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        refresh = QPushButton("检查服务")
        refresh.clicked.connect(self.refresh)
        start = QPushButton("启动 Router 与 Data Hub")
        start.clicked.connect(self.start_services)
        stop = QPushButton("停止 Router 与 Data Hub")
        stop.clicked.connect(self.stop_services)
        restart = QPushButton("重启 Router 与 Data Hub")
        restart.clicked.connect(self.restart_services)
        doctor = QPushButton("运行只读诊断")
        doctor.clicked.connect(self.doctor)
        actions = QHBoxLayout()
        for button in (refresh, start, stop, restart, doctor):
            actions.addWidget(button)
        layout.addLayout(actions)

    def refresh(self) -> None:
        statuses = [
            self.supervisor.status(name)
            for name in ("router", "data_hub", "finance_mcp")
        ]
        self.output.setPlainText(
            "\n".join(
                f"{item.name}: {item.state} {item.detail}".rstrip()
                for item in statuses
            )
        )

    def _run(self, function: Callable[[], object]) -> None:
        task = BackgroundTask(function)
        self._tasks.add(task)
        task.signals.succeeded.connect(
            lambda value: self.output.setPlainText(str(value))
        )
        task.signals.failed.connect(self.output.setPlainText)
        task.signals.finished.connect(
            lambda current=task: self._tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)

    def _environment(self) -> dict[str, str] | None:
        return self.environment_provider() if self.environment_provider else None

    def start_services(self) -> None:
        environment = self._environment()
        self._run(
            lambda: [
                self.supervisor.start(
                    name,
                    child_environment=environment,
                )
                for name in ("router", "data_hub")
            ]
        )

    def stop_services(self) -> None:
        self._run(
            lambda: [
                self.supervisor.stop(name)
                for name in ("router", "data_hub")
            ]
        )

    def restart_services(self) -> None:
        environment = self._environment()
        self._run(
            lambda: [
                self.supervisor.restart(
                    name,
                    child_environment=environment,
                )
                for name in ("router", "data_hub")
            ]
        )

    def doctor(self) -> None:
        self._run(self.supervisor.doctor)


class LogsPage(QWidget):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        layout = QVBoxLayout(self)
        heading = QLabel("日志")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        refresh = QPushButton("刷新日志")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)

    def refresh(self) -> None:
        fragments: list[str] = []
        if self.paths.logs_dir.is_dir():
            for path in sorted(self.paths.logs_dir.glob("*.log"))[-10:]:
                try:
                    lines = path.read_text(
                        encoding="utf-8", errors="replace"
                    ).splitlines()[-200:]
                    fragments.append(f"## {path.name}\n" + "\n".join(lines))
                except OSError:
                    fragments.append(f"## {path.name}\n[READ_FAILED]")
        self.output.setPlainText(sanitize_text("\n\n".join(fragments)))


class SettingsPage(QWidget):
    PROVIDERS = ("tushare", "deepseek", "qwen", "longcat", "mimo", "wecom")

    def __init__(
        self,
        paths: AppPaths,
        supervisor: ServiceSupervisor,
        desktop_settings: DesktopSettings,
        credential_store: CredentialStore,
        *,
        setup_service: SetupService | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.desktop_settings = desktop_settings
        self.credential_store = credential_store
        self.setup_service = setup_service or SetupService(paths)
        self.backup_service = DatabaseBackupService(paths, supervisor)
        self._background_tasks: set[BackgroundTask] = set()
        layout = QFormLayout(self)
        layout.addRow("设置", QLabel("凭据仅存储于 Windows 凭据管理器"))
        layout.addRow("用户数据目录", QLabel(str(paths.user_data_dir)))
        layout.addRow("数据库", QLabel(str(paths.database_path)))
        self.provider_edits: dict[str, QLineEdit] = {}
        self.provider_status: dict[str, QLabel] = {}
        for provider in self.PROVIDERS:
            layout.addRow(provider, self._provider_row(provider))
        backup = QPushButton("创建数据库备份")
        backup.clicked.connect(self.create_backup)
        layout.addRow("安全备份", backup)
        self.backup_status = QLabel(
            "创建前必须停止 Router 和 Data Hub；恢复按故障恢复说明人工执行"
        )
        self.backup_status.setWordWrap(True)
        layout.addRow("备份状态", self.backup_status)

    def _provider_row(self, provider: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit()
        edit.setEchoMode(QLineEdit.Password)
        edit.setPlaceholderText("留空表示不更改")
        self.provider_edits[provider] = edit
        layout.addWidget(edit, 1)
        save = QPushButton("保存")
        save.clicked.connect(
            lambda _checked=False, name=provider: self.save_credential(name)
        )
        layout.addWidget(save)
        if provider != "wecom":
            test = QPushButton("验证")
            test.clicked.connect(
                lambda _checked=False, name=provider: self.test_credential(name)
            )
            layout.addWidget(test)
        delete = QPushButton("删除")
        delete.clicked.connect(
            lambda _checked=False, name=provider: self.confirm_delete_credential(name)
        )
        layout.addWidget(delete)
        metadata = self.desktop_settings.credentials.get(provider)
        status = QLabel(
            "已配置" if metadata is not None and metadata.configured else "未配置"
        )
        status.setMinimumWidth(110)
        self.provider_status[provider] = status
        layout.addWidget(status)
        return row

    @property
    def settings_path(self) -> Path:
        return self.paths.config_dir / "desktop-settings.json"

    def save_credential(self, provider: str) -> None:
        value = self.provider_edits[provider].text().strip()
        if not value:
            self.provider_status[provider].setText("未输入新凭据")
            return
        try:
            self.credential_store.write(provider, value)
            self.desktop_settings.mark_credential(
                provider,
                provider,
                configured=True,
            )
            self.desktop_settings.save(self.settings_path)
            self.provider_edits[provider].clear()
            self.provider_status[provider].setText("已保存；重启服务生效")
        except (OSError, ValueError):
            self.provider_status[provider].setText("保存失败")

    def test_credential(self, provider: str) -> None:
        value = self.provider_edits[provider].text().strip()
        try:
            value = value or (self.credential_store.read(provider) or "")
            result = (
                self.setup_service.test_tushare(value)
                if provider == "tushare"
                else self.setup_service.test_model(provider, value)
            )
        except (OSError, ValueError):
            self.provider_status[provider].setText("PROVIDER_ERROR")
            return
        self.desktop_settings.mark_credential(
            provider,
            provider,
            configured=bool(value),
            test_status=result.status,
            tested_at=datetime.now().astimezone(),
        )
        self.desktop_settings.save(self.settings_path)
        self.provider_edits[provider].clear()
        self.provider_status[provider].setText(result.status)

    def confirm_delete_credential(self, provider: str) -> None:
        answer = QMessageBox.question(
            self,
            "删除Provider凭据",
            f"确认删除 {provider} 的本地凭据？相关服务需要重启。",
        )
        if answer == QMessageBox.Yes:
            self.delete_credential(provider)

    def delete_credential(self, provider: str) -> None:
        try:
            self.credential_store.delete(provider)
            self.desktop_settings.mark_credential(
                provider,
                provider,
                configured=False,
                test_status="NOT_CONFIGURED",
            )
            self.desktop_settings.save(self.settings_path)
            self.provider_edits[provider].clear()
            self.provider_status[provider].setText("NOT_CONFIGURED")
        except (OSError, ValueError):
            self.provider_status[provider].setText("删除失败")

    def create_backup(self) -> None:
        self.backup_status.setText("BACKUP_RUNNING")
        task = BackgroundTask(self.backup_service.create)
        self._background_tasks.add(task)
        task.signals.succeeded.connect(
            lambda result: self.backup_status.setText(
                f"BACKUP_OK | {result.created_at} | "
                f"{result.size_bytes} bytes | SHA-256已校验 | 只读打开通过"
            )
        )
        task.signals.failed.connect(self.backup_status.setText)
        task.signals.finished.connect(
            lambda current=task: self._background_tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)


class AboutPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        info = load_build_info()
        layout.addWidget(QLabel(f"Hermes OPC {info['application_version']}"))
        for key in (
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
        ):
            layout.addWidget(QLabel(f"{key}: {info[key]}"))
        limitation = QLabel(
            "A股研究与辅助决策工作台；不支持实盘交易。"
            "正式60/40覆盖不足，当前结果不能证明稳定盈利。"
        )
        limitation.setWordWrap(True)
        layout.addWidget(limitation)
        layout.addStretch(1)
