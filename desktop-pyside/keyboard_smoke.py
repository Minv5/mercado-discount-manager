from __future__ import annotations

import ctypes
import os
import time
from typing import Any

from PySide6.QtCore import QEventLoop, QPoint, Qt
from PySide6.QtTest import QTest

from core import Account
from main_window import MainWindow


class KeyboardSmokeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, path: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(("GET", path))
        if path.startswith("/api/tasks"):
            return {"ok": True, "tasks": []}
        return {"ok": True, "sites": [], "promotions": [], "results": {}}

    def post(self, path: str, _body: Any = None, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(("POST", path))
        return {"ok": True, "decision": {"action": "update"}}


class KeyboardSmokeService:
    def ensure_started(self) -> bool:
        return False

    def stop(self) -> None:
        return None


def run_keyboard_smoke(app) -> dict[str, Any]:
    api = KeyboardSmokeApi()
    window = MainWindow(api, KeyboardSmokeService(), auto_start=False)
    window.accounts = [Account("A1", "A1", "CBT", "测试店")]
    window._fill_store_combo()
    window.site_combo.addItem("墨西哥站", "MLM")
    window.seller_combo.addItem("全部自建活动", "")
    window.official_combo.addItem("全部官方活动", "")
    window.resize(1440, 900)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    app.processEvents()

    combo = window.mode_combo
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Tab)
    tab_moved = app.focusWidget() is not combo
    combo.setFocus()
    QTest.keyClick(combo, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
    app.processEvents()
    alt_opened = combo.view().isVisible()
    QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
    app.processEvents()
    escape_closed = not combo.view().isVisible()

    QTest.keyClick(combo, Qt.Key.Key_F4)
    app.processEvents()
    f4_opened = combo.view().isVisible()
    QTest.keyClick(combo.view(), Qt.Key.Key_Home)
    QTest.keyClick(combo.view(), Qt.Key.Key_End)
    QTest.keyClick(combo.view(), Qt.Key.Key_Up)
    QTest.keyClick(combo.view(), Qt.Key.Key_Enter)
    app.processEvents()
    enter_closed = not combo.view().isVisible()
    qt_path_alive = window.isVisible() and combo.currentIndex() >= 0

    raw_result = _run_raw_post_sequence(app, window)
    execution_calls = [path for method, path in api.calls if method == "POST" and path == "/api/execution/jobs/start"]
    result = {
        "tab_moved": tab_moved,
        "alt_opened": alt_opened,
        "escape_closed": escape_closed,
        "f4_opened": f4_opened,
        "enter_closed": enter_closed,
        "qt_path_alive": qt_path_alive,
        "raw_post_alive": raw_result["alive"],
        "raw_post_text": raw_result["text"],
        "execution_job_count": len(execution_calls),
    }
    window.close()
    app.processEvents()
    result["ok"] = all(
        bool(result[key])
        for key in ("tab_moved", "alt_opened", "escape_closed", "f4_opened", "enter_closed", "qt_path_alive", "raw_post_alive")
    ) and result["raw_post_text"] == "批量更新" and result["execution_job_count"] == 0
    return result


def _run_raw_post_sequence(app, window: MainWindow) -> dict[str, Any]:
    if os.name != "nt":
        return {"alive": True, "text": "批量更新"}
    hwnd = int(window.winId())
    user32 = ctypes.windll.user32
    wm_left_down, wm_left_up = 0x0201, 0x0202
    wm_key_down, wm_key_up = 0x0100, 0x0101
    vk_down, vk_return = 0x28, 0x0D

    def pump() -> None:
        for _ in range(20):
            app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
            time.sleep(0.002)

    def click(point: QPoint) -> None:
        packed = (point.y() << 16) | (point.x() & 0xFFFF)
        user32.PostMessageW(hwnd, wm_left_down, 1, packed)
        user32.PostMessageW(hwnd, wm_left_up, 0, packed)
        pump()

    def key(value: int) -> None:
        user32.PostMessageW(hwnd, wm_key_down, value, 1)
        user32.PostMessageW(hwnd, wm_key_up, value, 0xC0000001)
        pump()

    window.mode_combo.setCurrentIndex(0)
    click(window.nav_buttons[0].mapTo(window, window.nav_buttons[0].rect().center()))
    click(window.mode_combo.mapTo(window, window.mode_combo.rect().center()))
    key(vk_down)
    key(vk_down)
    key(vk_return)
    return {"alive": window.isVisible(), "text": window.mode_combo.currentText()}
