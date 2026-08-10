from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from desktop.workspace.backtests import (
    BacktestWorkspaceConfig,
    BacktestWorkspaceService,
)
from desktop.task_runner import BackgroundTask
from desktop.workspace.manual_trading import ManualTradeDesktopController
from desktop.workspace.reviews import DesktopReviewService
from desktop.workspace.scheduler import (
    Frequency,
    LocalScheduler,
    ScheduleRepository,
    ScheduleType,
)
from desktop.workspace.watchlists import WatchlistRepository
from trading.simulation.service import SimulationService
from trading.research.orchestration.schemas import (
    ResearchRequest,
    ScreeningRequest,
)
from trading.research.orchestration.service import OrchestrationService
from trading.schemas import OrderIntent, RiskLimits


class WatchlistPage(QWidget):
    def __init__(self, repository: WatchlistRepository, *, exports_dir: Path) -> None:
        super().__init__()
        self.repository = repository
        self.exports_dir = exports_dir
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("自选股与观察池（不是持仓）"))
        bar = QHBoxLayout()
        self.watchlists = QComboBox()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("新观察池名称")
        create = QPushButton("新建")
        create.clicked.connect(self.create_watchlist)
        rename = QPushButton("重命名")
        rename.clicked.connect(self.rename_watchlist)
        delete = QPushButton("删除观察池")
        delete.clicked.connect(self.delete_watchlist)
        bar.addWidget(self.watchlists)
        bar.addWidget(self.new_name)
        bar.addWidget(create)
        bar.addWidget(rename)
        bar.addWidget(delete)
        layout.addLayout(bar)
        form = QFormLayout()
        self.symbol = QLineEdit()
        self.tags = QLineEdit()
        self.note = QLineEdit()
        form.addRow("股票代码", self.symbol)
        form.addRow("标签（逗号分隔）", self.tags)
        form.addRow("备注", self.note)
        layout.addLayout(form)
        actions = QHBoxLayout()
        add = QPushButton("添加股票")
        add.clicked.connect(self.add_symbol)
        update = QPushButton("更新标签备注")
        update.clicked.connect(self.update_symbol)
        remove = QPushButton("删除股票")
        remove.clicked.connect(self.remove_symbol)
        batch_scan = QPushButton("批量扫描")
        batch_scan.clicked.connect(lambda: self._show_batch("SCAN"))
        batch_research = QPushButton("批量RESEARCH（最多30只）")
        batch_research.clicked.connect(lambda: self._show_batch("RESEARCH"))
        export = QPushButton("导出")
        export.clicked.connect(self.export)
        for button in (add, update, remove, batch_scan, batch_research, export):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.items = QListWidget()
        self.items.currentItemChanged.connect(self.selected_symbol)
        layout.addWidget(self.items, 1)
        self.status = QLabel()
        self.batch_result = QLabel()
        self.batch_result.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addWidget(self.batch_result)
        self.watchlists.currentIndexChanged.connect(self.refresh_items)
        self.refresh_watchlists()

    def refresh_watchlists(self) -> None:
        self.watchlists.clear()
        for item in self.repository.list_watchlists():
            self.watchlists.addItem(item.name, item.watchlist_id)
        if not self.watchlists.count():
            created = self.repository.create("默认观察池")
            self.watchlists.addItem(created.name, created.watchlist_id)
        self.refresh_items()

    def create_watchlist(self) -> None:
        if self.new_name.text().strip():
            self.repository.create(self.new_name.text())
            self.new_name.clear()
            self.refresh_watchlists()

    def rename_watchlist(self) -> None:
        watchlist_id = self.watchlists.currentData()
        if watchlist_id and self.new_name.text().strip():
            self.repository.rename(str(watchlist_id), self.new_name.text())
            self.new_name.clear()
            self.refresh_watchlists()

    def delete_watchlist(self) -> None:
        watchlist_id = self.watchlists.currentData()
        if watchlist_id:
            self.repository.delete(str(watchlist_id))
            self.refresh_watchlists()

    def add_symbol(self) -> None:
        watchlist_id = self.watchlists.currentData()
        if watchlist_id and self.symbol.text().strip():
            self.repository.add_symbol(
                str(watchlist_id),
                self.symbol.text(),
                tags=[item.strip() for item in self.tags.text().split(",")],
                note=self.note.text(),
            )
            self.symbol.clear()
            self.refresh_items()

    def remove_symbol(self) -> None:
        current = self.items.currentItem()
        watchlist_id = self.watchlists.currentData()
        if current and watchlist_id:
            self.repository.remove_symbol(
                str(watchlist_id), str(current.data(Qt.UserRole))
            )
            self.refresh_items()

    def update_symbol(self) -> None:
        current = self.items.currentItem()
        watchlist_id = self.watchlists.currentData()
        if current and watchlist_id:
            self.repository.update_symbol(
                str(watchlist_id),
                str(current.data(Qt.UserRole)),
                tags=[item.strip() for item in self.tags.text().split(",")],
                note=self.note.text(),
            )
            self.refresh_items()

    def selected_symbol(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        del previous
        watchlist_id = self.watchlists.currentData()
        if not current or not watchlist_id:
            return
        symbol = str(current.data(Qt.UserRole))
        item = next(
            candidate
            for candidate in self.repository.items(str(watchlist_id))
            if candidate.symbol == symbol
        )
        self.symbol.setText(item.symbol)
        self.tags.setText(",".join(item.tags))
        self.note.setText(item.note)

    def refresh_items(self) -> None:
        self.items.clear()
        watchlist_id = self.watchlists.currentData()
        if not watchlist_id:
            return
        for item in self.repository.items(str(watchlist_id)):
            row = QListWidgetItem(
                f"{item.symbol} | {','.join(item.tags)} | "
                f"{item.freshness} | {','.join(item.risk_flags)}"
            )
            row.setData(Qt.UserRole, item.symbol)
            self.items.addItem(row)

    def _show_batch(self, mode: str) -> None:
        watchlist_id = self.watchlists.currentData()
        if not watchlist_id:
            return
        symbols = (
            self.repository.research_symbols(str(watchlist_id))
            if mode == "RESEARCH"
            else self.repository.scan_symbols(str(watchlist_id))
        )
        if not symbols:
            self.status.setText("EMPTY_WATCHLIST")
            self.batch_result.setText("观察池没有可执行的股票。")
            return
        self.status.setText(f"{mode}_RUNNING")
        service = OrchestrationService()
        if mode == "RESEARCH":
            selected = symbols[:30]
            task = BackgroundTask(
                lambda: service.research(
                    ResearchRequest(
                        symbols=selected,
                        data_cutoff=datetime.now().astimezone(),
                        persist=False,
                        allow_external_fetch=False,
                    )
                )
            )
        else:
            selected = symbols[:30]
            task = BackgroundTask(
                lambda: service.screen(
                    ScreeningRequest(
                        symbols=selected,
                        data_cutoff=datetime.now().astimezone(),
                        limit=max(10, min(30, len(selected))),
                    )
                )
            )
        if not hasattr(self, "_batch_tasks"):
            self._batch_tasks: set[BackgroundTask] = set()
        self._batch_tasks.add(task)
        task.signals.succeeded.connect(
            lambda result, operation=mode: self._batch_finished(
                operation, result
            )
        )
        task.signals.failed.connect(
            lambda code: (
                self.status.setText(code),
                self.batch_result.setText(
                    "批量任务失败；未进入正式决策，未生成订单。"
                ),
            )
        )
        task.signals.finished.connect(
            lambda current=task: self._batch_tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)

    def _batch_finished(self, mode: str, result: object) -> None:
        collection_name = "results" if mode == "RESEARCH" else "candidates"
        results = list(getattr(result, collection_name, []))
        self.status.setText(f"{mode}_COMPLETED")
        self.batch_result.setText(
            "；".join(
                (
                    f"实际处理 {len(results)} 只",
                    f"网络请求 {getattr(result, 'network_request_count', 0)}",
                    f"模型调用 {getattr(result, 'model_call_count', 0)}",
                    "未进入正式决策",
                    "未生成订单",
                )
            )
        )

    def export(self) -> None:
        watchlist_id = self.watchlists.currentData()
        if not watchlist_id:
            return
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出观察池",
            str(self.exports_dir / "watchlist.csv"),
            "CSV (*.csv)",
        )
        if selected:
            self.repository.export_csv(str(watchlist_id), Path(selected))


