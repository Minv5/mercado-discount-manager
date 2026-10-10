from __future__ import annotations

import os
import sys
import json
from pathlib import Path

if os.name == "nt":
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QTextEdit,
    QWidget,
)

from api_client import ApiClient
from diagnostics import diagnostic_event, install_runtime_diagnostics, install_windows_unhandled_exception_filter
from main_window import MainWindow, resource_path
from service_manager import NodeServiceManager
from theme import APP_QSS


class FocusVisibleFilter(QObject):
    """全局焦点可见性事件过滤器（实现 W3C focus-visible 语义）：
    彻底消除窗口初次呈现、页面切入或弹窗打开时由 Qt 底层默认赋予焦点产生的高光金边。
    仅当用户产生主动交互时才呈现高光边框：
    - 鼠标主动点击输入/表格控件 (MouseFocusReason / MouseButtonPress)；
    - 键盘 Tab / Shift-Tab / 快捷键光标导航 (TabFocusReason / BacktabFocusReason / ShortcutFocusReason)；
    - 键盘主动打字按键 (KeyPress)；
    失焦或非用户主动赋焦（如 OtherFocusReason / ActiveWindowFocusReason）时边框保持 1px 基准暗金。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._interactive_reasons = {
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        }

    def _resolve_targets(self, widget: QWidget) -> list[QWidget]:
        targets: list[QWidget] = []
        if isinstance(widget, (QLineEdit, QAbstractSpinBox, QTextEdit, QPlainTextEdit, QAbstractItemView)):
            targets.append(widget)
        p = widget.parentWidget()
        if p is not None and isinstance(p, (QAbstractSpinBox, QAbstractItemView)):
            targets.append(p)
        return targets

    def _set_focused(self, widget: QWidget, focused: bool) -> None:
        for target in self._resolve_targets(widget):
            if target.property("focused") != focused:
                target.setProperty("focused", focused)
                style = target.style()
                if style:
                    style.unpolish(target)
                    style.polish(target)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if not isinstance(obj, QWidget):
            return False
        ev_type = ev.type()
        if ev_type == QEvent.Type.FocusIn:
            reason = getattr(ev, "reason", lambda: None)()
            if reason in self._interactive_reasons:
                self._set_focused(obj, True)
            else:
                self._set_focused(obj, False)
        elif ev_type == QEvent.Type.FocusOut:
            self._set_focused(obj, False)
        elif ev_type in (QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress):
            self._set_focused(obj, True)
        return False


def create_application(argv: list[str] | None = None) -> QApplication:
    if os.name == "nt":
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv if argv is not None else sys.argv)
    focus_filter = FocusVisibleFilter(app)
    app.installEventFilter(focus_filter)
    app._focus_visible_filter = focus_filter
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
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
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
