from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from desktop.task_runner import BackgroundTask
from desktop.workspace.manual_trading import ManualTradeDesktopController
from desktop.workspace.service import TaskCenterService
from desktop.workspace.state import DesktopStateRepository
from manual_tracking.risk_reviews import ManualPositionRiskReviewRepository
from trading.scanner.schemas import ScannerScanRequest
from trading.scanner.service import MarketScannerService
from trading.schemas import AnalysisMode


class _BackgroundPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._background_tasks: set[BackgroundTask] = set()

    def run_background(
        self,
        function: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_failed: Callable[[str], None],
    ) -> None:
        task = BackgroundTask(function)
        self._background_tasks.add(task)
        task.signals.succeeded.connect(on_success)
        task.signals.failed.connect(on_failed)
        task.signals.finished.connect(
            lambda current=task: self._background_tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)


class MarketScanPage(_BackgroundPage):
    def __init__(self, *, exports_dir: Path) -> None:
        super().__init__()
        self.exports_dir = exports_dir
        self.cancel_requested = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("全市场本地批量扫描；扫描阶段网络请求0、模型调用0"))
        form = QFormLayout()
        self.query = QLineEdit("成交额超过5亿元，排除ST，返回前20只")
        self.top_n = QSpinBox()
        self.top_n.setRange(1, 100)
        self.top_n.setValue(20)
        form.addRow("自然语言条件", self.query)
        form.addRow("候选数量", self.top_n)
        layout.addLayout(form)
        controls = QHBoxLayout()
        run = QPushButton("开始扫描")
        run.clicked.connect(self.run_scan)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.cancel)
        export = QPushButton("导出JSON")
        export.clicked.connect(self.export)
        controls.addWidget(run)
        controls.addWidget(cancel)
        controls.addWidget(export)
        layout.addLayout(controls)
        self.status = QLabel("READY")
        self.summary = QLabel("尚未执行扫描。")
        self.summary.setWordWrap(True)
        self.candidates = QTableWidget(0, 8)
        self.candidates.setHorizontalHeaderLabels(
            (
                "排名",
                "代码",
                "名称",
                "现价",
                "涨跌",
                "扫描分",
                "因子覆盖",
                "风险/新鲜度",
            )
        )
        self.candidates.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.candidates.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.raw_toggle = QPushButton("开发者详情（JSON）")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.toggled.connect(self._toggle_raw)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setVisible(False)
        layout.addWidget(self.status)
        layout.addWidget(self.summary)
        layout.addWidget(self.candidates, 1)
        layout.addWidget(self.raw_toggle)
        layout.addWidget(self.output, 1)

    def run_scan(self) -> None:
        self.cancel_requested = False
        self.status.setText("RUNNING")
        request = ScannerScanRequest(
            query=self.query.text(),
            analysis_mode=AnalysisMode.SCREENING,
            data_cutoff=datetime.now().astimezone(),
            top_n=self.top_n.value(),
            allow_parser_model=False,
            persist_run=True,
        )
        self.run_background(
            lambda: MarketScannerService().scan(request),
            self._scan_finished,
            self._failed,
        )

    def cancel(self) -> None:
        self.cancel_requested = True
        self.status.setText("CANCEL_REQUESTED")

    def _scan_finished(self, result: Any) -> None:
        if self.cancel_requested:
            self.status.setText("CANCELLED")
            return
        if not hasattr(result, "scanned_count"):
            questions = getattr(result, "clarification_questions", [])
            self.status.setText("WAITING_CLARIFICATION")
            self.summary.setText(
                "；".join(questions) if questions else "筛选条件需要补充说明。"
            )
            return
        if result.scanned_count <= 0:
            self.status.setText("SCANNER_EMPTY_UNIVERSE")
            self.summary.setText(
                "未读取到有效股票池。扫描没有成功，请检查数据库和市场快照。"
            )
            self.candidates.setRowCount(0)
            return
        payload = result.model_dump(mode="json")
        payload.update(
            {
                "network_request_count": 0,
                "model_call_count": 0,
                "decision_called": False,
                "is_trade_recommendation": False,
            }
        )
        self.output.setPlainText(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )
        self.status.setText(
            "COMPLETED" if result.returned_count else "COMPLETED_NO_MATCHES"
        )
        snapshot = (
            result.snapshot_time.isoformat()
            if result.snapshot_time is not None
            else "缺失"
        )
        self.summary.setText(
            "；".join(
                (
                    f"股票池 {result.universe_count} 只",
                    f"已扫描 {result.scanned_count} 只",
                    f"匹配 {result.matched_count} 只",
                    f"返回 {result.returned_count} 只",
                    f"快照时间 {snapshot}",
                    f"陈旧状态 {'是' if result.stale else '否'}",
                    "网络请求 0",
                    "模型调用 0",
                    "结果不构成买卖建议",
                    (
                        f"无匹配原因 {result.no_match_reason}"
                        if result.no_match_reason
                        else "已返回真实候选"
                    ),
                )
            )
        )
        self.candidates.setRowCount(len(result.candidates))
        for row, candidate in enumerate(result.candidates):
            risk = ",".join(
                item.value if hasattr(item, "value") else str(item)
                for item in candidate.risk_flags
            )
            values = (
                str(candidate.rank),
                candidate.symbol,
                candidate.short_name or "—",
                (
                    "—"
                    if candidate.current_price is None
                    else f"{candidate.current_price:.2f}"
                ),
                (
                    "—"
                    if candidate.change_pct is None
                    else f"{candidate.change_pct:.2%}"
                ),
                f"{candidate.scanner_score:.3f}",
                candidate.factor_coverage,
                f"{candidate.data_freshness} {risk}".strip(),
            )
            for column, value in enumerate(values):
                self.candidates.setItem(
                    row, column, QTableWidgetItem(value)
                )
        self.candidates.resizeColumnsToContents()

    def _failed(self, error_code: str) -> None:
        self.status.setText(error_code)
        self.summary.setText(
            "扫描未完成。请检查数据库、快照和系统状态后重试。"
        )

    def _toggle_raw(self, visible: bool) -> None:
        self.output.setVisible(visible)
        self.raw_toggle.setText(
            "收起开发者详情" if visible else "开发者详情（JSON）"
        )

    def export(self) -> None:
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出扫描结果",
            str(self.exports_dir / "scanner-result.json"),
            "JSON (*.json)",
        )
        if selected:
            Path(selected).write_text(self.output.toPlainText(), encoding="utf-8")


