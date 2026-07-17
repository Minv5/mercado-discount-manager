from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object)
    finished = Signal()


class Worker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function(*self.args, **self.kwargs)
        except Exception as error:  # Preserve transport metadata for idempotent recovery.
            if not str(error).strip():
                error.add_note(traceback.format_exc(limit=1).strip())
            self.signals.error.emit(error)
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()
