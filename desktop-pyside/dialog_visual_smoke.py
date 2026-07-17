from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPixmap

from app import create_application
from dialogs import ConfirmDialog


MESSAGES = {
    "short": "确认执行。",
    "execution": "店铺范围：全部店铺\n站点范围：全部站点\n执行动作：批量更新\n自建折扣：6%    官方折扣：7%\n以上为最终执行参数，请确认后执行。",
    "long": "将为以下店铺站点创建自建活动：\n\n" + "\n".join(f"测试店铺 {index + 1} / 墨西哥站" for index in range(120)),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=MESSAGES, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    app = create_application(["dialog-visual-smoke"])
    dialog = ConfirmDialog("最终执行确认", MESSAGES[args.kind], "确认执行", "取消")
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.show()
    dialog.adjustSize()
    dialog.ensurePolished()
    dialog.layout().activate()
    app.processEvents()
    canvas = QPixmap(dialog.width(), dialog.height())
    canvas.fill(QColor("#171B19"))
    dialog.render(canvas, targetOffset=canvas.rect().topLeft(), sourceRegion=QRect(0, 0, dialog.width(), dialog.height()))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ok = canvas.save(str(output))
    dialog.reject()
    app.processEvents()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
