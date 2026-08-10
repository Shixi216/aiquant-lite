from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from desktop.credentials import CredentialStore
from desktop.credentials import PROVIDER_ENVIRONMENT_KEYS
from desktop.paths import AppPaths
from desktop.settings import DesktopSettings
from desktop.setup_service import SetupService


class _DiagnosticsPage(QWizardPage):
    def __init__(self, wizard: FirstRunWizard) -> None:
        super().__init__()
        self.wizard = wizard

    def isComplete(self) -> bool:
        return self.wizard.diagnostics_passed


class FirstRunWizard(QWizard):
    def __init__(
        self,
        paths: AppPaths,
        settings: DesktopSettings,
        credential_store: CredentialStore,
        *,
        setup_service: SetupService | None = None,
    ) -> None:
        super().__init__()
        self.paths = paths
        self.settings = settings
        self.credential_store = credential_store
        self.setup_service = setup_service or SetupService(paths)
        self.diagnostics_passed = False
        self._provider_validation: dict[str, bool] = {}
        self.setWindowTitle("Hermes OPC 首次配置")
        self.resize(640, 480)
        self._build_location_page()
        self._build_credentials_page()
        self._build_diagnostics_page()

    def _build_location_page(self) -> None:
        page = QWizardPage()
        page.setTitle("用户数据与数据库")
        layout = QFormLayout(page)
        self.configuration_mode = QComboBox()
        self.configuration_mode.addItems(
            ("空白配置", ".env.example模板", "导入现有配置")
        )
        self.configuration_source = QLineEdit()
        choose_configuration = QPushButton("选择配置文件")
        choose_configuration.clicked.connect(self._choose_configuration)
        self.user_dir = QLineEdit(str(self.paths.user_data_dir))
        choose_user = QPushButton("选择目录")
        choose_user.clicked.connect(self._choose_user_dir)
        self.database_import = QLineEdit()
        choose_database = QPushButton("导入现有数据库副本")
        choose_database.clicked.connect(self._choose_database)
        layout.addRow("配置方式", self.configuration_mode)
        layout.addRow("配置来源", self.configuration_source)
        layout.addRow("", choose_configuration)
        layout.addRow("用户数据目录", self.user_dir)
        layout.addRow("", choose_user)
        layout.addRow("数据库来源", self.database_import)
        layout.addRow("", choose_database)
        layout.addRow("", QLabel("不会把开发数据库打入安装包；导入时复制并只读验证。"))
        for widget in (
            self.configuration_mode,
            self.configuration_source,
            self.user_dir,
            self.database_import,
        ):
            signal = (
                widget.currentTextChanged
                if isinstance(widget, QComboBox)
                else widget.textChanged
            )
            signal.connect(self._invalidate_diagnostics)
        self.addPage(page)

    def _build_credentials_page(self) -> None:
        page = QWizardPage()
        page.setTitle("Provider 凭据")
        layout = QFormLayout(page)
        self.credential_edits: dict[str, QLineEdit] = {}
        for provider in ("tushare", "deepseek", "qwen", "longcat", "mimo", "wecom"):
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.Password)
            edit.setPlaceholderText("留空表示暂不配置")
            edit.textChanged.connect(
                lambda _value, name=provider: self._credential_changed(name)
            )
            self.credential_edits[provider] = edit
            layout.addRow(provider, edit)
        layout.addRow("", QLabel("凭据不会显示、写入日志或普通配置文件。"))
        wecom_ready = self.settings.credentials.get("wecom")
        layout.addRow(
            "企业微信状态",
            QLabel(
                "CONFIGURED"
                if wecom_ready and wecom_ready.configured
                else "NOT_CONFIGURED"
            ),
        )
        self.provider_test_result = QLabel("尚未验证Provider")
        test_tushare = QPushButton("最小验证Tushare")
        test_tushare.clicked.connect(self._test_tushare)
        test_model = QPushButton("最小验证首个模型Provider")
        test_model.clicked.connect(self._test_first_model)
        layout.addRow("", self.provider_test_result)
        layout.addRow("", test_tushare)
        layout.addRow("", test_model)
        self.addPage(page)

    def _build_diagnostics_page(self) -> None:
        page = _DiagnosticsPage(self)
        self.diagnostics_page = page
        page.setTitle("本机诊断")
        layout = QVBoxLayout(page)
        self.diagnostic_result = QLabel("尚未检查")
        button = QPushButton("运行本机只读检查")
        button.clicked.connect(self._run_diagnostics)
        layout.addWidget(self.diagnostic_result)
        layout.addWidget(button)
        layout.addStretch(1)
        self.addPage(page)

    def _choose_user_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "选择用户数据目录", self.user_dir.text()
        )
        if selected:
            self.user_dir.setText(selected)

    def _choose_database(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择DuckDB数据库", "", "DuckDB (*.duckdb)"
        )
        if selected:
            self.database_import.setText(selected)

    def _choose_configuration(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", "", "Environment (*.env *.example);;All files (*)"
        )
        if selected:
            self.configuration_source.setText(selected)
            self.configuration_mode.setCurrentText("导入现有配置")

    def _test_tushare(self) -> None:
        result = self.setup_service.test_tushare(
            self.credential_edits["tushare"].text()
        )
        self._provider_validation["tushare"] = result.ok
        self._invalidate_diagnostics()
        self.provider_test_result.setText(f"tushare: {result.status}")

    def _test_first_model(self) -> None:
        for provider in ("deepseek", "qwen", "longcat", "mimo"):
            value = self.credential_edits[provider].text()
            if value:
                result = self.setup_service.test_model(provider, value)
                self._provider_validation[provider] = result.ok
                self._invalidate_diagnostics()
                self.provider_test_result.setText(f"{provider}: {result.status}")
                return
        self.provider_test_result.setText("model: NOT_CONFIGURED")

    def _run_diagnostics(self) -> None:
        selected_user_dir = Path(self.user_dir.text()).expanduser().resolve()
        selected_paths = AppPaths.resolve(
            development=self.paths.development,
            application_root=self.paths.application_root,
            user_data_override=selected_user_dir,
        )
        selected_paths.ensure_user_directories()
        database_source = self.database_import.text().strip()
        database_path = (
            Path(database_source).expanduser().resolve()
            if database_source
            else selected_paths.database_path
        )
        setup_service = SetupService(
            selected_paths,
            api_client=self.setup_service.api_client,
            model_probe=self.setup_service.model_probe,
        )
        service_checks = setup_service.test_services()
        local_checks = setup_service.local_checks(database_path=database_path)
        checks = [*service_checks, *local_checks]
        provider_checks_ok = all(
            not edit.text().strip()
            or provider == "wecom"
            or self._provider_validation.get(provider, False)
            for provider, edit in self.credential_edits.items()
        )
        self.diagnostics_passed = (
            all(item.ok for item in local_checks) and provider_checks_ok
        )
        self.diagnostic_result.setText(
            "\n".join(f"{item.name}: {item.status}" for item in checks)
        )
        self.diagnostics_page.completeChanged.emit()

    def _credential_changed(self, provider: str) -> None:
        self._provider_validation.pop(provider, None)
        self._invalidate_diagnostics()

    def _invalidate_diagnostics(self) -> None:
        self.diagnostics_passed = False
        page = getattr(self, "diagnostics_page", None)
        if page is not None:
            page.completeChanged.emit()

    def accept(self) -> None:
        if not self.diagnostics_passed:
            QMessageBox.critical(
                self,
                "配置检查未通过",
                "请先运行本机只读检查，并修复所有关键失败项。",
            )
            return
        selected_user_dir = Path(self.user_dir.text()).expanduser().resolve()
        if selected_user_dir != self.paths.user_data_dir:
            self.paths = AppPaths.resolve(
                development=self.paths.development,
                application_root=self.paths.application_root,
                user_data_override=selected_user_dir,
            )
            self.paths.ensure_user_directories()
            self.paths.persist_user_data_location()
            self.setup_service = SetupService(self.paths)
        self._import_configuration_metadata()
        for provider, edit in self.credential_edits.items():
            value = edit.text()
            if not value:
                continue
            self.credential_store.write(provider, value)
            self.settings.mark_credential(
                provider, provider, configured=True
            )
            edit.clear()
        database_source = self.database_import.text().strip()
        if database_source:
            try:
                self.paths.import_database_copy(database_source)
            except (OSError, ValueError):
                QMessageBox.critical(
                    self, "数据库导入失败", "数据库或Migration校验未通过。"
                )
                return
        self.settings.first_run_completed = True
        self.settings.save(self.paths.config_dir / "desktop-settings.json")
        super().accept()

    def _import_configuration_metadata(self) -> None:
        mode = self.configuration_mode.currentText()
        if mode == "空白配置":
            return
        source = self.configuration_source.text().strip()
        if mode == ".env.example模板" and not source:
            source = str(self.paths.application_root / ".env.example")
        path = Path(source)
        if not path.is_file():
            return
        provider_by_variable = {
            variable: provider
            for provider, variable in PROVIDER_ENVIRONMENT_KEYS.items()
        }
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            provider = provider_by_variable.get(key.strip().upper())
            value = value.strip().strip("'\"")
            if provider and value:
                self.credential_store.write(provider, value)
                self.settings.mark_credential(
                    provider, provider, configured=True
                )
