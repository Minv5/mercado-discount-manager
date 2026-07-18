from __future__ import annotations

import os
import sys
import json
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from api_client import ApiClient
from diagnostics import diagnostic_event, install_runtime_diagnostics, install_windows_unhandled_exception_filter
from main_window import MainWindow, resource_path
from service_manager import NodeServiceManager
from theme import APP_QSS


def create_application(argv: list[str] | None = None) -> QApplication:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv if argv is not None else sys.argv)
    install_windows_unhandled_exception_filter()
    app.setApplicationName("美客多活动管家")
    app.setOrganizationName("MercadoDiscountManager")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    down = str(resource_path("assets/chevron-down.xpm")).replace("\\", "/")
    up = str(resource_path("assets/chevron-up.xpm")).replace("\\", "/")
    app.setStyleSheet(APP_QSS.replace("@CHEVRON_DOWN@", down).replace("@CHEVRON_UP@", up))
    icon = resource_path("assets/app.ico")
    if icon.exists():
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon)))
    return app


def main() -> int:
    install_runtime_diagnostics()
    if "--smoke-service" in sys.argv:
        project_root = Path(__file__).resolve().parents[1]
        service = NodeServiceManager(project_root)
        started = False
        try:
            started = service.ensure_started()
            health = ApiClient().get("/api/health")
            print(json.dumps({"ok": bool(health.get("ok")), "started_by_application": started}, ensure_ascii=False))
            return 0 if health.get("ok") else 1
        finally:
            service.stop()
    if "--keyboard-smoke" in sys.argv:
        from keyboard_smoke import run_keyboard_smoke

        app = create_application(["keyboard-smoke"])
        result = run_keyboard_smoke(app)
        diagnostic_event("keyboard_smoke_result", **result)
        return 0 if result.get("ok") else 1
    app = create_application()
    project_root = Path(__file__).resolve().parents[1]
    service = NodeServiceManager(project_root)
    window = MainWindow(ApiClient(), service)
    app.aboutToQuit.connect(lambda: diagnostic_event("application_about_to_quit", visible=window.isVisible()))
    app.lastWindowClosed.connect(lambda: diagnostic_event("application_last_window_closed"))
    window.show()
    exit_code = app.exec()
    diagnostic_event("application_event_loop_returned", exit_code=exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
