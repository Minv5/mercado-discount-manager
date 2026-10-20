from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QComboBox

from core import get_store_alias

logger = logging.getLogger(__name__)


def populate_store_combo(
    combo: QComboBox,
    accounts: list[Any] | None = None,
    auth_mgr: Any = None,
    all_label: str = "全部店铺",
    all_value: str = "all",
    include_account_id_in_label: bool = False,
    selected_value: str | None = None,
) -> None:
    """Populate a QComboBox with store options safely and uniformly.

    - Blocks signals during update to prevent spurious cascade reload.
    - Preserves previous selection if possible.
    - Handles both dictionary accounts and model Account objects.
    - Resolves Chinese store alias via core.get_store_alias.
    """
    blocker = QSignalBlocker(combo)
    current_data = combo.currentData() if selected_value is None else selected_value
    combo.clear()

    if all_label:
        combo.addItem(all_label, all_value)

    acc_list = accounts
    if acc_list is None and auth_mgr is not None and hasattr(auth_mgr, "list_accounts"):
        try:
            acc_list = auth_mgr.list_accounts()
        except Exception as e:
            logger.debug("Failed to list accounts in populate_store_combo: %s", e)
            acc_list = []

    if acc_list:
        for acc in acc_list:
            if isinstance(acc, dict):
                aid = str(acc.get("account_id") or acc.get("id") or acc.get("user_id") or "").strip()
                site = str(acc.get("site_id") or "").strip()
                store_name = str(acc.get("store_name") or acc.get("name") or "").strip()
            else:
                aid = str(
                    getattr(acc, "account_id", "")
                    or getattr(acc, "id", "")
                    or getattr(acc, "user_id", "")
                    or ""
                ).strip()
                site = str(getattr(acc, "site_id", "") or "").strip()
                store_name = str(getattr(acc, "store_name", "") or getattr(acc, "name", "") or "").strip()

            if not aid:
                continue

            alias = get_store_alias(auth_mgr, aid, site) if (auth_mgr or aid) else (store_name or aid)
            if include_account_id_in_label:
                disp = f"{alias} ({aid})" if f"({aid})" not in alias else alias
            else:
                disp = alias

            combo.addItem(disp, aid)

    # Restore selection
    if current_data is not None:
        idx = combo.findData(current_data)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentIndex(0)
    del blocker