class CandidateDetailPage(_BackgroundPage):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("扫描候选详情；不是买卖建议"))
        bar = QHBoxLayout()
        self.symbol = QLineEdit("300750.SZ")
        load = QPushButton("读取本地候选")
        load.clicked.connect(self.load)
        bar.addWidget(self.symbol)
        bar.addWidget(load)
        layout.addLayout(bar)
        self.status = QLabel("READY")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setVisible(False)
        raw_toggle = QPushButton("开发者详情（JSON）")
        raw_toggle.setCheckable(True)
        raw_toggle.toggled.connect(self.raw_output.setVisible)
        layout.addWidget(self.status)
        layout.addWidget(self.output)
        layout.addWidget(raw_toggle)
        layout.addWidget(self.raw_output)

    def load(self) -> None:
        symbol = self.symbol.text().strip().upper()
        self.status.setText("RUNNING")
        self.run_background(
            lambda: MarketScannerService().symbol(symbol),
            lambda value: self._finished(symbol, value),
            lambda code: (
                self.status.setText(code),
                self.output.setPlainText("候选读取失败；未生成交易指令。"),
            ),
        )

    def _finished(self, symbol: str, value: dict[str, Any] | None) -> None:
        if value is None:
            self.status.setText("NOT_FOUND")
            self.output.setPlainText(
                f"{symbol} 暂无已保存扫描候选。\n结果不是买卖建议。"
            )
        else:
            self.status.setText("COMPLETED")
            lines = [
                f"股票代码：{symbol}",
                f"名称：{value.get('short_name') or '缺失'}",
                f"排名：{value.get('rank') or '缺失'}",
                f"扫描分：{value.get('scanner_score') or '缺失'}",
                f"因子覆盖：{value.get('factor_coverage') or '缺失'}",
                f"新鲜度：{value.get('data_freshness') or '缺失'}",
                "结果不是买卖建议。",
            ]
            self.output.setPlainText("\n".join(lines))
        self.raw_output.setPlainText(
            json.dumps(
                {
                    "symbol": symbol,
                    "candidate": value,
                    "is_trade_recommendation": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


class TaskSkillPage(_BackgroundPage):
    def __init__(
        self,
        *,
        title: str,
        prompt_prefix: str,
        service: TaskCenterService,
        repository: DesktopStateRepository,
        placeholder: str,
    ) -> None:
        super().__init__()
        self.service = service
        self.repository = repository
        self.prompt_prefix = prompt_prefix
        self.conversation_id: str | None = None
        self.current_task_id: str | None = None
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title))
        self.input = QLineEdit()
        self.input.setPlaceholderText(placeholder)
        layout.addWidget(self.input)
        controls = QHBoxLayout()
        run = QPushButton("执行")
        run.clicked.connect(self.run_task)
        retry = QPushButton("重试")
        retry.clicked.connect(self.retry)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.cancel)
        controls.addWidget(run)
        controls.addWidget(retry)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.status = QLabel("READY")
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setVisible(False)
        raw_toggle = QPushButton("开发者详情（JSON）")
        raw_toggle.setCheckable(True)
        raw_toggle.toggled.connect(self.raw_output.setVisible)
        layout.addWidget(self.status)
        layout.addWidget(self.output, 1)
        layout.addWidget(raw_toggle)
        layout.addWidget(self.raw_output, 1)

    def _conversation(self) -> str:
        if self.conversation_id is None:
            self.conversation_id = self.service.new_conversation(
                self.prompt_prefix
            ).conversation_id
        return self.conversation_id

    def run_task(self) -> None:
        value = self.input.text().strip()
        if not value:
            return
        self.status.setText("RUNNING")
        message = f"{self.prompt_prefix}{value}"
        self.run_background(
            lambda: self.service.submit_message(self._conversation(), message),
            self._finished,
            self._failed,
        )

    def _finished(self, task: Any) -> None:
        self.current_task_id = task.task_id
        self.status.setText(task.status.value)
        if task.result_card is None:
            self.output.setPlainText(
                f"任务未完成。\n错误代码：{task.error_code or 'TASK_FAILED'}"
            )
            payload = {"error_code": task.error_code}
        else:
            card = task.result_card
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
                    display = "缺失" if value is None else str(value)
                elif isinstance(value, list):
                    display = f"{len(value)}项"
                elif isinstance(value, dict):
                    display = f"{len(value)}个字段"
                else:
                    display = type(value).__name__
                lines.append(f"{key}：{display}")
            self.output.setPlainText("\n".join(lines))
            payload = card.model_dump(mode="json")
        self.raw_output.setPlainText(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )

    def _failed(self, error_code: str) -> None:
        self.status.setText(error_code)

    def cancel(self) -> None:
        if self.current_task_id:
            task = self.service.cancel(self.current_task_id)
            self.status.setText(task.status.value)

    def retry(self) -> None:
        if self.current_task_id:
            self.run_background(
                lambda: self.service.retry(self.current_task_id),
                self._finished,
                self._failed,
            )


