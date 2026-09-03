from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Qt, Signal, Slot


class GuiDispatcher(QObject):
    """Queue arbitrary callbacks onto the GUI thread.

    ``QRunnable`` result signals may be emitted from a pool thread.  A plain
    Python lambda does not provide a reliable Qt receiver context, so every
    callback that can touch widgets is routed through this QObject with an
    explicit queued connection.
    """

    _invoke = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._invoke.connect(self._run, Qt.ConnectionType.QueuedConnection)

    @Slot(object)
    def _run(self, callback: object) -> None:
        if callable(callback):
            callback()

    def dispatch(self, callback: object) -> None:
        self._invoke.emit(callback)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(object)
    progress = Signal(object)
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

    def report_progress(self, value: object) -> None:
        self.signals.progress.emit(value)
