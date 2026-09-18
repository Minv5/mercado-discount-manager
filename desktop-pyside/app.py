from __future__ import annotations

import os
import sys
import json
from pathlib import Path

if os.name == "nt":
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
    if os.name == "nt":
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(argv if argv is not None else sys.argv)
    install_windows_unhandled_exception_filter()
    app.setApplicationName("美客多活动管家")
    app.setOrganizationName("MercadoDiscountManager")
    if sys.platform == "darwin":
        app.setFont(QFont("PingFang SC", 13.5))
    else:
        app.setFont(QFont("Microsoft YaHei UI", 10))
    down = str(resource_path("assets/chevron-down.xpm")).replace("\\", "/")
    up = str(resource_path("assets/chevron-up.xpm")).replace("\\", "/")
    qss = APP_QSS.replace("@CHEVRON_DOWN@", down).replace("@CHEVRON_UP@", up)
    if sys.platform == "darwin":
        qss = qss.replace('"Microsoft YaHei UI"', '"PingFang SC"')
        qss = qss.replace("font-size: 10pt;", "font-size: 13.5pt;")
        qss = qss.replace("font-size: 11pt;", "font-size: 15pt;")
        qss = qss.replace("font-size: 16pt;", "font-size: 20pt;")
    app.setStyleSheet(qss)
    icon_name = "assets/app.icns" if sys.platform == "darwin" and resource_path("assets/app.icns").exists() else "assets/app.ico"
    icon = resource_path(icon_name)
    if not icon.exists():
        icon = resource_path("assets/app-icon.png")
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
    if "--auto-execute" in sys.argv:
        from PySide6.QtCore import QTimer

        seller_disc = 28.0
        official_disc = 28.0
        for i, arg in enumerate(sys.argv):
            if arg == "--seller-discount" and i + 1 < len(sys.argv):
                try:
                    seller_disc = float(sys.argv[i + 1])
                except ValueError:
                    pass
            elif arg == "--official-discount" and i + 1 < len(sys.argv):
                try:
                    official_disc = float(sys.argv[i + 1])
                except ValueError:
                    pass

        app = create_application()
        project_root = Path(__file__).resolve().parents[1]
        service = NodeServiceManager(project_root)
        window = MainWindow(ApiClient(), service)
        window.show()

        def do_auto_execute() -> None:
            window.log(f"[自动化执行] 切换执行模式为「批量报活动」，设置自建折扣={seller_disc}%，官方折扣={official_disc}%")
            window.mode_combo.setCurrentText("批量报活动")
            window.seller_discount.setValue(seller_disc)
            window.official_discount.setValue(official_disc)
            window.log("[自动化执行] 点击「开始执行」...")
            window.execute_button.click()

        QTimer.singleShot(2500, do_auto_execute)
        exit_code = app.exec()
        diagnostic_event("application_event_loop_returned", exit_code=exit_code)
        return exit_code

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
