from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication

from app import create_application
from core import Account
from main_window import MainWindow


class SmokeApi:
    def get(self, path: str, **_kwargs):
        if path.startswith("/api/tasks"):
            return {"ok": True, "tasks": []}
        return {"ok": True}

    def post(self, _path: str, _body=None, **_kwargs):
        return {"ok": True, "decision": {"action": "update"}}


class SmokeService:
    def ensure_started(self) -> bool:
        return False

    def stop(self) -> None:
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()
    app = create_application(["visual-smoke"])
    window = MainWindow(SmokeApi(), SmokeService(), auto_start=False)
    window.accounts = [
        Account("SMOKE-A", "", "CBT", "湖北"),
        Account("SMOKE-B", "", "CBT", "广州"),
        Account("SMOKE-C", "", "CBT", "湖南"),
    ]
    window.global_seller_discount = 6
    window.global_official_discount = 7
    window._fill_store_combo()
    window.site_combo.addItem("墨西哥站", "MLM")
    window.seller_combo.addItem("全部自建活动", "")
    window.official_combo.addItem("全部官方活动", "")
    window._apply_global_discounts()
    window.today_label.setText("今日折扣：自建6%，官方7%。不同店铺需要不同动作，请手动选择后分开执行。")
    window._apply_current_records(
        [
            {
                "created_at": "2026-07-10T08:20:00Z",
                "action": "update",
                "seller_activity_text": "5%",
                "official_activity_text": "6%",
                "quantity_type": "已更新商品数",
                "relation_count": 24910,
                "unique_item_count": 24775,
                "activity_failure_count": 0,
                "success_count": 24775,
                "failed_count": 0,
                "short_failure_reason": "",
            },
            {
                "created_at": "2026-07-07T08:43:06Z",
                "action": "cancel",
                "relation_count": 2400,
                "unique_item_count": 2315,
                "activity_failure_count": 1,
                "request_success_count": 2315,
                "live_verified_removed_count": 2295,
                "pending_verification_count": 20,
                "success_count": 2295,
                "failed_count": 19,
                "skipped_count": 20,
                "short_failure_reason": "商品失败19，活动失败1",
            },
        ]
    )
    window.log("已读取店铺列表：湖北、广州、湖南。")
    window.log("工作台已打开，当前仅进行离屏视觉检查。")
    window.resize(args.width, args.height)
    window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    window.show()
    window.ensurePolished()
    window.layout().activate()
    app.processEvents()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas = QPixmap(args.width, args.height)
    canvas.fill(QColor("#171B19"))
    window.render(canvas, targetOffset=canvas.rect().topLeft(), sourceRegion=QRect(0, 0, args.width, args.height))
    ok = canvas.save(str(output))
    window.close()
    QApplication.processEvents()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
