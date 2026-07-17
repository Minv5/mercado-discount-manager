from __future__ import annotations

import atexit
import ctypes
import faulthandler
import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import qInstallMessageHandler

from log_rotation import rotate_log


_lock = threading.Lock()
_handle = None
_windows_filter_callback = None


def diagnostic_path() -> Path:
    override = os.environ.get("MDM_DIAGNOSTIC_LOG", "").strip()
    if override:
        return Path(override)
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local / "MercadoDiscountManagerStandalone" / "data" / "logs" / "pyside-candidate-runtime.log"


def diagnostic_event(event: str, **details: Any) -> None:
    safe = {key: _safe_value(value) for key, value in details.items()}
    row = {
        "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "event": event,
        **safe,
    }
    try:
        path = diagnostic_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            rotate_log(path)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def install_runtime_diagnostics() -> None:
    global _handle
    path = diagnostic_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_log(path)
    if os.name == "nt":
        install_windows_unhandled_exception_filter()
    else:
        try:
            _handle = path.open("a", encoding="utf-8")
            faulthandler.enable(file=_handle, all_threads=True)
        except OSError:
            _handle = None

    previous_exception = sys.excepthook

    def exception_hook(exc_type, exc_value, exc_traceback):
        diagnostic_event("python_unhandled_exception", exception_type=exc_type.__name__, message=str(exc_value))
        previous_exception(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_hook

    previous_thread = threading.excepthook

    def thread_hook(args):
        diagnostic_event(
            "thread_unhandled_exception",
            thread=args.thread.name if args.thread else "",
            exception_type=args.exc_type.__name__,
            message=str(args.exc_value),
        )
        previous_thread(args)

    threading.excepthook = thread_hook

    def qt_handler(message_type, context, message):
        diagnostic_event(
            "qt_message",
            message_type=int(message_type),
            category=str(getattr(context, "category", "") or ""),
            message=str(message),
        )

    qInstallMessageHandler(qt_handler)
    atexit.register(lambda: diagnostic_event("process_atexit"))
    diagnostic_event("process_started", frozen=bool(getattr(sys, "frozen", False)), argv=_safe_argv(sys.argv))


def install_windows_unhandled_exception_filter() -> None:
    """Log only second-chance Windows exceptions, not recoverable COM first-chance events."""
    global _windows_filter_callback
    if os.name != "nt":
        return

    class ExceptionRecord(ctypes.Structure):
        pass

    exception_record_pointer = ctypes.POINTER(ExceptionRecord)
    ExceptionRecord._fields_ = [
        ("ExceptionCode", ctypes.c_ulong),
        ("ExceptionFlags", ctypes.c_ulong),
        ("ExceptionRecord", exception_record_pointer),
        ("ExceptionAddress", ctypes.c_void_p),
        ("NumberParameters", ctypes.c_ulong),
        ("ExceptionInformation", ctypes.c_size_t * 15),
    ]

    class ExceptionPointers(ctypes.Structure):
        _fields_ = [("ExceptionRecord", exception_record_pointer), ("ContextRecord", ctypes.c_void_p)]

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(ExceptionPointers))

    @callback_type
    def unhandled(exception_pointers):
        code = 0
        try:
            if exception_pointers and exception_pointers.contents.ExceptionRecord:
                code = int(exception_pointers.contents.ExceptionRecord.contents.ExceptionCode)
            diagnostic_event("native_unhandled_exception", exception_code=f"0x{code:08X}")
        except Exception:
            pass
        return 0  # EXCEPTION_CONTINUE_SEARCH

    _windows_filter_callback = unhandled
    ctypes.windll.kernel32.SetUnhandledExceptionFilter(_windows_filter_callback)


def _safe_argv(values: list[str]) -> list[str]:
    allowed = {"--smoke-service", "--keyboard-smoke"}
    return [value for value in values[1:] if value in allowed]


def _safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value[:20]]
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text[:500]
