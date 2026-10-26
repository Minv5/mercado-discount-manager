from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.crypto import get_data_dir

logger = logging.getLogger(__name__)

SITE_NAMES = {
    "MLB": "巴西",
    "MLM": "墨西哥",
    "MLC": "智利",
    "MCO": "哥伦比亚",
    "MLA": "阿根廷",
    "MLU": "乌拉圭",
    "MPE": "秘鲁",
}

SITE_FLAGS = {
    "MLB": "🇧🇷",
    "MLM": "🇲🇽",
    "MLC": "🇨🇱",
    "MCO": "🇨🇴",
    "MLA": "🇦🇷",
    "MLU": "🇺🇾",
    "MPE": "🇵🇪",
}

STATUS_NAMES = {
    "active": "在售",
    "paused": "暂停",
    "closed": "已下架",
}


@dataclass
class ItemModel:
    """Standardized Mercado Libre Listing Model."""
    id: str = ""
    title: str = ""
    price: float = 0.0
    currency_id: str = "USD"
    status: str = "active"  # "active" | "paused" | "closed"
    available_quantity: int = 0
    sold_quantity: int = 0
    thumbnail: str = ""
    permalink: str = ""
    sku: str = ""
    account_id: str = ""
    account_name: str = ""
    site_id: str = ""
    updated_at: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def site_display(self) -> str:
        flag = SITE_FLAGS.get(self.site_id, "🌐")
        cn = SITE_NAMES.get(self.site_id, self.site_id)
        return f"{flag} {cn} · {self.site_id}"

    @property
    def status_display(self) -> str:
        return STATUS_NAMES.get(self.status.lower(), self.status)

    @property
    def price_display(self) -> str:
        if not self.price:
            return "--"
        return f"{self.currency_id} {self.price:,.2f}"


def get_items_snapshot_path() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "items_snapshot.json"


def save_items_snapshot(items: list[ItemModel]) -> None:
    """Persist items to local snapshot file safely."""
    path = get_items_snapshot_path()
    try:
        raw_list = []
        for it in items:
            d = asdict(it)
            # Remove bulky raw_data to keep file lightweight and fast
            d.pop("raw_data", None)
            raw_list.append(d)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(raw_list, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    except Exception as err:
        logger.error("Failed saving items snapshot: %s", err)


def load_items_snapshot() -> list[ItemModel]:
    """Load items from local snapshot file."""
    path = get_items_snapshot_path()
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
        raw_list = json.loads(content)
        if not isinstance(raw_list, list):
            return []
        items: list[ItemModel] = []
        valid_fields = set(ItemModel.__dataclass_fields__.keys())
        for r in raw_list:
            if isinstance(r, dict):
                clean_r = {k: v for k, v in r.items() if k in valid_fields}
                items.append(ItemModel(**clean_r))
        return items
    except Exception as err:
        logger.error("Failed loading items snapshot: %s", err)
        return []