class DecisionSupportPage(TaskSkillPage):
    def __init__(
        self,
        service: TaskCenterService,
        repository: DesktopStateRepository,
    ) -> None:
        super().__init__(
            title="正式决策支持：单只股票，必须二次确认",
            prompt_prefix="正式决策 ",
            service=service,
            repository=repository,
            placeholder="输入股票代码，例如300750.SZ",
        )
        self.confirm = QPushButton("精确确认当前待决策股票")
        self.confirm.clicked.connect(self.confirm_pending)
        self.layout().insertWidget(3, self.confirm)

    def confirm_pending(self) -> None:
        if self.conversation_id is None:
            return
        pending = self.repository.context(
            self.conversation_id
        ).pending_confirmation
        if not pending:
            return
        self.status.setText("RUNNING")
        self.run_background(
            lambda: self.service.submit_message(
                self.conversation_id or "",
                pending,
            ),
            self._finished,
            self._failed,
        )


class ManualPositionsPage(_BackgroundPage):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("人工持仓：由人工成交账本只读投影，不代表券商真实持仓")
        )
        refresh = QPushButton("刷新人工持仓")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.status = QLabel("READY")
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)

    def refresh(self) -> None:
        self.status.setText("RUNNING")
        self.run_background(
            lambda: ManualTradeDesktopController().positions(),
            self._positions_finished,
            lambda code: (
                self.status.setText(code),
                self.output.setPlainText("人工持仓读取失败。"),
            ),
        )

    def _positions_finished(self, positions: list[Any]) -> None:
        self.status.setText("COMPLETED")
        lines = [
            "来源：MANUAL_LEDGER_ONLY",
            f"持仓数量：{len(positions)}",
            "包含Paper账户：否",
            "包含券商持仓：否",
        ]
        for item in positions:
            lines.append(
                f"{getattr(item, 'symbol', '未知')} "
                f"{getattr(item, 'quantity', 0)}股 "
                f"成本{getattr(item, 'average_cost', '缺失')}"
            )
        self.output.setPlainText("\n".join(lines))