class ManualTradingPage(QWidget):
    def __init__(
        self, controller: ManualTradeDesktopController | None = None
    ) -> None:
        super().__init__()
        self.controller = controller
        self.preview_id: str | None = None
        layout = QVBoxLayout(self)
        warning = QLabel("仅记录人工成交，不会向券商发送订单")
        warning.setStyleSheet("color:#b45309;font-weight:700")
        layout.addWidget(warning)
        form = QFormLayout()
        self.symbol = QLineEdit()
        self.direction = QComboBox()
        self.direction.addItems(["BUY", "SELL"])
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 100_000_000)
        self.price = QDoubleSpinBox()
        self.price.setRange(0.001, 1_000_000)
        self.price.setDecimals(4)
        self.fee = QDoubleSpinBox()
        self.fee.setRange(0, 1_000_000)
        self.account = QLineEdit("manual-default")
        self.note = QLineEdit()
        self.decision_id = QLineEdit()
        for label, widget in (
            ("symbol", self.symbol),
            ("direction", self.direction),
            ("quantity", self.quantity),
            ("price", self.price),
            ("fee", self.fee),
            ("account", self.account),
            ("note", self.note),
            ("source_decision_id", self.decision_id),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        preview = QPushButton("第一阶段：生成预览")
        preview.clicked.connect(self.create_preview)
        layout.addWidget(preview)
        self.status = QLabel("READY")
        layout.addWidget(self.status)
        self.impact = QPlainTextEdit()
        self.impact.setReadOnly(True)
        layout.addWidget(self.impact)
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setVisible(False)
        raw_toggle = QPushButton("开发者详情（JSON）")
        raw_toggle.setCheckable(True)
        raw_toggle.toggled.connect(self.raw_output.setVisible)
        layout.addWidget(raw_toggle)
        layout.addWidget(self.raw_output)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText("输入精确二阶段确认文本")
        confirm = QPushButton("第二阶段：确认写入人工账本")
        confirm.clicked.connect(self.confirm_preview)
        layout.addWidget(self.confirmation)
        layout.addWidget(confirm)
        ledger = QPushButton("查看人工持仓与审计链")
        ledger.clicked.connect(self.refresh_ledger)
        layout.addWidget(ledger)

    def create_preview(self) -> None:
        if self.controller is None:
            self.controller = ManualTradeDesktopController()
        try:
            view = self.controller.preview(
                symbol=self.symbol.text(),
                direction=self.direction.currentText(),
                quantity=self.quantity.value(),
                price=self.price.value(),
                trade_time=datetime.now().astimezone(),
                fee=self.fee.value(),
                account=self.account.text(),
                note=self.note.text(),
                source_decision_id=self.decision_id.text() or None,
                client_trade_id="desktop-" + uuid4().hex[:16],
            )
        except (OSError, ValueError):
            self.status.setText("MANUAL_TRADE_PREVIEW_FAILED")
            self.impact.setPlainText("预览失败；人工账本未写入。")
            return
        self.preview_id = view.preview.confirmation_id
        self.status.setText("WAITING_CONFIRMATION")
        self.impact.setPlainText(
            "\n".join(
                (
                    f"股票：{view.impact.symbol}",
                    f"持仓变化：{view.impact.position_quantity_delta}股",
                    f"现金变化：{view.impact.cash_delta}",
                    view.impact.statement,
                    f"精确确认文本：{view.required_confirmation}",
                    "尚未写入人工账本。",
                    "不会向券商发送订单。",
                )
            )
        )
        self.raw_output.setPlainText(
            json.dumps(
                {
                    "symbol": view.impact.symbol,
                    "position_quantity_delta": view.impact.position_quantity_delta,
                    "cash_delta": view.impact.cash_delta,
                    "statement": view.impact.statement,
                    "required_confirmation": view.required_confirmation,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    def confirm_preview(self) -> None:
        if self.preview_id and self.controller is not None:
            try:
                result = self.controller.confirm(
                    self.preview_id, self.confirmation.text()
                )
            except (OSError, ValueError):
                self.status.setText("MANUAL_TRADE_CONFIRMATION_FAILED")
                self.impact.appendPlainText(
                    "确认失败；请核对精确确认文本，账本未写入。"
                )
                return
            self.status.setText("RECORDED")
            self.confirmation.clear()
            self.impact.appendPlainText(
                f"已记录人工成交：{result.trade.trade_id}"
            )

    def refresh_ledger(self) -> None:
        if self.controller is None:
            self.controller = ManualTradeDesktopController()
        trades = self.controller.trades()
        positions = self.controller.positions()
        self.status.setText("COMPLETED")
        lines = [
            "来源：MANUAL_LEDGER_ONLY",
            f"人工成交记录：{len(trades)}",
            f"人工持仓：{len(positions)}",
            "历史不可变：是",
            "包含Paper账户：否",
            "包含券商持仓：否",
        ]
        for item in positions:
            lines.append(
                f"{item.symbol} {item.quantity}股 成本{item.average_cost}"
            )
        self.impact.setPlainText("\n".join(lines))
        self.raw_output.setPlainText(
            json.dumps(
                {
                    "source": "MANUAL_LEDGER_ONLY",
                    "trades": [
                        item.model_dump(mode="json")
                        for item in trades
                    ],
                    "positions": [
                        item.model_dump(mode="json")
                        for item in positions
                    ],
                    "immutable_history": True,
                    "paper_account_included": False,
                    "broker_position_included": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )


class PaperTradingPage(QWidget):
    def __init__(self, service: SimulationService | None = None) -> None:
        super().__init__()
        self.service = service
        layout = QVBoxLayout(self)
        banner = QLabel("模拟账户，不代表真实资金和券商成交")
        banner.setStyleSheet("background:#ede9fe;color:#5b21b6;padding:12px;font-weight:700")
        layout.addWidget(banner)
        form = QFormLayout()
        self.symbol = QLineEdit("600000.SH")
        self.side = QComboBox()
        self.side.addItems(["BUY", "SELL"])
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 100_000_000)
        self.quantity.setValue(100)
        self.price = QDoubleSpinBox()
        self.price.setRange(0.001, 1_000_000)
        self.price.setDecimals(4)
        self.price.setValue(10)
        self.confirmation = QLineEdit()
        self.confirmation.setPlaceholderText("输入 CONFIRM_PAPER_ORDER")
        form.addRow("股票代码", self.symbol)
        form.addRow("方向", self.side)
        form.addRow("数量", self.quantity)
        form.addRow("模拟成交价", self.price)
        form.addRow("明确确认", self.confirmation)
        layout.addLayout(form)
        submit = QPushButton("提交Paper模拟订单")
        submit.clicked.connect(self.submit_order)
        layout.addWidget(submit)
        self.kill_switch = QCheckBox("启用Paper买入停止开关")
        apply_kill_switch = QPushButton("应用Paper安全开关")
        apply_kill_switch.clicked.connect(self.apply_kill_switch)
        layout.addWidget(self.kill_switch)
        layout.addWidget(apply_kill_switch)
        self.status = QLabel("READY")
        layout.addWidget(self.status)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setVisible(False)
        raw_toggle = QPushButton("开发者详情（JSON）")
        raw_toggle.setCheckable(True)
        raw_toggle.toggled.connect(self.raw_output.setVisible)
        layout.addWidget(raw_toggle)
        layout.addWidget(self.raw_output)
        refresh = QPushButton("刷新模拟账户")
        refresh.clicked.connect(self.refresh)
        layout.addWidget(refresh)
        export = QPushButton("导出模拟账户 JSON")
        export.clicked.connect(self.export)
        layout.addWidget(export)

    def refresh(self) -> None:
        if self.service is None:
            self.service = SimulationService()
        account = self.service.paper_account()
        orders = self.service.paper_orders()
        self.kill_switch.setChecked(account.kill_switch)
        lines = [
            "账户类型：PAPER_ONLY",
            f"现金：{account.cash:.2f}",
            f"权益：{account.equity:.2f}",
            f"持仓数量：{len(account.positions)}",
            f"模拟订单数量：{len(orders)}",
            f"买入停止开关：{'已启用' if account.kill_switch else '未启用'}",
            "真实订单能力：无",
        ]
        if orders:
            latest = orders[-1]
            lines.append(
                "最近模拟订单："
                f"{latest.symbol} {latest.side.value} "
                f"{latest.quantity}股 {latest.status.value}"
            )
            if latest.reason:
                lines.append(f"最近订单说明：{latest.reason}")
        self.output.setPlainText("\n".join(lines))
        self.raw_output.setPlainText(
            json.dumps(
                {
                    "label": "PAPER_ONLY",
                    "cash": account.cash,
                    "equity": account.equity,
                    "positions": {
                        key: value.model_dump(mode="json")
                        for key, value in account.positions.items()
                    },
                    "orders": [
                        order.model_dump(mode="json") for order in orders
                    ],
                    "kill_switch": account.kill_switch,
                    "live_order_supported": False,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        self.status.setText("READY")

    def submit_order(self) -> None:
        symbol = self.symbol.text().strip().upper()
        if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", symbol) is None:
            self.status.setText("INVALID_SYMBOL")
            return
        if self.confirmation.text().strip() != "CONFIRM_PAPER_ORDER":
            self.status.setText("PAPER_CONFIRMATION_REQUIRED")
            return
        if self.service is None:
            self.service = SimulationService()
        try:
            result = self.service.submit_paper_order(
                OrderIntent(
                    symbol=symbol,
                    side=self.side.currentText().lower(),
                    quantity=self.quantity.value(),
                    reference_price=self.price.value(),
                    price_time=datetime.now().astimezone(),
                    approved_by="desktop-explicit-confirmation",
                ),
                RiskLimits(),
            )
        except (OSError, ValueError):
            self.status.setText("PAPER_ORDER_FAILED")
            return
        self.confirmation.clear()
        self.refresh()
        self.status.setText(f"PAPER_{result.status.value.upper()}")

    def apply_kill_switch(self) -> None:
        if self.service is None:
            self.service = SimulationService()
        self.service.set_kill_switch(self.kill_switch.isChecked())
        self.refresh()
        self.status.setText("PAPER_KILL_SWITCH_UPDATED")

    def export(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出模拟账户",
            "paper-account.json",
            "JSON (*.json)",
        )
        if selected:
            Path(selected).write_text(
                self.raw_output.toPlainText(),
                encoding="utf-8",
            )


class BacktestPage(QWidget):
    def __init__(self, service: BacktestWorkspaceService | None = None) -> None:
        super().__init__()
        self.service = service or BacktestWorkspaceService()
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "research_only=true · profitability_proven=false · "
                "RAW价格/公司行动/幸存者偏差风险"
            )
        )
        form = QFormLayout()
        self.experiment = QLineEdit(self.service.latest_experiment_id() or "")
        self.symbols = QLineEdit("由既有实验定义决定")
        self.symbols.setReadOnly(True)
        self.start = QDateEdit()
        self.start.setDate(date.today() - timedelta(days=365))
        self.end = QDateEdit()
        self.end.setDate(date.today())
        self.top_k = QSpinBox()
        self.top_k.setRange(1, 100)
        self.top_k.setValue(20)
        self.holding = QSpinBox()
        self.holding.setRange(1, 250)
        self.holding.setValue(5)
        self.weighting = QComboBox()
        self.weighting.addItems(["EQUAL", "SCORE"])
        self.benchmark = QLineEdit("000300.SH")
        for label, widget in (
            ("实验", self.experiment),
            ("股票池", self.symbols),
            ("开始日期", self.start),
            ("结束日期", self.end),
            ("top_k", self.top_k),
            ("持有期", self.holding),
            ("权重", self.weighting),
            ("基准", self.benchmark),
        ):
            form.addRow(label, widget)
        layout.addLayout(form)
        controls = QHBoxLayout()
        run = QPushButton("执行只读回测（不落库）")
        run.clicked.connect(self.run_backtest)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.service.cancel)
        controls.addWidget(run)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setVisible(False)
        self.raw_toggle = QPushButton("开发者详情（JSON）")
        self.raw_toggle.setCheckable(True)
        self.raw_toggle.toggled.connect(self.raw_output.setVisible)
        layout.addWidget(self.raw_toggle)
        layout.addWidget(self.raw_output)
        export = QPushButton("导出回测摘要 JSON")
        export.clicked.connect(self.export)
        layout.addWidget(export)

    def _config(self) -> BacktestWorkspaceConfig:
        return BacktestWorkspaceConfig(
            experiment_id=self.experiment.text(),
            symbols=["EXPERIMENT_UNIVERSE"],
            start_date=self.start.date().toPython(),
            end_date=self.end.date().toPython(),
            top_k=self.top_k.value(),
            holding_period=self.holding.value(),
            weighting_method=self.weighting.currentText(),
            benchmark=self.benchmark.text(),
        )

    def run_backtest(self) -> None:
        self.output.setPlainText("RUNNING：正在读取既有实验数据并执行只读回测。")
        task = BackgroundTask(lambda: self.service.run_existing(self._config()))
        if not hasattr(self, "_tasks"):
            self._tasks: set[BackgroundTask] = set()
        self._tasks.add(task)
        task.signals.succeeded.connect(self._finished)
        task.signals.failed.connect(
            lambda code: self.output.setPlainText(
                f"回测失败。\n错误代码：{code}\n未写入人工账本或Paper账户。"
            )
        )
        task.signals.finished.connect(
            lambda current=task: self._tasks.discard(current)
        )
        QThreadPool.globalInstance().start(task)

    def run_summary(self) -> None:
        self.run_backtest()

    def _finished(self, result: object) -> None:
        metrics = getattr(result, "metrics", {})
        lines = [
            f"状态：{result.status}",
            f"样本天数：{result.data_quality.get('sample_count', 0)}",
            "价格口径：RAW",
            "研究用途：是",
            "已证明稳定盈利：否",
            "写入人工账本：否",
            "写入Paper账户：否",
        ]
        for key, value in metrics.items():
            display = "缺失" if value is None else str(value)
            lines.append(f"{key}：{display}")
        if result.risk_flags:
            lines.append("风险：" + "；".join(result.risk_flags))
        self.output.setPlainText("\n".join(lines))
        payload = {
            "status": result.status,
            "metrics": metrics,
            "data_quality": result.data_quality,
            "risk_flags": result.risk_flags,
            "research_only": result.research_only,
            "profitability_proven": result.profitability_proven,
            "manual_ledger_written": result.manual_ledger_written,
            "paper_trading_written": result.paper_trading_written,
        }
        self.raw_output.setPlainText(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        )

    def export(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "导出回测摘要",
            "backtest-summary.json",
            "JSON (*.json)",
        )
        if selected:
            Path(selected).write_text(
                self.raw_output.toPlainText(),
                encoding="utf-8",
            )


class ReviewPage(QWidget):
    def __init__(self, service: DesktopReviewService, *, premarket: bool) -> None:
        super().__init__()
        self.service = service
        self.premarket = premarket
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("盘前简报" if premarket else "每日复盘"))
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        layout.addWidget(self.output)
        self.raw_output = QPlainTextEdit()
        self.raw_output.setReadOnly(True)
        self.raw_output.setVisible(False)
        raw_toggle = QPushButton("开发者详情（JSON）")
        raw_toggle.setCheckable(True)
        raw_toggle.toggled.connect(self.raw_output.setVisible)
        layout.addWidget(raw_toggle)
        layout.addWidget(self.raw_output)
        generate = QPushButton("生成只读报告")
        generate.clicked.connect(self.generate)
        layout.addWidget(generate)

    def generate(self) -> None:
        report = (
            self.service.premarket_brief()
            if self.premarket
            else self.service.daily_review(date.today())
        )
        lines = [
            f"报告类型：{report.report_type}",
            f"章节数量：{len(report.sections)}",
            f"风险提示：{len(report.risk_flags)}",
            f"生成订单：{'是' if report.order_created else '否'}",
            (
                "生成交易指令：是"
                if report.trade_instruction_created
                else "生成交易指令：否"
            ),
        ]
        for title, content in report.sections.items():
            lines.append(f"{title}：{content}")
        if report.risk_flags:
            lines.append("风险：" + "；".join(report.risk_flags))
        self.output.setPlainText("\n".join(lines))
        self.raw_output.setPlainText(
            json.dumps(
                {
                    "report_type": report.report_type,
                    "sections": report.sections,
                    "risk_flags": report.risk_flags,
                    "order_created": report.order_created,
                    "trade_instruction_created": report.trade_instruction_created,
                },
                ensure_ascii=False,
                indent=2,
            )
        )


class SchedulerPage(QWidget):
    def __init__(
        self,
        repository: ScheduleRepository,
        scheduler: LocalScheduler,
    ) -> None:
        super().__init__()
        self.repository = repository
        self.scheduler = scheduler
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("本地调度中心（默认全部关闭）"))
        create_form = QFormLayout()
        self.name = QLineEdit()
        self.task_type = QComboBox()
        self.task_type.addItems([item.value for item in ScheduleType])
        self.frequency = QComboBox()
        self.frequency.addItems([item.value for item in Frequency])
        self.next_run = QDateTimeEdit(datetime.now() + timedelta(minutes=5))
        self.next_run.setCalendarPopup(True)
        create_form.addRow("任务名称", self.name)
        create_form.addRow("任务类型", self.task_type)
        create_form.addRow("频率", self.frequency)
        create_form.addRow("首次运行", self.next_run)
        layout.addLayout(create_form)
        save = QPushButton("保存任务（默认关闭）")
        save.clicked.connect(self.save_schedule)
        layout.addWidget(save)
        self.schedules = QListWidget()
        layout.addWidget(self.schedules, 1)
        controls = QHBoxLayout()
        self.enabled = QCheckBox("启用")
        self.enabled.toggled.connect(self.toggle)
        run = QPushButton("立即运行")
        run.clicked.connect(self.run_now)
        retry = QPushButton("重试最近失败")
        retry.clicked.connect(self.retry_failed)
        cancel = QPushButton("取消最近运行")
        cancel.clicked.connect(self.cancel_latest)
        controls.addWidget(self.enabled)
        controls.addWidget(run)
        controls.addWidget(retry)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        layout.addWidget(self.history)
        self.schedules.currentItemChanged.connect(self.selected)
        self.refresh()

    def save_schedule(self) -> None:
        if not self.name.text().strip():
            return
        next_run = self.next_run.dateTime().toPython()
        if next_run.tzinfo is None:
            next_run = next_run.astimezone()
        self.repository.create(
            name=self.name.text(),
            task_type=ScheduleType(self.task_type.currentText()),
            frequency=Frequency(self.frequency.currentText()),
            enabled=False,
            next_run_at=next_run,
        )
        self.name.clear()
        self.refresh()

    def refresh(self) -> None:
        self.schedules.clear()
        for schedule in self.repository.list():
            item = QListWidgetItem(
                f"{'[启用]' if schedule.enabled else '[关闭]'} {schedule.name}"
            )
            item.setData(Qt.UserRole, schedule.schedule_id)
            self.schedules.addItem(item)

    def selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if not current:
            return
        schedule = self.repository.get(str(current.data(Qt.UserRole)))
        self.enabled.blockSignals(True)
        self.enabled.setChecked(schedule.enabled)
        self.enabled.blockSignals(False)
        runs = self.repository.runs(schedule.schedule_id)
        self.history.setPlainText(
            "\n".join(f"{item.status} {item.scheduled_for.isoformat()}" for item in runs)
        )

    def toggle(self, enabled: bool) -> None:
        current = self.schedules.currentItem()
        if current:
            self.repository.set_enabled(
                str(current.data(Qt.UserRole)), enabled
            )
            self.refresh()

    def run_now(self) -> None:
        current = self.schedules.currentItem()
        if current:
            run = self.scheduler.run_now(str(current.data(Qt.UserRole)))
            self.history.setPlainText(f"{run.status} {run.error_code or ''}")

    def retry_failed(self) -> None:
        current = self.schedules.currentItem()
        if not current:
            return
        runs = self.repository.runs(str(current.data(Qt.UserRole)))
        failed = next((item for item in runs if item.status == "FAILED"), None)
        if failed:
            retried = self.scheduler.retry_failed(failed.run_id)
            self.history.setPlainText(
                f"{retried.status} {retried.error_code or ''}"
            )

    def cancel_latest(self) -> None:
        current = self.schedules.currentItem()
        if not current:
            return
        runs = self.repository.runs(str(current.data(Qt.UserRole)))
        if runs:
            cancelled = self.repository.cancel_run(runs[0].run_id)
            self.history.setPlainText(
                f"{cancelled.status} {cancelled.error_code or ''}"
            )
