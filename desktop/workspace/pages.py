from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from desktop.workspace.service import TaskCenterService
from desktop.task_runner import BackgroundTask
from desktop.workspace.state import DesktopStateRepository
from router.integration.skills import SKILL_REGISTRY, SkillRegistry


class ConversationPage(QWidget):
    def __init__(
        self,
        repository: DesktopStateRepository,
        service: TaskCenterService,
        *,
        exports_dir: Path,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.service = service
        self.exports_dir = exports_dir
        self.current_conversation_id: str | None = None
        self.current_task_id: str | None = None
        self._tasks: set[BackgroundTask] = set()
        root = QVBoxLayout(self)
        heading = QLabel("对话任务")
        heading.setObjectName("pageTitle")
        root.addWidget(heading)
        splitter = QSplitter(Qt.Horizontal)
        root.addWidget(splitter, 1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索历史会话")
        self.search.textChanged.connect(self.refresh_conversations)
        left_layout.addWidget(self.search)
        self.conversations = QListWidget()
        self.conversations.currentItemChanged.connect(self._conversation_selected)
        left_layout.addWidget(self.conversations, 1)
        new_button = QPushButton("新建会话")
        new_button.clicked.connect(self.new_conversation)
        rename_button = QPushButton("重命名")
        rename_button.clicked.connect(self.rename_conversation)
        pin_button = QPushButton("固定/取消固定")
        pin_button.clicked.connect(self.toggle_pin)
        delete_button = QPushButton("清除会话")
        delete_button.clicked.connect(self.delete_conversation)
        for button in (new_button, rename_button, pin_button, delete_button):
            left_layout.addWidget(button)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        right_layout.addWidget(self.messages, 3)
        self.timeline = QPlainTextEdit()
        self.timeline.setReadOnly(True)
        self.timeline.setPlaceholderText("任务进度时间线")
        right_layout.addWidget(self.timeline, 2)
        self.task_status = QLabel("READY")
        right_layout.addWidget(self.task_status)
        input_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入研究任务或继续当前会话")
        self.input.returnPressed.connect(self.send)
        send_button = QPushButton("发送")
        send_button.clicked.connect(self.send)
        input_row.addWidget(self.input, 1)
        input_row.addWidget(send_button)
        right_layout.addLayout(input_row)
        actions = QHBoxLayout()
        cancel_button = QPushButton("取消任务")
        cancel_button.clicked.connect(self.cancel_task)
        retry_button = QPushButton("重试失败步骤")
        retry_button.clicked.connect(self.retry_task)
        template_button = QPushButton("保存任务模板")
        template_button.clicked.connect(self.save_template)
        export_button = QPushButton("导出会话报告")
        export_button.clicked.connect(self.export_conversation)
        for button in (
            cancel_button,
            retry_button,
            template_button,
            export_button,
        ):
            actions.addWidget(button)
        right_layout.addLayout(actions)
        splitter.addWidget(right)
        splitter.setSizes([260, 850])
        self.refresh_conversations()
        if self.conversations.count() == 0:
            self.new_conversation()

    def refresh_conversations(self) -> None:
        selected = self.current_conversation_id
        self.conversations.clear()
        for conversation in self.repository.list_conversations(self.search.text()):
            prefix = "★ " if conversation.pinned else ""
            item = QListWidgetItem(prefix + conversation.title)
            item.setData(Qt.UserRole, conversation.conversation_id)
            self.conversations.addItem(item)
            if conversation.conversation_id == selected:
                self.conversations.setCurrentItem(item)
        if self.conversations.currentItem() is None and self.conversations.count():
            self.conversations.setCurrentRow(0)

    def new_conversation(self) -> None:
        conversation = self.service.new_conversation()
        self.current_conversation_id = conversation.conversation_id
        self.refresh_conversations()
        for index in range(self.conversations.count()):
            item = self.conversations.item(index)
            if item.data(Qt.UserRole) == conversation.conversation_id:
                self.conversations.setCurrentItem(item)
                break

    def _conversation_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if current is None:
            return
        self.current_conversation_id = str(current.data(Qt.UserRole))
        self._render_messages()
        self.timeline.clear()
        self.current_task_id = None

    def _render_messages(self) -> None:
        if not self.current_conversation_id:
            self.messages.clear()
            return
        lines = [
            f"[{item.role}] {item.content}"
            for item in self.repository.messages(self.current_conversation_id)
        ]
        self.messages.setPlainText("\n\n".join(lines))

    def send(self) -> None:
        if not self.current_conversation_id:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.task_status.setText("RUNNING")
        self.timeline.setPlainText("任务已提交，正在执行。")
        conversation_id = self.current_conversation_id
        task = BackgroundTask(
            lambda: self.service.submit_message(
                conversation_id,
                text,
                on_created=self._task_created,
            )
        )
        self._tasks.add(task)
        task.signals.succeeded.connect(self._task_finished)
        task.signals.failed.connect(self._task_failed)
        task.signals.finished.connect(
            lambda current=task: self._tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)

    def _task_created(self, task) -> None:
        self.current_task_id = task.task_id

    def _task_finished(self, task) -> None:
        self.task_status.setText(task.status.value)
        self.refresh_conversations()
        self.current_task_id = task.task_id
        self._render_messages()
        self._render_task(task.task_id)

    def _task_failed(self, error_code: str) -> None:
        self.task_status.setText(error_code)
        self.timeline.setPlainText(
            f"任务执行失败。\n错误代码：{error_code}\n未生成交易指令。"
        )
        self._render_messages()

    def _render_task(self, task_id: str) -> None:
        task = self.repository.task(task_id)
        lines = [
            f"任务状态：{task.status.value}",
            f"技能：{task.skill_id}",
            f"请求：{task.request_id}",
            "",
        ]
        lines.extend(
            f"{step.sequence}. {step.kind.value} · {step.label}"
            for step in self.repository.steps(task_id)
        )
        if task.error_code:
            failed_steps = [
                step
                for step in self.repository.steps(task_id)
                if step.kind.value == "FAILED"
            ]
            reason = (
                failed_steps[-1].payload.get("sanitized_reason")
                if failed_steps
                else None
            )
            lines.extend(("", f"失败原因：{task.error_code}"))
            if reason:
                lines.append(f"说明：{reason}")
        if task.result_card is not None:
            lines.extend(
                [
                    "",
                    f"结果卡片：{task.result_card.title}",
                    f"状态：{task.result_card.status}",
                    f"数据截止时间：{task.result_card.data_cutoff.isoformat()}",
                ]
            )
            if task.result_card.risk_flags:
                lines.append(
                    "风险：" + "；".join(task.result_card.risk_flags)
                )
            if task.result_card.warnings:
                lines.append(
                    "警告：" + "；".join(task.result_card.warnings)
                )
            for key, value in task.result_card.fields.items():
                if isinstance(value, (str, int, float, bool)) or value is None:
                    display = "缺失" if value is None else str(value)
                elif isinstance(value, list):
                    display = f"{len(value)}项"
                elif isinstance(value, dict):
                    display = f"{len(value)}个字段"
                else:
                    display = type(value).__name__
                lines.append(f"{key}：{display}")
        self.timeline.setPlainText("\n".join(lines))

    def rename_conversation(self) -> None:
        if not self.current_conversation_id:
            return
        title, ok = QInputDialog.getText(self, "重命名会话", "新名称")
        if ok and title.strip():
            self.repository.rename_conversation(
                self.current_conversation_id, title
            )
            self.refresh_conversations()

    def toggle_pin(self) -> None:
        if not self.current_conversation_id:
            return
        conversation = next(
            item
            for item in self.repository.list_conversations()
            if item.conversation_id == self.current_conversation_id
        )
        self.repository.pin_conversation(
            conversation.conversation_id, not conversation.pinned
        )
        self.refresh_conversations()

    def delete_conversation(self) -> None:
        if not self.current_conversation_id:
            return
        answer = QMessageBox.question(
            self,
            "清除会话",
            "只删除桌面会话状态，不删除底层市场事实。是否继续？",
        )
        if answer != QMessageBox.Yes:
            return
        self.repository.delete_conversation(self.current_conversation_id)
        self.current_conversation_id = None
        self.refresh_conversations()
        if self.conversations.count() == 0:
            self.new_conversation()

    def cancel_task(self) -> None:
        if self.current_task_id:
            task = self.service.cancel(self.current_task_id)
            self.task_status.setText(task.status.value)
            self._render_task(task.task_id)

    def retry_task(self) -> None:
        if not self.current_task_id:
            return
        try:
            task = self.service.retry(self.current_task_id)
        except ValueError:
            return
        self.current_task_id = task.task_id
        self._render_task(task.task_id)

    def save_template(self) -> None:
        if not self.current_task_id:
            return
        task = self.repository.task(self.current_task_id)
        self.repository.save_template(
            task.skill_id,
            task.skill_id,
            task.input_payload,
            pinned=True,
        )

    def export_conversation(self) -> None:
        if not self.current_conversation_id:
            return
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        default = self.exports_dir / f"{self.current_conversation_id}.md"
        selected, _ = QFileDialog.getSaveFileName(
            self, "导出会话报告", str(default), "Markdown (*.md)"
        )
        if selected:
            self.service.export_conversation(
                self.current_conversation_id, Path(selected)
            )


class SkillCenterPage(QWidget):
    def __init__(
        self,
        repository: DesktopStateRepository | None = None,
        service: TaskCenterService | None = None,
        registry: SkillRegistry = SKILL_REGISTRY,
    ) -> None:
        super().__init__()
        self.registry = registry
        self.repository = repository
        self.service = service
        self.conversation_id: str | None = None
        self.current_task_id: str | None = None
        self._tasks: set[BackgroundTask] = set()
        layout = QVBoxLayout(self)
        heading = QLabel("技能中心")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        splitter = QSplitter(Qt.Horizontal)
        self.skills = QListWidget()
        splitter.addWidget(self.skills)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        self.skill_name = QLabel()
        self.skill_name.setStyleSheet("font-size:18px;font-weight:700")
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.policy = QLabel()
        self.policy.setWordWrap(True)
        detail_layout.addWidget(self.skill_name)
        detail_layout.addWidget(self.description)
        detail_layout.addWidget(self.policy)
        form = QFormLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("输入任务说明")
        self.symbols = QLineEdit()
        self.symbols.setPlaceholderText(
            "股票代码，逗号分隔，例如 300750.SZ"
        )
        self.data_cutoff = QDateTimeEdit(datetime.now().astimezone())
        self.data_cutoff.setCalendarPopup(True)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText("仅需二次确认的技能填写")
        form.addRow("任务说明", self.query)
        form.addRow("股票代码", self.symbols)
        form.addRow("数据截止时间", self.data_cutoff)
        form.addRow("确认文本", self.confirmation)
        detail_layout.addLayout(form)
        actions = QHBoxLayout()
        self.run_button = QPushButton("运行技能")
        self.run_button.clicked.connect(self.run_selected)
        self.cancel_button = QPushButton("取消当前任务")
        self.cancel_button.clicked.connect(self.cancel_current)
        actions.addWidget(self.run_button)
        actions.addWidget(self.cancel_button)
        detail_layout.addLayout(actions)
        self.status = QLabel("READY")
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.raw_toggle = QPushButton("开发者详情（JSON）")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.toggled.connect(self._toggle_raw)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        detail_layout.addWidget(self.status)
        detail_layout.addWidget(self.result, 1)
        detail_layout.addWidget(self.raw_toggle)
        detail_layout.addWidget(self.details, 1)
        splitter.addWidget(detail_panel)
        splitter.setSizes([280, 760])
        layout.addWidget(splitter, 1)
        for contract in registry.list():
            item = QListWidgetItem(contract.name)
            item.setData(Qt.UserRole, contract.skill_id)
            self.skills.addItem(item)
        self.skills.currentItemChanged.connect(self._selected)
        if self.skills.count():
            self.skills.setCurrentRow(0)

    def _selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if current is None:
            return
        contract = self.registry.get(str(current.data(Qt.UserRole)))
        self.skill_name.setText(contract.name)
        self.description.setText(contract.description)
        self.policy.setText(
            " · ".join(
                (
                    f"类别 {contract.category.value}",
                    f"网络 {contract.network_policy}",
                    f"模型 {contract.model_policy}",
                    f"最长 {contract.timeout} 秒",
                    "需要二次确认"
                    if contract.requires_confirmation
                    else "无需二次确认",
                )
            )
        )
        self.confirmation.setVisible(contract.requires_confirmation)
        self.details.setPlainText(
            json.dumps(contract.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )

    def _selected_contract(self):
        current = self.skills.currentItem()
        if current is None:
            return None
        return self.registry.get(str(current.data(Qt.UserRole)))

    def run_selected(self) -> None:
        contract = self._selected_contract()
        if contract is None:
            return
        if self.repository is None or self.service is None:
            self.status.setText("SKILL_EXECUTOR_NOT_READY")
            return
        symbols = [
            item.strip().upper()
            for item in re.split(r"[,，\s]+", self.symbols.text())
            if item.strip()
        ]
        if any(
            re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", item) is None
            for item in symbols
        ):
            self.status.setText("INVALID_SYMBOL")
            return
        if len(symbols) > 30:
            self.status.setText("TOO_MANY_SYMBOLS")
            return
        if self.conversation_id is None:
            self.conversation_id = self.service.new_conversation(
                "技能中心"
            ).conversation_id
        text_parts = [self.query.text().strip() or contract.name]
        if symbols:
            text_parts.append(" ".join(symbols))
        if self.confirmation.text().strip():
            text_parts.append(self.confirmation.text().strip())
        text = " ".join(text_parts)
        self.status.setText("RUNNING")
        self.result.setPlainText("技能正在执行，请稍候。")
        task = BackgroundTask(
            lambda: self.service.submit_message(
                self.conversation_id or "",
                text,
                skill_id_override=contract.skill_id,
            )
        )
        self._tasks.add(task)
        task.signals.succeeded.connect(self._finished)
        task.signals.failed.connect(self._failed)
        task.signals.finished.connect(
            lambda current=task: self._tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)

    def _finished(self, task) -> None:
        self.current_task_id = task.task_id
        self.status.setText(task.status.value)
        card = task.result_card
        if card is None:
            self.result.setPlainText(
                f"任务未完成。\n错误代码：{task.error_code or 'TASK_FAILED'}"
            )
            self.details.setPlainText(
                json.dumps(
                    {
                        "task_id": task.task_id,
                        "status": task.status.value,
                        "error_code": task.error_code,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        lines = [
            card.title,
            f"状态：{card.status}",
            f"数据截止时间：{card.data_cutoff.isoformat()}",
        ]
        if card.warnings:
            lines.append("警告：" + "；".join(card.warnings))
        if card.risk_flags:
            lines.append("风险：" + "；".join(card.risk_flags))
        for key, value in card.fields.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                lines.append(f"{key}：{value if value is not None else '缺失'}")
            elif isinstance(value, list):
                lines.append(f"{key}：{len(value)}项")
            elif isinstance(value, dict):
                lines.append(f"{key}：{len(value)}个字段")
        self.result.setPlainText("\n".join(lines))
        self.details.setPlainText(
            json.dumps(card.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )

    def _failed(self, error_code: str) -> None:
        self.status.setText(error_code)
        self.result.setPlainText(
            f"技能执行失败。\n错误代码：{error_code}\n未生成交易指令。"
        )

    def cancel_current(self) -> None:
        if (
            self.current_task_id
            and self.service is not None
        ):
            task = self.service.cancel(self.current_task_id)
            self.status.setText(task.status.value)

    def _toggle_raw(self, visible: bool) -> None:
        self.details.setVisible(visible)
        self.raw_toggle.setText(
            "收起开发者详情" if visible else "开发者详情（JSON）"
        )