class PositionRiskPage(_BackgroundPage):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("持仓风险复核：建议性输出，不修改持仓或生成订单"))
        bar = QHBoxLayout()
        self.position_id = QLineEdit()
        self.position_id.setPlaceholderText("输入人工持仓position_id")
        load = QPushButton("读取风险复核记录")
        load.clicked.connect(self.load)
        bar.addWidget(self.position_id)
        bar.addWidget(load)
        layout.addLayout(bar)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.status = QLabel("READY")
        layout.addWidget(self.status)
        layout.addWidget(self.output)

    def load(self) -> None:
        position_id = self.position_id.text().strip()
        if not position_id:
            return
        self.status.setText("RUNNING")
        self.run_background(
            lambda: ManualPositionRiskReviewRepository().list_for_position(
                position_id
            ),
            lambda reviews: self._reviews_finished(position_id, reviews),
            lambda code: (
                self.status.setText(code),
                self.output.setPlainText("风险复核记录读取失败。"),
            ),
        )

    def _reviews_finished(self, position_id: str, reviews: list[Any]) -> None:
        self.status.setText("COMPLETED")
        lines = [
            f"人工持仓ID：{position_id}",
            f"复核记录：{len(reviews)}",
            "仅提供建议：是",
            "修改持仓：否",
            "生成订单：否",
        ]
        if reviews:
            latest = reviews[-1]
            lines.append(
                f"最近风险等级：{getattr(latest, 'risk_level', '缺失')}"
            )
            lines.append(
                f"最近建议：{getattr(latest, 'recommended_action', '缺失')}"
            )
        self.output.setPlainText("\n".join(lines))


class ExperimentReportPage(QWidget):
    def __init__(self, reports_dir: Path) -> None:
        super().__init__()
        self.reports_dir = reports_dir
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("实验与评价报告；当前结果不能证明稳定盈利")
        )
        self.files = QListWidget()
        self.files.currentTextChanged.connect(self.open_report)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.files)
        layout.addWidget(self.output, 1)
        refresh = QPushButton("刷新报告列表")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        self.refresh()

    def refresh(self) -> None:
        self.files.clear()
        root = self.reports_dir / "experiments"
        if not root.is_dir():
            return
        for path in sorted(root.glob("*")):
            if path.suffix.casefold() in {".json", ".md"}:
                self.files.addItem(path.name)

    def open_report(self, name: str) -> None:
        if not name:
            return
        path = self.reports_dir / "experiments" / Path(name).name
        try:
            self.output.setPlainText(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except OSError:
            self.output.setPlainText("READ_FAILED")


__all__ = [
    "CandidateDetailPage",
    "DecisionSupportPage",
    "ExperimentReportPage",
    "ManualPositionsPage",
    "MarketScanPage",
    "PositionRiskPage",
    "TaskSkillPage",
]
