from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class TaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    finished = Signal()


class BackgroundTask(QRunnable):
    def __init__(self, function: Callable[[], Any]) -> None:
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.function())
        except Exception as error:
            message = str(error).strip()
            if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", message):
                code = message
            elif isinstance(error, ModuleNotFoundError):
                code = "COMPONENT_UNAVAILABLE"
            elif isinstance(error, OSError):
                code = "IO_ERROR"
            elif isinstance(error, ValueError):
                code = "INVALID_INPUT"
            else:
                code = "TASK_FAILED"
            self.signals.failed.emit(code)
        finally:
            self.signals.finished.emit()
