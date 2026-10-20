from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPoint, QSettings, QSize, QUrl, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPixmap, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from core import SITE_FLAGS, get_store_alias, resolve_store_name
from engine.auth import AuthManager
from engine.client import MercadoClient, clean_error_message
from service_center.image_cache import get_placeholder_pixmap, load_image_async
from service_center.models import SITE_LANGUAGES, SITE_NAMES, ServiceMessage, ServiceSession, get_item_url
from service_center.template_manager import ReplyTemplate, TemplateManager
from service_center.toast_notification import ToastNotification
from service_center.translator import translate_text
from theme import COLORS, STATUS_COLORS
from ui_common import (
    ImagePreviewDialog,
    copy_pure_text,
    extract_copyable_text,
    make_click_copy_handler,
    populate_store_combo,
    show_image_preview,
    show_pure_copy_menu,
)

logger = logging.getLogger(__name__)


def clean_message_text(text: str) -> str:
    """Strip HTML markup and entities from message while preserving clean formatting."""
    if not text:
        return ""
    text = html.unescape(str(text))
    text = text.replace('\xa0', ' ')
    text = re.sub(r'(?i)<br\s*/?>|</p>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\n{2,}', '\n', text)
    return text.strip()


def play_notification_chime() -> None:
    """Cross-platform non-blocking notification sound playback."""
    try:
        if sys.platform == "darwin":
            sound_file = Path("/System/Library/Sounds/Funk.aiff")
            if sound_file.exists():
                subprocess.Popen(["afplay", str(sound_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        elif sys.platform == "win32":
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                return
            except Exception:
                pass
    except Exception:
        pass
    try:
        QApplication.beep()
    except Exception:
        pass


CLAIM_STAGE_NAMES: dict[str, str] = {
    "claim": "买家发起投诉",
    "dispute": "平台官方调解中",
    "recontact": "售后协商阶段",
    "closed": "已结案",
    "none": "已结案",
}


def format_message_date(date_val: Any) -> str:
    """Format diverse Mercado message dates into clean Beijing time 'YYYY-MM-DD HH:MM:SS' string."""
    if not date_val:
        return ""
    if isinstance(date_val, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(date_val), tz=timezone(timedelta(hours=8)))
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return ""
    if isinstance(date_val, dict):
        raw_val = str(date_val.get("created") or date_val.get("received") or date_val.get("available") or "")
    else:
        raw_val = str(date_val).strip()
    if not raw_val:
        return ""
    try:
        clean_val = raw_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_val)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    if "T" in raw_val:
        parts = raw_val.split("T")
        date_part = parts[0]
        time_part = parts[1][:8] if len(parts) > 1 else ""
        return f"{date_part} {time_part}".strip()
    return raw_val[:19]


def parse_iso_timestamp(date_val: Any) -> float:
    """Parse diverse ISO 8601 strings or date dicts into UTC timestamp float for sorting."""
    if not date_val:
        return 0.0
    if isinstance(date_val, (int, float)):
        return float(date_val)
    if isinstance(date_val, dict):
        raw_val = str(date_val.get("created") or date_val.get("received") or date_val.get("available") or "")
    else:
        raw_val = str(date_val).strip()
    if not raw_val:
        return 0.0
    try:
        clean_val = raw_val.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        try:
            cleaned = re.sub(r"[^\d]", "", raw_val[:19])
            return float(cleaned) if cleaned else 0.0
        except Exception:
            return 0.0


# resolve_store_name and get_store_alias are unified in core.py


def save_sessions_snapshot(sessions: list[ServiceSession]) -> None:
    """Save sessions metadata and enriched product info to local disk snapshot."""
    try:
        from engine.crypto import get_data_dir
        path = get_data_dir() / "sessions_snapshot.json"
        data = []
        for s in sessions:
            msg_list = []
            for m in (getattr(s, "messages", None) or []):
                msg_list.append({
                    "id": m.id,
                    "sender_role": m.sender_role,
                    "sender_name": m.sender_name,
                    "text": m.text,
                    "text_translated": m.text_translated,
                    "created_at": m.created_at,
                    "attachments": m.attachments or [],
                    "stage": m.stage,
                    "is_seller": m.is_seller,
                    "is_read": m.is_read,
                    "read_date": m.read_date,
                })
            data.append({
                "id": s.id,
                "session_type": s.session_type,
                "account_id": s.account_id,
                "account_name": s.account_name,
                "site_id": s.site_id,
                "child_user_id": s.child_user_id,
                "buyer_id": s.buyer_id,
                "buyer_nickname": s.buyer_nickname,
                "item_id": s.item_id,
                "item_title": s.item_title,
                "item_thumbnail": s.item_thumbnail,
                "item_price": s.item_price,
                "item_permalink": s.item_permalink,
                "sku": s.sku,
                "variation_id": s.variation_id,
                "order_id": s.order_id,
                "pack_id": s.pack_id,
                "status": s.status,
                "stage": s.stage,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "summary": s.summary,
                "summary_translated": s.summary_translated,
                "can_reply": s.can_reply,
                "blocked_reason": s.blocked_reason,
                "is_unread": s.is_unread,
                "affects_reputation": getattr(s, "affects_reputation", ""),
                "messages": msg_list,
            })
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception as err:
        logger.debug("Failed to save sessions snapshot: %s", err)


def extract_sku_picture_url(item: dict[str, Any], variation_id: str = "", sku: str = "") -> str:
    """Extract precise SKU variation image if variation_id or sku is provided, falling back to main item image."""
    if not item or not isinstance(item, dict):
        return ""
    pictures = item.get("pictures") or []
    pic_map: dict[str, str] = {}
    for p in pictures:
        if isinstance(p, dict):
            pid = str(p.get("id") or "").strip()
            url = str(p.get("secure_url") or p.get("url") or "").strip()
            if pid and url:
                pic_map[pid] = url

    target_vid = str(variation_id or "").strip()
    target_sku = str(sku or "").strip().lower()
    variations = item.get("variations") or []

    if target_vid and target_vid != "0":
        for v in variations:
            if isinstance(v, dict) and str(v.get("id") or "").strip() == target_vid:
                for p_id in (v.get("picture_ids") or []):
                    p_id_str = str(p_id).strip()
                    if p_id_str in pic_map:
                        return pic_map[p_id_str]
                if v.get("picture_url"):
                    return str(v.get("picture_url"))
                break

    if target_sku:
        for v in variations:
            if isinstance(v, dict):
                v_sku = str(v.get("seller_custom_field") or v.get("seller_sku") or "").strip().lower()
                if v_sku and (v_sku == target_sku or v_sku in target_sku or target_sku in v_sku):
                    for p_id in (v.get("picture_ids") or []):
                        p_id_str = str(p_id).strip()
                        if p_id_str in pic_map:
                            return pic_map[p_id_str]
                    if v.get("picture_url"):
                        return str(v.get("picture_url"))
                    break

        # Match by attribute_combinations (e.g. color, size, pattern in SKU)
        best_v = None
        best_score = 0
        for v in variations:
            if isinstance(v, dict):
                score = 0
                for combo in (v.get("attribute_combinations") or []):
                    if isinstance(combo, dict):
                        val_name = str(combo.get("value_name") or "").strip().lower()
                        if val_name and val_name in target_sku:
                            score += len(val_name)
                if score > best_score:
                    best_score = score
                    best_v = v
        if best_v:
            for p_id in (best_v.get("picture_ids") or []):
                p_id_str = str(p_id).strip()
                if p_id_str in pic_map:
                    return pic_map[p_id_str]
            if best_v.get("picture_url"):
                return str(best_v.get("picture_url"))

    if pictures and isinstance(pictures[0], dict):
        first_url = str(pictures[0].get("secure_url") or pictures[0].get("url") or "").strip()
        if first_url:
            return first_url
    return str(item.get("secure_thumbnail") or item.get("thumbnail") or "").strip()


def is_fallback_title(t: str) -> bool:
    """Check if title is empty or a temporary placeholder that needs replacement."""
    title = (t or "").strip()
    return not title or title == "商品" or title == "美客多会话商品" or title.startswith("商品 ") or (title.startswith("订单 ") and ("售后私信" in title or "纠纷索赔" in title))


def load_sessions_snapshot() -> list[ServiceSession]:
    """Load cached sessions from local disk snapshot for instant 0.05s cold start."""
    try:
        from engine.crypto import get_data_dir
        path = get_data_dir() / "sessions_snapshot.json"
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        sessions: list[ServiceSession] = []
        valid_fields = set(ServiceSession.__dataclass_fields__.keys())
        for item in data:
            if isinstance(item, dict):
                raw_msgs = item.pop("messages", None) or []
                clean_item = {k: v for k, v in item.items() if k in valid_fields}
                sess = ServiceSession(**clean_item)
                for rm in raw_msgs:
                    if isinstance(rm, dict):
                        sess.messages.append(ServiceMessage(**rm))
                sessions.append(sess)
        return sessions
    except Exception as err:
        logger.debug("Failed to load sessions snapshot: %s", err)
        return []


# ----------------------------------------------------------------------
# Lightbox / Image Preview Dialog (Extracted to ui_common.lightbox)
# ----------------------------------------------------------------------
# ImagePreviewDialog and show_image_preview are imported from ui_common above.


# ----------------------------------------------------------------------
# Background Workers for Non-Blocking Operations
# ----------------------------------------------------------------------

class SafeWorkerBase(QObject):
    """Base class for background workers using standard threading.Thread.

    Inheriting from QObject gives Qt Signal/Slot delivery across threads,
    while using Python threading.Thread avoids C++ QThread lifecycle crashes
    (e.g., 'QThread: Destroyed while thread is still running').
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._is_stopped: bool = False

    def start(self) -> None:
        self._is_stopped = False
        self._thread = threading.Thread(target=self._exec_run, daemon=True)
        self._thread.start()

    def _exec_run(self) -> None:
        try:
            self.run()
        except Exception as exc:
            logger.error("Unhandled exception in %s: %s", self.__class__.__name__, exc)

    def run(self) -> None:
        raise NotImplementedError

    def isRunning(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def wait(self, timeout_ms: int | None = None) -> bool:
        if self._thread and self._thread.is_alive():
            timeout_sec = (timeout_ms / 1000.0) if timeout_ms is not None else None
            self._thread.join(timeout=timeout_sec)
            return not self._thread.is_alive()
        return True

    def quit(self) -> None:
        self._is_stopped = True


class FetchConversationMessagesWorker(SafeWorkerBase):
    finished = Signal(str, list, str)  # session_id, messages, detected_buyer_name
    error = Signal(str, str)            # session_id, error_message

    def __init__(self, client: MercadoClient, session: ServiceSession, auth_mgr: AuthManager | None = None):
        super().__init__()
        self.client = client
        self.session = session
        self.auth_mgr = auth_mgr

    def run(self) -> None:
        messages: list[ServiceMessage] = []
        detected_buyer_name: str = ""
        sess_id = self.session.id
        sess_type = self.session.session_type
        account_id = self.session.account_id
        pack_id = self.session.pack_id or self.session.order_id
        buyer_nickname = self.session.buyer_nickname
        child_user_id = self.session.child_user_id

        try:
            if sess_type == "question":
                messages = list(self.session.messages or [])
                self.finished.emit(sess_id, messages, detected_buyer_name)
                return

            if sess_type == "claim":
                cid = sess_id.replace("c_", "").strip()
                if not getattr(self.session, "affects_reputation", ""):
                    try:
                        rep_res = self.client.get_claim_affects_reputation(account_id, cid)
                        if isinstance(rep_res, dict):
                            self.session.affects_reputation = str(rep_res.get("affects_reputation") or "")
                    except Exception as rep_err:
                        logger.debug("Failed to get reputation impact for claim %s: %s", cid, rep_err)

                raw_msgs = self.client.get_claim_messages(account_id, cid)
                if isinstance(raw_msgs, list):
                    # Sort chronologically by date_created / message_date
                    sorted_raw = sorted(
                        raw_msgs,
                        key=lambda m: str(m.get("date_created") or m.get("message_date") or ""),
                    )
                else:
                    sorted_raw = []

                for rm in sorted_raw:
                    m_id = str(rm.get("id") or rm.get("hash") or rm.get("date_created") or "")
                    sender_role = str(rm.get("sender_role") or "buyer")
                    is_seller = sender_role in ("respondent", "seller")
                    is_mediator = (sender_role == "mediator")
                    if is_seller:
                        s_name = "卖家"
                    elif is_mediator:
                        s_name = "美客多仲裁调解员"
                    else:
                        s_name = buyer_nickname or "买家"

                    raw_text = str(rm.get("message") or "")
                    text = clean_message_text(raw_text)

                    # Extract translated message if provided directly by Mercado Libre
                    raw_trans = str(rm.get("translated_message") or "")
                    text_trans = clean_message_text(raw_trans) if raw_trans else ""

                    # Auto-translate to Chinese if not in Chinese or if text_trans is identical to raw text
                    needs_zh_trans = (
                        not text_trans
                        or text_trans.strip().lower() == text.strip().lower()
                        or not any('\u4e00' <= c <= '\u9fff' for c in text_trans)
                    )
                    if needs_zh_trans and text:
                        if not any('\u4e00' <= c <= '\u9fff' for c in text):
                            try:
                                candidate = text_trans if (text_trans and text_trans.strip().lower() != text.strip().lower()) else text
                                zh = translate_text(candidate, target_lang="zh", timeout=3.0)
                                if zh and zh.strip().lower() != text.strip().lower():
                                    text_trans = zh
                                elif text_trans.strip().lower() == text.strip().lower():
                                    text_trans = ""
                            except Exception:
                                if text_trans.strip().lower() == text.strip().lower():
                                    text_trans = ""
                        else:
                            text_trans = ""
                    elif text_trans.strip().lower() == text.strip().lower():
                        text_trans = ""

                    att_urls: list[str] = []
                    attachments = rm.get("attachments") or []
                    if isinstance(attachments, list) and attachments:
                        from engine.crypto import get_data_dir
                        cache_dir = get_data_dir() / "image_cache"
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        for att in attachments:
                            if isinstance(att, dict) and att.get("filename"):
                                fn = str(att["filename"])
                                local_path = cache_dir / f"claim_{cid}_{fn}"
                                if not local_path.exists():
                                    try:
                                        data = self.client.download_claim_attachment(account_id, cid, fn)
                                        if data:
                                            local_path.write_bytes(data)
                                    except Exception:
                                        pass
                                if local_path.exists():
                                    att_urls.append(str(local_path))
                                else:
                                    att_urls.append(fn)
                            elif isinstance(att, str):
                                att_urls.append(att)

                    created_at = format_message_date(rm.get("date_created") or rm.get("message_date"))
                    messages.append(
                        ServiceMessage(
                            id=m_id,
                            sender_role=sender_role,
                            sender_name=s_name,
                            text=text,
                            text_translated=text_trans,
                            created_at=created_at,
                            attachments=att_urls,
                            is_seller=is_seller,
                        )
                    )

            elif sess_type == "message":
                pid = pack_id
                res = self.client.get_pack_messages(account_id, pid)
                raw_msgs = res.get("messages") or []
                if isinstance(raw_msgs, list):
                    sorted_raw = list(reversed(raw_msgs))
                else:
                    sorted_raw = []

                # Pre-fetch associated claim ID if any message contains attachments
                has_any_att = any(bool(rm.get("message_attachments")) for rm in sorted_raw if isinstance(rm, dict))
                associated_claim_id = None
                real_oid = self.session.order_id or pid
                if has_any_att:
                    try:
                        ord_data = self.client.get_marketplace_order(account_id, real_oid)
                        meds = ord_data.get("mediations") or []
                        if meds:
                            associated_claim_id = str(meds[0].get("id") or "")
                    except Exception:
                        pass
                    if not associated_claim_id and pid != real_oid:
                        try:
                            ord_data = self.client.get_marketplace_order(account_id, pid)
                            meds = ord_data.get("mediations") or []
                            if meds:
                                associated_claim_id = str(meds[0].get("id") or "")
                        except Exception:
                            pass
                    if not associated_claim_id:
                        try:
                            claims_res = self.client.get_claims(account_id, "", resource_id=pid)
                            c_data = claims_res.get("data") or []
                            if c_data:
                                associated_claim_id = str(c_data[0].get("id") or "")
                        except Exception:
                            pass

                # If message contains seller attachments, check if item thumbnail needs de-duplication
                has_seller_att = any(
                    isinstance(rm, dict) and bool(rm.get("message_attachments")) and str((rm.get("from") or {}).get("user_id") or "") != str(self.session.buyer_id)
                    for rm in sorted_raw
                )
                if has_seller_att and self.session.item_id:
                    try:
                        item_data = self.client.get_item_detail(account_id, self.session.item_id)
                        pics = item_data.get("pictures") or []
                        if len(pics) > 1:
                            pic0_id = str(pics[0].get("id") or "")
                            if pic0_id and (pic0_id in self.session.item_thumbnail):
                                alt_url = str(pics[1].get("url") or pics[1].get("secure_url") or "")
                                if alt_url and alt_url != self.session.item_thumbnail:
                                    self.session.item_thumbnail = alt_url
                    except Exception as alt_err:
                        logger.debug("Failed checking alternate picture for session %s: %s", sess_id, alt_err)

                msg_res = res.get("message_resources") or []
                seller_resource_ids = {
                    str(r.get("id"))
                    for r in msg_res
                    if isinstance(r, dict) and r.get("name") == "sellers"
                }
                seller_ids: set[str] = {str(account_id)}
                if child_user_id:
                    seller_ids.add(str(child_user_id))
                if getattr(self, "auth_mgr", None) is not None:
                    try:
                        for site in self.auth_mgr.list_sites(account_id):
                            cuid = str(site.get("child_user_id") or "").strip()
                            if cuid:
                                seller_ids.add(cuid)
                    except Exception:
                        pass
                seller_ids |= seller_resource_ids

                for rm in sorted_raw:
                    m_id = str(rm.get("id") or "")
                    from_info = rm.get("from") or {}
                    from_user_id = str(from_info.get("user_id") or "")
                    from_name = str(from_info.get("name") or "")

                    is_seller = (from_user_id in seller_ids)

                    if is_seller:
                        s_name = "卖家"
                    else:
                        if from_name:
                            s_name = from_name
                        elif buyer_nickname and buyer_nickname != "买家":
                            s_name = buyer_nickname
                        elif from_user_id:
                            s_name = f"买家 {from_user_id[-6:]}"
                        else:
                            s_name = "买家"

                        if not detected_buyer_name and from_user_id:
                            detected_buyer_name = from_name or f"买家 {from_user_id[-6:]}"

                    text_info = rm.get("text") or {}
                    if isinstance(text_info, dict):
                        raw_text = str(text_info.get("plain") or "")
                    else:
                        raw_text = str(text_info or "")

                    text = clean_message_text(raw_text)

                    att_urls = []
                    attachments = rm.get("message_attachments") or []
                    if isinstance(attachments, list) and attachments:
                        from engine.crypto import get_data_dir
                        cache_dir = get_data_dir() / "image_cache"
                        cache_dir.mkdir(parents=True, exist_ok=True)
                        for att in attachments:
                            if isinstance(att, dict):
                                direct_url = str(att.get("url") or att.get("render_url") or "").strip()
                                fn = str(att.get("filename") or "").strip()
                                orig_fn = str(att.get("original_filename") or "").strip()
                                target_fn = fn or orig_fn
                                if direct_url.startswith("http"):
                                    att_urls.append(direct_url)
                                elif target_fn:
                                    local_path = cache_dir / f"msg_{pid}_{target_fn}"
                                    if not local_path.exists():
                                        # 1. Download via associated claim attachment if order in mediation
                                        if associated_claim_id and fn:
                                            try:
                                                data = self.client.download_claim_attachment(account_id, associated_claim_id, fn)
                                                if data:
                                                    local_path.write_bytes(data)
                                            except Exception:
                                                pass
                                        # 2. Try marketplace message attachment endpoint
                                        if not local_path.exists() and fn:
                                            try:
                                                site_hint = str(rm.get("site_id") or "MLM")
                                                data = self.client.request_bytes(account_id, "GET", f"/marketplace/messages/attachments/{fn}", params={"site_id": site_hint})
                                                if data:
                                                    local_path.write_bytes(data)
                                            except Exception:
                                                pass
                                    if local_path.exists():
                                        att_urls.append(str(local_path))
                                    elif direct_url:
                                        att_urls.append(direct_url)
                            elif isinstance(att, str):
                                att_urls.append(att)

                    raw_mdate = rm.get("message_date") or rm.get("date_created") or {}
                    created_at = format_message_date(raw_mdate)
                    is_read = False
                    read_date = ""
                    if isinstance(raw_mdate, dict):
                        read_val = raw_mdate.get("read")
                        if read_val:
                            is_read = True
                            read_date = format_message_date(read_val)

                    # Extract / auto-translate text for message
                    raw_trans = str(rm.get("translated_message") or "")
                    text_trans = clean_message_text(raw_trans) if raw_trans else ""

                    needs_zh_trans = (
                        not text_trans
                        or text_trans.strip().lower() == text.strip().lower()
                        or not any('\u4e00' <= c <= '\u9fff' for c in text_trans)
                    )
                    if needs_zh_trans and text:
                        if not any('\u4e00' <= c <= '\u9fff' for c in text):
                            try:
                                candidate = text_trans if (text_trans and text_trans.strip().lower() != text.strip().lower()) else text
                                zh = translate_text(candidate, target_lang="zh", timeout=3.0)
                                if zh and zh.strip().lower() != text.strip().lower():
                                    text_trans = zh
                                elif text_trans.strip().lower() == text.strip().lower():
                                    text_trans = ""
                            except Exception:
                                if text_trans.strip().lower() == text.strip().lower():
                                    text_trans = ""
                        else:
                            text_trans = ""
                    elif text_trans.strip().lower() == text.strip().lower():
                        text_trans = ""

                    messages.append(
                        ServiceMessage(
                            id=m_id,
                            sender_role="seller" if is_seller else "buyer",
                            sender_name=s_name,
                            text=text,
                            text_translated=text_trans,
                            created_at=created_at,
                            attachments=att_urls,
                            is_seller=is_seller,
                            is_read=is_read,
                            read_date=read_date,
                        )
                    )

            self.finished.emit(sess_id, messages, detected_buyer_name)
        except Exception as err:
            logger.warning("Failed to fetch messages for session %s: %s", sess_id, err)
            self.error.emit(sess_id, str(err))


class FetchSessionsWorker(SafeWorkerBase):
    finished = Signal(list)
    sessions_chunk = Signal(list)
    error = Signal(str)

    def __init__(
        self,
        client: MercadoClient,
        auth_mgr: AuthManager,
        account_ids: list[str],
        session_type: str,
        cached_sessions: list[ServiceSession] | None = None,
    ):
        super().__init__()
        self.client = client
        self.auth_mgr = auth_mgr
        self.account_ids = account_ids
        self.session_type = session_type
        self.cached_sessions = cached_sessions or []
        self.cache_map: dict[str, ServiceSession] = {s.id: s for s in self.cached_sessions}
        self._not_found_orders: set[str] = set()

    def _fetch_questions_for_account(self, aid: str, acc_name: str) -> list[ServiceSession]:
        q_sessions: list[ServiceSession] = []
        seen_question_ids: set[str] = set()
        for q_status in ("UNANSWERED", "ANSWERED"):
            offset = 0
            page_size = 50
            while True:
                try:
                    res = self.client.get_questions(aid, status=q_status, limit=page_size, offset=offset)
                    raw_questions = res.get("questions") or []
                    if not raw_questions:
                        break
                    for q in raw_questions:
                        q_id = str(q.get("id") or "")
                        if not q_id or q_id in seen_question_ids:
                            continue
                        seen_question_ids.add(q_id)

                        item_id = str(q.get("marketplace_item_id") or q.get("item_id") or "")
                        site_id = str(q.get("site_id") or (item_id[:3] if len(item_id) >= 3 else "MLB"))
                        q_text = str(q.get("text_translated") or q.get("text") or "")
                        created_at = str(q.get("date_created") or "")
                        buyer_data = q.get("from") or {}
                        buyer_id = str(buyer_data.get("id") or "")
                        is_unans = (q_status == "UNANSWERED" or q.get("status") == "UNANSWERED")

                        sess = ServiceSession(
                            id=f"q_{q_id}",
                            session_type="question",
                            account_id=aid,
                            account_name=acc_name,
                            site_id=site_id,
                            buyer_id=buyer_id,
                            buyer_nickname=f"买家 {buyer_id[-6:]}" if buyer_id else "买家",
                            item_id=item_id,
                            item_title=f"商品 {item_id}",
                            status="unanswered" if is_unans else "answered",
                            created_at=created_at,
                            summary=q_text if is_unans else f"[已回复] {q_text}",
                            summary_translated="",
                            can_reply=is_unans,
                            is_unread=is_unans,
                            raw_data=q,
                        )
                        cached = self.cache_map.get(sess.id)
                        if cached:
                            if not sess.item_thumbnail and cached.item_thumbnail:
                                sess.item_thumbnail = cached.item_thumbnail
                            if cached.variation_id:
                                sess.variation_id = cached.variation_id
                            if (not sess.item_title or sess.item_title.startswith("商品 ")) and cached.item_title:
                                sess.item_title = cached.item_title
                            if not sess.item_price and cached.item_price:
                                sess.item_price = cached.item_price
                            if not sess.item_permalink and cached.item_permalink:
                                sess.item_permalink = cached.item_permalink
                            if not sess.sku and cached.sku:
                                sess.sku = cached.sku
                        sess.messages.append(
                            ServiceMessage(
                                id=q_id,
                                sender_role="buyer",
                                sender_name=sess.buyer_nickname,
                                text=q_text,
                                created_at=created_at,
                                is_seller=False,
                            )
                        )
                        ans_data = q.get("answer") or {}
                        if ans_data.get("text") or ans_data.get("text_translated"):
                            ans_text = str(ans_data.get("text_translated") or ans_data.get("text") or "")
                            sess.messages.append(
                                ServiceMessage(
                                    id=f"ans_{q_id}",
                                    sender_role="seller",
                                    sender_name="卖家",
                                    text=ans_text,
                                    created_at=str(ans_data.get("date_created") or ""),
                                    is_seller=True,
                                )
                            )
                        q_sessions.append(sess)

                    if q_status == "ANSWERED":
                        break
                    total_q = res.get("total") or 0
                    offset += len(raw_questions)
                    if offset >= total_q or len(raw_questions) < page_size or offset >= 100:
                        break
                except Exception as err:
                    logger.warning("Error fetching %s questions for %s: %s", q_status, aid, err)
                    break
        return q_sessions

    def _fetch_claims_for_account(self, aid: str, acc_name: str, sites: list[dict[str, Any]]) -> list[ServiceSession]:
        claim_sessions: list[ServiceSession] = []
        seen_claim_ids: set[str] = set()

        tasks = []
        for site in sites:
            child_uid = str(site.get("child_user_id") or "")
            site_id = str(site.get("site_id") or "MLB")
            if not child_uid:
                continue
            for c_status in ("opened", "closed"):
                tasks.append((child_uid, site_id, c_status))

        if not tasks:
            return []

        def _fetch_single_claim_target(target: tuple[str, str, str]) -> list[tuple[dict[str, Any], str, str, str]]:
            c_uid, s_id, status_val = target
            results: list[tuple[dict[str, Any], str, str, str]] = []
            offset = 0
            page_size = 50
            while True:
                try:
                    c_res = self.client.get_claims(aid, c_uid, status=status_val, limit=page_size, offset=offset)
                    c_list = c_res.get("data") or []
                    if not c_list:
                        break
                    for item in c_list:
                        results.append((item, s_id, c_uid, status_val))
                    if status_val == "closed":
                        break
                    total = c_res.get("paging", {}).get("total", 0)
                    offset += len(c_list)
                    if offset >= total or len(c_list) < page_size or offset >= 100:
                        break
                except Exception as c_err:
                    logger.warning("Error fetching %s claims for %s %s: %s", status_val, aid, c_uid, c_err)
                    break
            return results

        max_w = min(len(tasks), 8)
        with ThreadPoolExecutor(max_workers=max_w) as executor:
            futures = [executor.submit(_fetch_single_claim_target, t) for t in tasks]
            for f in as_completed(futures):
                try:
                    batch = f.result()
                    for c, site_id, child_uid, c_status in batch:
                        claim_id = str(c.get("id") or "")
                        if not claim_id or claim_id in seen_claim_ids:
                            continue
                        seen_claim_ids.add(claim_id)
                        sess_id = f"c_{claim_id}"
                        cached = self.cache_map.get(sess_id)

                        res_id = str(c.get("resource_id") or "")
                        res_type = str(c.get("resource") or "order")
                        stage = str(c.get("stage") or "claim")
                        reason = str(c.get("reason_id") or "退款索赔")
                        created_at = str(c.get("date_created") or "")

                        order_id = res_id
                        if cached and cached.order_id:
                            order_id = cached.order_id
                        elif res_type == "shipment" and res_id:
                            try:
                                sh_res = self.client.get_marketplace_shipment(aid, res_id)
                                real_oid = str(sh_res.get("order_id") or "").strip()
                                if real_oid:
                                    order_id = real_oid
                            except Exception as sh_err:
                                logger.debug("Failed to resolve shipment %s for claim %s: %s", res_id, claim_id, sh_err)

                        buyer_id = ""
                        for p in c.get("players") or []:
                            if p.get("role") == "complainant" or p.get("type") == "buyer":
                                buyer_id = str(p.get("user_id") or "")

                        is_op = (c_status == "opened")
                        status_tag = "进行中" if is_op else "已结案"

                        item_id = cached.item_id if cached else ""
                        item_title = cached.item_title if cached else ""
                        item_thumbnail = cached.item_thumbnail if cached else ""
                        item_price = cached.item_price if cached else ""
                        item_permalink = cached.item_permalink if cached else ""
                        sku_val = cached.sku if cached else ""
                        var_id = cached.variation_id if cached else ""
                        pack_id = (cached.pack_id or order_id) if cached else order_id
                        buyer_nick = (
                            cached.buyer_nickname
                            if (cached and cached.buyer_nickname and cached.buyer_nickname != "买家")
                            else (f"买家 {buyer_id[-6:]}" if buyer_id else "买家")
                        )

                        affects_rep = getattr(cached, "affects_reputation", "") if cached else ""

                        sess = ServiceSession(
                            id=sess_id,
                            session_type="claim",
                            account_id=aid,
                            account_name=acc_name,
                            site_id=site_id,
                            child_user_id=child_uid,
                            buyer_id=buyer_id or (cached.buyer_id if cached else ""),
                            buyer_nickname=buyer_nick,
                            item_id=item_id,
                            item_title=item_title,
                            item_thumbnail=item_thumbnail,
                            item_price=item_price,
                            item_permalink=item_permalink,
                            sku=sku_val,
                            variation_id=var_id,
                            order_id=order_id,
                            pack_id=pack_id,
                            status="opened" if is_op else "closed",
                            stage=stage,
                            created_at=created_at or (cached.created_at if cached else ""),
                            summary=f"[{status_tag}] [{stage.upper()}] 索赔原因: {reason}",
                            summary_translated=cached.summary_translated if cached else "",
                            can_reply=is_op,
                            is_unread=is_op,
                            affects_reputation=affects_rep,
                            raw_data=c,
                        )
                        claim_sessions.append(sess)
                except Exception as err:
                    logger.warning("Error collecting claim results: %s", err)

        # Resolve reputation impact concurrently for any claim sessions missing this field
        rep_needed = [s for s in claim_sessions if not s.affects_reputation]
        if rep_needed:
            def _fetch_rep(s: ServiceSession) -> None:
                cid = s.id.replace("c_", "").strip()
                try:
                    r = self.client.get_claim_affects_reputation(s.account_id, cid)
                    if isinstance(r, dict):
                        s.affects_reputation = str(r.get("affects_reputation") or "")
                except Exception as r_err:
                    logger.debug("Failed to fetch reputation for claim %s: %s", cid, r_err)

            with ThreadPoolExecutor(max_workers=min(len(rep_needed), 8)) as rep_exec:
                list(rep_exec.map(_fetch_rep, rep_needed))

        return claim_sessions

    def _fetch_messages_for_account(self, aid: str, acc_name: str, sites: list[dict[str, Any]]) -> list[ServiceSession]:
        msg_sessions: list[ServiceSession] = []
        seen_pack_ids: set[str] = set()

        account_seller_ids: set[str] = {str(aid)}
        for site in sites:
            cuid = str(site.get("child_user_id") or "").strip()
            if cuid:
                account_seller_ids.add(cuid)

        # Pre-fetch recent orders from parent CBT account and child sites for pack-to-order mapping
        order_list: list[dict[str, Any]] = []
        try:
            ord_res = self.client.search_marketplace_orders(aid, limit=100, sort="date_desc")
            order_list.extend(ord_res.get("results") or [])
        except Exception as e:
            logger.debug("Failed search orders for aid %s: %s", aid, e)

        for site in sites:
            cuid = str(site.get("child_user_id") or "").strip()
            if cuid and cuid != aid:
                try:
                    c_res = self.client.request(aid, "GET", "/marketplace/orders/search", params={"seller": cuid, "limit": 50, "sort": "date_desc"})
                    order_list.extend(c_res.get("results") or [])
                except Exception as e:
                    logger.debug("Failed search orders for child %s: %s", cuid, e)

        pack_info_map: dict[str, dict[str, Any]] = {}
        for ord_item in order_list:
            oid = str(ord_item.get("id") or "").strip()
            sub_orders = ord_item.get("orders") or []
            for so in sub_orders:
                so_seller = str(so.get("seller", {}).get("id") or "").strip()
                if so_seller:
                    account_seller_ids.add(so_seller)
            sub_oid = str(sub_orders[0].get("id") or "") if sub_orders else oid
            cfg_items = ord_item.get("config", {}).get("items") or []
            it_id = str(cfg_items[0].get("id") or "") if cfg_items else ""
            b_info = ord_item.get("buyer") or {}
            b_id = str(b_info.get("id") or "")
            b_nick = str(b_info.get("nickname") or "").strip()

            info = {
                "real_order_id": sub_oid,
                "item_id": it_id,
                "buyer_id": b_id,
                "buyer_nickname": b_nick,
                "ord_item": ord_item,
            }
            if oid:
                pack_info_map[oid] = info
            pid_val = str(ord_item.get("pack_id") or "").strip()
            if pid_val:
                pack_info_map[pid_val] = info

        # 3.1 Unread pack messages across child sites in parallel
        site_targets = [
            (str(site.get("child_user_id") or ""), str(site.get("site_id") or "MLB"))
            for site in sites
            if site.get("child_user_id")
        ]

        def _fetch_unread_for_site(target: tuple[str, str]) -> list[tuple[dict[str, Any], str, str]]:
            child_uid, site_id = target
            res_items: list[tuple[dict[str, Any], str, str]] = []
            try:
                m_res = self.client.get_unread_pack_messages(aid, child_uid)
                for res_item in m_res.get("results") or []:
                    res_items.append((res_item, site_id, child_uid))
            except Exception as err:
                logger.warning("Error fetching unread messages for %s %s: %s", aid, child_uid, err)
            return res_items

        if site_targets:
            with ThreadPoolExecutor(max_workers=min(len(site_targets), 6)) as executor:
                futures = [executor.submit(_fetch_unread_for_site, t) for t in site_targets]
                for f in as_completed(futures):
                    try:
                        for res_item, site_id, child_uid in f.result():
                            res_path = str(res_item.get("resource") or "")
                            pack_id = res_path.replace("/packs/", "").strip()
                            count = res_item.get("count", 1)
                            if not pack_id or pack_id in seen_pack_ids:
                                continue
                            seen_pack_ids.add(pack_id)

                            p_info = pack_info_map.get(pack_id)
                            real_oid = p_info["real_order_id"] if p_info else pack_id
                            real_it_id = p_info["item_id"] if (p_info and p_info["item_id"]) else ""
                            real_b_id = p_info["buyer_id"] if (p_info and p_info["buyer_id"]) else ""
                            real_b_nick = p_info["buyer_nickname"] if (p_info and p_info["buyer_nickname"]) else ""

                            sess = ServiceSession(
                                id=f"m_{pack_id}",
                                session_type="message",
                                account_id=aid,
                                account_name=acc_name,
                                site_id=site_id,
                                child_user_id=child_uid,
                                pack_id=pack_id,
                                order_id=real_oid,
                                item_id=real_it_id,
                                buyer_id=real_b_id,
                                buyer_nickname=real_b_nick or (f"买家 {real_b_id[-6:]}" if real_b_id else "买家"),
                                status="new_message",
                                summary=f"买家未读售后私信 ({count} 条)",
                                can_reply=True,
                                is_unread=True,
                                raw_data=res_item,
                            )
                            cached = self.cache_map.get(sess.id)
                            if cached:
                                if not sess.item_thumbnail and cached.item_thumbnail:
                                    sess.item_thumbnail = cached.item_thumbnail
                                if cached.variation_id:
                                    sess.variation_id = cached.variation_id
                                if cached.item_title:
                                    sess.item_title = cached.item_title
                                if cached.item_price:
                                    sess.item_price = cached.item_price
                                if cached.item_permalink:
                                    sess.item_permalink = cached.item_permalink
                                if cached.sku:
                                    sess.sku = cached.sku
                                if cached.buyer_nickname and cached.buyer_nickname != "买家":
                                    sess.buyer_nickname = cached.buyer_nickname
                            msg_sessions.append(sess)
                    except Exception as err:
                        logger.warning("Error parsing unread pack messages: %s", err)

        # 3.2 Recent orders conversation messages (concurrent probing)
        try:
            orders_to_probe: list[tuple[dict[str, Any], str, str]] = []
            for ord_item in order_list:
                pack_id = str(ord_item.get("id") or "").strip()
                sub_orders = ord_item.get("orders") or []
                sub_order_id = str(sub_orders[0].get("id") or "") if sub_orders else pack_id
                pid = str(ord_item.get("pack_id") or "").strip() or pack_id or sub_order_id
                if pid and pid not in seen_pack_ids:
                    orders_to_probe.append((ord_item, pid, sub_order_id))

            def _probe_pack_message(item_tuple: tuple[dict[str, Any], str, str]) -> tuple[dict[str, Any], str, str, dict[str, Any]] | None:
                ord_item, pid, sub_order_id = item_tuple
                try:
                    msg_res = self.client.get_pack_messages(aid, pid)
                    msgs = msg_res.get("messages") or []
                    if msgs:
                        return (ord_item, pid, sub_order_id, msg_res)
                except Exception:
                    pass
                return None

            if orders_to_probe:
                orders_to_probe = orders_to_probe[:10]
                max_probe_w = min(len(orders_to_probe), 8)
                with ThreadPoolExecutor(max_workers=max_probe_w) as probe_executor:
                    probe_futures = [probe_executor.submit(_probe_pack_message, t) for t in orders_to_probe]
                    for pf in as_completed(probe_futures):
                        try:
                            probe_result = pf.result()
                            if not probe_result:
                                continue
                            ord_item, pid, sub_order_id, msg_res = probe_result
                            if pid in seen_pack_ids:
                                continue
                            seen_pack_ids.add(pid)

                            msgs = msg_res.get("messages") or []
                            if msgs:
                                msgs.sort(key=lambda m: parse_iso_timestamp((m.get("message_date") or {}).get("created")))
                                last_m = msgs[-1]
                            else:
                                last_m = {}
                            msg_resources = msg_res.get("message_resources") or []
                            seller_resource_ids = {
                                str(r.get("id"))
                                for r in msg_resources
                                if isinstance(r, dict) and r.get("name") == "sellers"
                            }
                            effective_seller_ids = account_seller_ids | seller_resource_ids

                            from_info = last_m.get("from") or {}
                            from_user_id = str(from_info.get("user_id") or "")
                            is_seller_msg = (from_user_id in effective_seller_ids)

                            text_val = last_m.get("text") or {}
                            plain_txt = str(text_val.get("plain") or "") if isinstance(text_val, dict) else str(text_val or "")
                            m_date = last_m.get("message_date") or {}
                            read_val = m_date.get("read")
                            created_at = str(m_date.get("created") or "")

                            sub_orders = ord_item.get("orders") or []
                            is_cancelled = any(str(so.get("status") or "").lower() == "cancelled" for so in sub_orders)
                            has_mediations = any(bool(so.get("mediations")) for so in sub_orders)

                            target_child_uid = ""
                            if sub_orders:
                                target_child_uid = str(sub_orders[0].get("seller", {}).get("id") or "")

                            if is_cancelled:
                                status_str = "blocked"
                                is_unr = False
                                can_reply_val = False
                                blocked_msg = "订单已取消，买家售后私信通道已关闭"
                            elif has_mediations:
                                status_str = "blocked"
                                is_unr = False
                                can_reply_val = False
                                blocked_msg = "订单已转入纠纷索赔仲裁，请在订单索赔中处理"
                            elif not is_seller_msg:
                                status_str = "new_message"
                                is_unr = True
                                can_reply_val = True
                                blocked_msg = None
                            else:
                                is_unr = False
                                can_reply_val = True
                                blocked_msg = None
                                status_str = "read" if read_val else "unread"

                            buyer_info = ord_item.get("buyer") or {}
                            b_id = str(buyer_info.get("id") or "")
                            b_nick = str(buyer_info.get("nickname") or "").strip()
                            b_name = b_nick if b_nick else (f"买家 {b_id[-6:]}" if b_id else "买家")

                            cfg_items = ord_item.get("config", {}).get("items") or []
                            it_id = str(cfg_items[0].get("id") or "") if cfg_items else ""
                            it_title = ""
                            it_price = ""
                            sku_val = ""

                            ord_items = ord_item.get("order_items") or []
                            if ord_items:
                                first_oi = ord_items[0]
                                it_info = first_oi.get("item") or {}
                                if not it_id:
                                    it_id = str(it_info.get("id") or "")
                                it_title = str(it_info.get("title") or "")
                                sku_val = str(it_info.get("seller_custom_field") or it_info.get("seller_sku") or "")
                                if first_oi.get("unit_price"):
                                    cur = str(ord_item.get("currency_id") or "$")
                                    it_price = f"{cur} {first_oi.get('unit_price')}"

                            site_id = it_id[:3] if len(it_id) >= 3 else "MLB"
                            if not target_child_uid:
                                target_child_uid = next((str(s.get("child_user_id")) for s in sites if s.get("site_id") == site_id), "")

                            sess = ServiceSession(
                                id=f"m_{pid}",
                                session_type="message",
                                account_id=aid,
                                account_name=acc_name,
                                site_id=site_id,
                                child_user_id=target_child_uid,
                                buyer_id=b_id,
                                buyer_nickname=b_name,
                                item_id=it_id,
                                item_title=it_title,
                                item_price=it_price,
                                sku=sku_val,
                                pack_id=pid,
                                order_id=sub_order_id,
                                status=status_str,
                                created_at=created_at,
                                summary=plain_txt[:80] if plain_txt else "买家售后私信沟通",
                                can_reply=can_reply_val,
                                blocked_reason=blocked_msg,
                                is_unread=is_unr,
                                raw_data=ord_item,
                            )
                            cached = self.cache_map.get(sess.id)
                            if cached:
                                if not sess.item_thumbnail and cached.item_thumbnail:
                                    sess.item_thumbnail = cached.item_thumbnail
                                if cached.variation_id:
                                    sess.variation_id = cached.variation_id
                                if (not sess.item_title or sess.item_title.startswith("商品 ")) and cached.item_title:
                                    sess.item_title = cached.item_title
                                if not sess.item_price and cached.item_price:
                                    sess.item_price = cached.item_price
                                if not sess.item_permalink and cached.item_permalink:
                                    sess.item_permalink = cached.item_permalink
                                if not sess.sku and cached.sku:
                                    sess.sku = cached.sku
                            msg_sessions.append(sess)
                        except Exception as p_err:
                            logger.debug("Error parsing probed pack message: %s", p_err)
        except Exception as ord_err:
            logger.warning("Error searching recent orders for %s: %s", aid, ord_err)

        return msg_sessions

    def _enrich_account_sessions(self, aid: str, sessions: list[ServiceSession]) -> None:
        if not sessions:
            return

        # Recover item_id from permalink if missing
        for s in sessions:
            if not s.item_id and s.item_permalink:
                m = re.search(r'(ML[A-Z])-?(\d+)', s.item_permalink)
                if m:
                    s.item_id = f"{m.group(1)}{m.group(2)}"

        sessions_needing_enrich = [
            s for s in sessions
            if s.order_id and (not s.item_title or s.item_title.startswith("商品 ") or not s.item_id)
        ]
        # Prioritize active/unread/opened sessions first
        sessions_needing_enrich.sort(
            key=lambda s: (1 if s.is_unread or s.status == "opened" else 0),
            reverse=True,
        )

        orders_to_enrich: dict[str, dict[str, Any] | None] = {}
        for s in sessions_needing_enrich:
            if s.order_id not in self._not_found_orders and s.order_id not in orders_to_enrich:
                orders_to_enrich[s.order_id] = None
                if len(orders_to_enrich) >= 10:
                    break

        if orders_to_enrich:
            def _fetch_order_data(oid: str) -> tuple[str, dict[str, Any] | None]:
                if oid in self._not_found_orders:
                    return (oid, None)
                try:
                    ord_res = self.client.get_marketplace_order(aid, oid)
                    return (oid, ord_res)
                except Exception as err:
                    err_str = str(err)
                    if "404" in err_str or "not found" in err_str.lower() or "不存在" in err_str:
                        self._not_found_orders.add(oid)
                    return (oid, None)

            max_w = min(len(orders_to_enrich), 10)
            with ThreadPoolExecutor(max_workers=max_w) as executor:
                futures = [executor.submit(_fetch_order_data, oid) for oid in orders_to_enrich.keys()]
                for f in as_completed(futures):
                    try:
                        oid, ord_res = f.result()
                        if ord_res:
                            orders_to_enrich[oid] = ord_res
                    except Exception:
                        pass

            for sess in sessions:
                if sess.order_id:
                    ord_res = orders_to_enrich.get(sess.order_id)
                    if ord_res:
                        try:
                            ord_pack_id = str(ord_res.get("pack_id") or "").strip()
                            if ord_pack_id:
                                sess.pack_id = ord_pack_id

                            buyer = ord_res.get("buyer") or {}
                            b_nick = str(buyer.get("nickname") or "").strip()
                            b_id = str(buyer.get("id") or "").strip()
                            if b_nick:
                                sess.buyer_nickname = b_nick
                            elif b_id and (not sess.buyer_nickname or sess.buyer_nickname == "买家"):
                                sess.buyer_nickname = f"买家 {b_id[-6:]}"
                            if b_id and not sess.buyer_id:
                                sess.buyer_id = b_id

                            order_items = ord_res.get("order_items") or []
                            if order_items:
                                first_oi = order_items[0]
                                it_info = first_oi.get("item") or {}
                                if not sess.item_id:
                                    sess.item_id = str(it_info.get("id") or "")
                                if not sess.item_title or sess.item_title.startswith("商品 "):
                                    sess.item_title = str(it_info.get("title") or "")
                                if not sess.item_price and first_oi.get("unit_price"):
                                    cur = str(ord_res.get("currency_id") or "$")
                                    sess.item_price = f"{cur} {first_oi.get('unit_price')}"
                                if not sess.sku:
                                    sess.sku = str(it_info.get("seller_custom_field") or it_info.get("seller_sku") or "")
                                var_id = str(it_info.get("variation_id") or first_oi.get("variation_id") or "").strip()
                                if var_id and var_id != "0":
                                    sess.variation_id = var_id
                        except Exception as enrich_err:
                            logger.debug("Error processing enriched order items: %s", enrich_err)

        # Batch fetch missing item thumbnails, titles, permalinks, prices, and SKU variation images
        item_ids_to_query = {
            s.item_id for s in sessions
            if s.item_id and (
                not s.item_thumbnail
                or is_fallback_title(s.item_title)
                or not getattr(s, "_sku_thumb_resolved", False)
            )
        }
        if item_ids_to_query:
            id_list = list(item_ids_to_query)
            for i in range(0, len(id_list), 20):
                chunk = id_list[i:i + 20]
                try:
                    items_data = self.client.get_items_batch(aid, chunk)
                    for item in items_data:
                        iid = str(item.get("id") or "")
                        for s in sessions:
                            if s.item_id == iid:
                                sku_thumb = extract_sku_picture_url(item, s.variation_id, s.sku)
                                if sku_thumb:
                                    s.item_thumbnail = sku_thumb
                                    setattr(s, "_sku_thumb_resolved", True)
                                    if not s.variation_id and item.get("variations"):
                                        for v in item.get("variations", []):
                                            if isinstance(v, dict):
                                                pic_ids = v.get("picture_ids") or []
                                                for p_id in pic_ids:
                                                    if p_id in sku_thumb:
                                                        s.variation_id = str(v.get("id") or "")
                                                        break
                                elif not s.item_thumbnail:
                                    thumb = item.get("secure_thumbnail") or item.get("thumbnail")
                                    if thumb:
                                        s.item_thumbnail = str(thumb)

                                if is_fallback_title(s.item_title):
                                    s.item_title = str(item.get("title") or s.item_title)
                                if not s.item_permalink and item.get("permalink"):
                                    s.item_permalink = str(item.get("permalink"))
                                if not s.item_price and item.get("price"):
                                    cur = item.get("currency_id") or "$"
                                    s.item_price = f"{cur} {item.get('price')}"
                except Exception as err:
                    logger.debug("Failed batch item query for account %s: %s", aid, err)

        # Apply fallback title only for sessions that still lack an item title
        for sess in sessions:
            if is_fallback_title(sess.item_title):
                if sess.session_type == "message":
                    sess.item_title = f"订单 {sess.order_id} 售后私信" if sess.order_id else "买家售后私信"
                elif sess.session_type == "claim":
                    sess.item_title = f"订单 {sess.order_id} 纠纷索赔" if sess.order_id else "订单纠纷索赔"
                elif sess.item_id:
                    sess.item_title = f"商品 {sess.item_id}"
                else:
                    sess.item_title = "美客多会话商品"

    def _fetch_single_account(self, aid: str) -> list[ServiceSession]:
        acc_name = resolve_store_name(self.auth_mgr, aid)
        sites = self.auth_mgr.list_sites(aid)
        acc_sessions: list[ServiceSession] = []

        if self.session_type in ("all", "question"):
            q_sessions = self._fetch_questions_for_account(aid, acc_name)
            if q_sessions:
                acc_sessions.extend(q_sessions)
                self.sessions_chunk.emit(list(q_sessions))

        if self.session_type in ("all", "claim"):
            claim_sessions = self._fetch_claims_for_account(aid, acc_name, sites)
            if claim_sessions:
                acc_sessions.extend(claim_sessions)
                self.sessions_chunk.emit(list(claim_sessions))

        if self.session_type in ("all", "message"):
            msg_sessions = self._fetch_messages_for_account(aid, acc_name, sites)
            if msg_sessions:
                acc_sessions.extend(msg_sessions)
                self.sessions_chunk.emit(list(msg_sessions))

        self._enrich_account_sessions(aid, acc_sessions)
        if acc_sessions:
            self.sessions_chunk.emit(list(acc_sessions))

        return acc_sessions

    def run(self) -> None:
        all_sessions: list[ServiceSession] = []
        try:
            if not self.account_ids:
                self.finished.emit([])
                return

            if len(self.account_ids) == 1:
                all_sessions = self._fetch_single_account(self.account_ids[0])
            else:
                max_acc_workers = min(len(self.account_ids), 4)
                try:
                    with ThreadPoolExecutor(max_workers=max_acc_workers) as acc_executor:
                        futures = [acc_executor.submit(self._fetch_single_account, aid) for aid in self.account_ids]
                        for f in as_completed(futures):
                            if self._is_stopped:
                                break
                            try:
                                acc_res = f.result()
                                all_sessions.extend(acc_res)
                            except Exception as acc_err:
                                logger.debug("Error fetching account in parallel: %s", acc_err)
                except RuntimeError as re:
                    logger.debug("ThreadPoolExecutor shutdown: %s", re)

            # Deduplicate by (account_id, session_id)
            seen_keys: set[tuple[str, str]] = set()
            deduped_sessions: list[ServiceSession] = []
            for s in all_sessions:
                key = (s.account_id, s.id)
                if key not in seen_keys:
                    seen_keys.add(key)
                    deduped_sessions.append(s)

            self.finished.emit(deduped_sessions)
        except Exception as err:
            logger.error("Error in FetchSessionsWorker: %s", err)
            self.error.emit(str(err))



class TranslateWorker(SafeWorkerBase):
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, text: str, target_lang: str):
        super().__init__()
        self.text = text
        self.target_lang = target_lang

    def run(self) -> None:
        try:
            res = translate_text(self.text, target_lang=self.target_lang)
            self.finished.emit(res)
        except Exception as err:
            self.error.emit(str(err))


class SendReplyWorker(SafeWorkerBase):
    finished = Signal(bool, str)

    def __init__(self, client: MercadoClient, session: ServiceSession, reply_text: str, image_paths: list[str] | None = None):
        super().__init__()
        self.client = client
        self.session = session
        self.reply_text = reply_text
        self.image_paths = list(image_paths or [])

    def run(self) -> None:
        try:
            attachment_names = []
            if self.image_paths:
                for img_p in self.image_paths:
                    if not os.path.exists(img_p):
                        continue
                    with open(img_p, "rb") as f:
                        f_bytes = f.read()
                    f_name = os.path.basename(img_p)
                    # Detect mime
                    mime = "image/png" if f_name.lower().endswith(".png") else "image/jpeg"
                    if self.session.session_type == "claim":
                        raw_cid = self.session.id.replace("c_", "")
                        res = self.client.upload_claim_attachment(self.session.account_id, raw_cid, f_bytes, f_name, mime_type=mime)
                        att_name = res.get("filename") or res.get("id") or f_name
                        if att_name:
                            attachment_names.append(att_name)
                    elif self.session.session_type == "message":
                        res = self.client.upload_pack_attachment(self.session.account_id, f_bytes, f_name, mime_type=mime, site_id=self.session.site_id)
                        att_name = res.get("filename") or res.get("id") or f_name
                        if att_name:
                            attachment_names.append(att_name)

            if self.session.session_type == "question":
                raw_qid = self.session.id.replace("q_", "")
                self.client.answer_question(self.session.account_id, raw_qid, self.reply_text)
                self.finished.emit(True, "售前咨询回复已成功提交！")
            elif self.session.session_type == "claim":
                raw_cid = self.session.id.replace("c_", "")
                is_dispute = (self.session.stage == "dispute") or any(
                    m.sender_role == "mediator" for m in (self.session.messages or [])
                )
                receiver_role = "mediator" if is_dispute else "complainant"
                self.client.send_claim_message(
                    self.session.account_id,
                    raw_cid,
                    self.reply_text,
                    receiver_role=receiver_role,
                    attachments=attachment_names,
                )
                self.finished.emit(True, "订单索赔调解留言已成功发送！")
            elif self.session.session_type == "message":
                self.client.send_pack_message(self.session.account_id, self.session.pack_id, self.reply_text, attachments=attachment_names)
                self.finished.emit(True, "买家售后私信已成功发出！")
            else:
                self.finished.emit(False, "未知会话类型")
        except Exception as err:
            self.finished.emit(False, str(err))


class ClaimActionWorker(SafeWorkerBase):
    finished = Signal(str, str, dict)  # action, claim_id, result
    error = Signal(str, str, str)  # action, claim_id, error_message

    def __init__(
        self,
        client: MercadoClient,
        account_id: str,
        claim_id: str,
        action: str,  # "open-dispute", "partial-refund", "full-refund"
        extra_params: dict[str, Any] | None = None,
    ):
        super().__init__()
        self.client = client
        self.account_id = account_id
        self.claim_id = str(claim_id).replace("c_", "").strip()
        self.action = action
        self.extra_params = extra_params or {}

    def run(self) -> None:
        try:
            if self.action == "open-dispute":
                res = self.client.request_claim_mediation(self.account_id, self.claim_id)
            elif self.action == "partial-refund":
                pct = self.extra_params.get("percentage")
                amt = self.extra_params.get("amount")
                res = self.client.submit_claim_partial_refund(self.account_id, self.claim_id, percentage=pct, amount=amt)
            elif self.action == "full-refund":
                res = self.client.submit_claim_full_refund(self.account_id, self.claim_id)
            else:
                raise ValueError(f"Unknown claim action: {self.action}")
            self.finished.emit(self.action, self.claim_id, res if isinstance(res, dict) else {})
        except Exception as err:
            logger.warning("Claim action %s failed for %s: %s", self.action, self.claim_id, err)
            self.error.emit(self.action, self.claim_id, str(err))


class PartialRefundDialog(QDialog):
    """Dialog for offering partial refund to buyer with platform recommendations."""

    def __init__(
        self,
        client: MercadoClient,
        session: ServiceSession,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.client = client
        self.session = session
        self.selected_percentage = 30.0
        self.setWindowTitle("发起部分退款提议")
        self.setFixedSize(460, 360)
        self.setStyleSheet("""
            QDialog {
                background: #141816;
                color: #F6F3EA;
            }
            QLabel {
                color: #E6E2D8;
                font-size: 13px;
            }
            QPushButton {
                border-radius: 6px;
                padding: 6px 14px;
                font-weight: 600;
                font-size: 13px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title_lbl = QLabel(f"<b>为订单 {session.order_id} 申请部分退款</b>")
        title_lbl.setStyleSheet("font-size: 15px; color: #F6F3EA; font-weight: bold;")
        layout.addWidget(title_lbl)

        info_box = QFrame()
        info_box.setObjectName("refundInfoBox")
        info_box.setStyleSheet("""
            QFrame#refundInfoBox {
                background: #1C231F;
                border: 1px solid #4E472F;
                border-radius: 8px;
            }
            QFrame#refundInfoBox QLabel {
                border: none;
                background: transparent;
                color: #E6E2D8;
                font-size: 13px;
                padding: 1px 0px;
            }
        """)
        ib_layout = QVBoxLayout(info_box)
        ib_layout.setContentsMargins(14, 10, 14, 10)
        ib_layout.setSpacing(6)

        cur_price = session.item_price or "未知"
        ib_layout.addWidget(QLabel(f"店铺: <b>{session.account_name}</b> · 站点: <b>{session.site_id}</b>"))
        ib_layout.addWidget(QLabel(f"买家昵称: <b>{session.buyer_nickname}</b>"))
        ib_layout.addWidget(QLabel(f"商品金额: <b style='color: #FBBF24;'>{cur_price}</b>"))
        layout.addWidget(info_box)

        pct_lbl = QLabel("选择退款比例:")
        layout.addWidget(pct_lbl)

        btn_row = QHBoxLayout()
        self.pct_group = QButtonGroup(self)

        self.pct_buttons = []
        for p in (10, 20, 30, 50):
            btn = QPushButton(f"{p}%")
            btn.setCheckable(True)
            btn.setFixedHeight(34)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #202820;
                    color: #C8C3B7;
                    border: 1px solid #4E472F;
                }
                QPushButton:checked {
                    background: #2F6B3F;
                    color: #FFFFFF;
                    border: 1px solid #3E7B4B;
                }
            """)
            if p == 30:
                btn.setChecked(True)
            self.pct_group.addButton(btn, p)
            btn_row.addWidget(btn)
            self.pct_buttons.append(btn)

        layout.addLayout(btn_row)

        custom_row = QHBoxLayout()
        custom_lbl = QLabel("或自定义百分比:")
        self.spin = QSpinBox()
        self.spin.setRange(1, 90)
        self.spin.setValue(30)
        self.spin.setSuffix("%")
        self.spin.setFixedHeight(32)
        self.spin.setFixedWidth(100)
        self.spin.setStyleSheet("background: #1C231F; color: #FFFFFF; border: 1px solid #4E472F; border-radius: 4px; padding: 2px 6px;")

        def _on_btn_clicked(b):
            self.spin.setValue(self.pct_group.id(b))
        self.pct_group.buttonClicked.connect(_on_btn_clicked)

        def _on_spin_changed(val):
            for b in self.pct_buttons:
                if self.pct_group.id(b) == val:
                    b.setChecked(True)
                    break
                else:
                    b.setChecked(False)
        self.spin.valueChanged.connect(_on_spin_changed)

        custom_row.addWidget(custom_lbl)
        custom_row.addWidget(self.spin)
        custom_row.addStretch()
        layout.addLayout(custom_row)

        tip_lbl = QLabel("💡 提示：买家接受部分退款提议后索赔将自动关闭，不计入负面指标并退还对应比例佣金。")
        tip_lbl.setStyleSheet("color: #8C867A; font-size: 11px;")
        tip_lbl.setWordWrap(True)
        layout.addWidget(tip_lbl)

        layout.addStretch()

        actions_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("background: #2A332D; color: #C8C3B7; border: 1px solid #4E472F;")
        cancel_btn.clicked.connect(self.reject)
        actions_row.addWidget(cancel_btn)

        confirm_btn = QPushButton("确认发起提议")
        confirm_btn.setStyleSheet(f"background: {COLORS['green']}; color: #FFFFFF; border: 1px solid #3E7B4B; font-weight: bold;")
        confirm_btn.clicked.connect(self._on_confirm)
        actions_row.addWidget(confirm_btn)

        layout.addLayout(actions_row)

    def _on_confirm(self) -> None:
        self.selected_percentage = float(self.spin.value())
        self.accept()


class ReplyTextEdit(QTextEdit):
    """Text edit with smart clipboard image paste support."""
    image_pasted = Signal(str)

    def canInsertFromMimeData(self, source) -> bool:
        if source and source.hasImage():
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source) -> None:
        if source and source.hasImage():
            img = source.imageData()
            if img:
                import tempfile
                tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="meli_clip_")
                os.close(tmp_fd)
                pix = QPixmap.fromImage(img) if isinstance(img, QImage) else QPixmap(img)
                if pix.save(tmp_path, "PNG"):
                    self.image_pasted.emit(tmp_path)
                    return
        super().insertFromMimeData(source)


# ----------------------------------------------------------------------
# Main Service Center Page Widget
# ----------------------------------------------------------------------

class ServiceCenterPage(QFrame):
    """Refined Customer Service & After-sales Center Workbench."""

    unreadCountChanged = Signal(int)
    oauthExpired = Signal(str, str)

    def __init__(self, client: MercadoClient, auth_mgr: AuthManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.client = client
        self.auth = auth_mgr
        self.template_mgr = TemplateManager()

        self._all_sessions: list[ServiceSession] = load_sessions_snapshot()
        self._current_session: ServiceSession | None = None
        self._qsettings = QSettings("MercadoDiscountManager", "CustomerService")
        self._sound_enabled: bool = bool(self._qsettings.value("sound_enabled", True, type=bool))
        self._known_session_ids: set[str] = {s.id for s in self._all_sessions}
        self._known_unread_ids: set[str] = {s.id for s in self._all_sessions if s.is_unread}
        self._recent_alert_ids: dict[str, float] = {}
        self._is_silent_reload: bool = False

        self._active_store_filter: str = "all"
        self._active_site_filter: str = "all"
        self._active_status_filter: str = "all"
        self._active_tab: str = "question"  # "question" | "claim" | "message"
        self._active_msg_workers: set[FetchConversationMessagesWorker] = set()
        self._active_claim_action_workers: set[ClaimActionWorker] = set()
        self._displayed_session_id: str | None = None
        self._displayed_messages_count: int = 0

        self._init_ui()

        # Periodic auto-poll fallback timer (every 30 seconds)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(30000)
        self._poll_timer.timeout.connect(self._on_auto_poll_timeout)
        self._poll_timer.start()

        if self._all_sessions:
            self._update_tab_counts()
            self._apply_filter(preserve_selection=False)

    def _init_ui(self) -> None:
        self.setObjectName("serviceCenterPage")
        self.setStyleSheet("QWidget#serviceCenterPage { background: transparent; border: none; }")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(6)

        # 1. Top Integrated Control Bar
        top_bar = self._build_top_bar()
        main_layout.addLayout(top_bar)

        # 2. Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(3)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("QProgressBar { background: rgba(47, 107, 63, 0.2); border: none; } QProgressBar::chunk { background: #559E67; }")
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

        # 3. Content Splitter (Left: Session List | Right: Chat Stream & Reply Dock)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(8)
        self.splitter.setStyleSheet("""
            QSplitter::handle:horizontal {
                background-color: transparent;
            }
            QSplitter::handle:horizontal:hover {
                background-color: rgba(78, 71, 47, 0.3);
                border-radius: 4px;
            }
        """)

        left_widget = self._build_left_panel()
        right_widget = self._build_right_panel()

        # Strict width boundaries to keep card typography, icons and badges spacious
        left_widget.setMinimumWidth(380)
        right_widget.setMinimumWidth(480)

        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(right_widget)
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setSizes([410, 790])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        main_layout.addWidget(self.splitter, 1)
        self._display_session_detail(None)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Ensure left session panel maintains a comfortable width when switching to this page
        sizes = self.splitter.sizes()
        if not sizes or sizes[0] < 380:
            total_w = self.width() if self.width() > 800 else 1200
            left_w = 410
            right_w = max(480, total_w - left_w - self.splitter.handleWidth())
            self.splitter.setSizes([left_w, right_w])

    # ------------------------------------------------------------------
    # Top Control Bar
    # ------------------------------------------------------------------

    def _build_top_bar(self) -> QHBoxLayout:
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        # Title
        title_lbl = QLabel("客服与售后")
        title_lbl.setObjectName("sectionTitle")
        top.addWidget(title_lbl)

        top.addSpacing(4)

        # Store Filter Dropdown
        top.addWidget(QLabel("选择店铺:"))
        self.store_combo = QComboBox()
        self.store_combo.setFixedHeight(32)
        self.store_combo.setFixedWidth(190)
        populate_store_combo(self.store_combo, auth_mgr=self.auth, all_label="全部店铺", all_value="all")
        self.store_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.store_combo)

        top.addSpacing(4)

        # Site Filter Dropdown
        top.addWidget(QLabel("选择站点:"))
        self.site_combo = QComboBox()
        self.site_combo.setFixedHeight(32)
        self.site_combo.setFixedWidth(140)
        self.site_combo.addItem("全部站点", "all")
        self.site_combo.addItem("🇲🇽 墨西哥 (MLM)", "MLM")
        self.site_combo.addItem("🇧🇷 巴西 (MLB)", "MLB")
        self.site_combo.addItem("🇨🇱 智利 (MLC)", "MLC")
        self.site_combo.addItem("🇨🇴 哥伦比亚 (MCO)", "MCO")
        self.site_combo.addItem("🇦🇷 阿根廷 (MLA)", "MLA")
        self.site_combo.addItem("🇺🇾 乌拉圭 (MLU)", "MLU")
        self.site_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.site_combo)

        top.addSpacing(4)

        # Status Filter Dropdown
        top.addWidget(QLabel("状态筛选:"))
        self.status_combo = QComboBox()
        self.status_combo.setFixedHeight(32)
        self.status_combo.setFixedWidth(130)
        self.status_combo.addItem("全部状态", "all")
        self.status_combo.addItem("开启中/待处理", "opened")
        self.status_combo.addItem("已结案/历史", "closed")
        self.status_combo.currentIndexChanged.connect(self._on_filter_changed)
        top.addWidget(self.status_combo)

        top.addSpacing(4)

        # Keyword search
        self.search_edit = QLineEdit()
        self.search_edit.setFixedHeight(32)
        self.search_edit.setPlaceholderText("搜索买家昵称 / 商品 ID / 订单号...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.search_edit.setFixedWidth(230)
        top.addWidget(self.search_edit)

        top.addStretch(1)

        # Sound toggle button
        self.sound_btn = QPushButton("🔔 提示音: 开" if self._sound_enabled else "🔕 提示音: 关")
        self.sound_btn.setFixedHeight(32)
        self.sound_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sound_btn.clicked.connect(self._toggle_sound)
        top.addWidget(self.sound_btn)

        return top

    # ------------------------------------------------------------------
    # Left Panel: Segmented Tabs & Session List
    # ------------------------------------------------------------------

    def _build_left_panel(self) -> QWidget:
        container = QFrame()
        container.setObjectName("serviceLeftPanel")
        container.setMinimumWidth(380)
        container.setStyleSheet("""
            QFrame#serviceLeftPanel {
                background: #171D1A;
                border: 1px solid #4E472F;
                border-radius: 8px;
            }
        """)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Tab Segmented Header
        tab_box = QHBoxLayout()
        tab_box.setContentsMargins(0, 0, 0, 0)
        tab_box.setSpacing(4)

        self.tab_btn_questions = QPushButton("售前咨询")
        self.tab_btn_claims = QPushButton("订单索赔")
        self.tab_btn_messages = QPushButton("售后私信")

        for btn, tab_id in [
            (self.tab_btn_questions, "question"),
            (self.tab_btn_claims, "claim"),
            (self.tab_btn_messages, "message"),
        ]:
            btn.setCheckable(True)
            btn.setFixedHeight(30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: #1C231F;
                    border: 1px solid #4E472F;
                    border-radius: 6px;
                    padding: 4px 6px;
                    font-size: 11px;
                    font-weight: 600;
                    color: #C8C3B7;
                }
                QPushButton:checked {
                    background: #203A2B;
                    border: 1px solid #3E7B4B;
                    font-weight: bold;
                    color: #FFFFFF;
                }
                QPushButton:hover:!checked {
                    background: #242E27;
                    border-color: #6E5D2A;
                    color: #F6F3EA;
                }
            """)
            btn.clicked.connect(lambda checked, tid=tab_id: self._switch_tab(tid))
            tab_box.addWidget(btn, 1)

        self.tab_btn_questions.setChecked(True)
        layout.addLayout(tab_box)

        # Session List
        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionListWidget")
        self.session_list.setStyleSheet("""
            QListWidget#sessionListWidget {
                background: transparent;
                border: none;
                padding: 0px;
            }
            QListWidget#sessionListWidget::item {
                background: #171D1A;
                border: 1px solid #28332B;
                border-radius: 8px;
                padding: 0px;
                margin: 3px 6px;
            }
            QListWidget#sessionListWidget::item:hover {
                background: #1C2420;
                border-color: #38463B;
            }
            QListWidget#sessionListWidget::item:selected {
                background: #203A2B;
                border: 1px solid #3E7B4B;
                border-radius: 8px;
            }
            QListWidget#sessionListWidget QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QListWidget#sessionListWidget QScrollBar::handle:vertical {
                background: #4E472F;
                min-height: 20px;
                border-radius: 3px;
            }
            QListWidget#sessionListWidget QScrollBar::handle:vertical:hover {
                background: #6E5D2A;
            }
            QListWidget#sessionListWidget QScrollBar::add-line:vertical,
            QListWidget#sessionListWidget QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QListWidget#sessionListWidget QScrollBar::add-page:vertical,
            QListWidget#sessionListWidget QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)
        self.session_list.currentItemChanged.connect(self._on_session_selected)
        self.session_list.itemClicked.connect(self._on_session_clicked)
        self.session_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.session_list, 1)

        # Bottom symmetry bar: Left [刷新], Right [共 X 条提问/索赔/私信]
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(4, 2, 4, 2)
        bottom_bar.setSpacing(8)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedHeight(28)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background: #1C231F;
                border: 1px solid #4E472F;
                border-radius: 4px;
                padding: 4px 14px;
                font-size: 11px;
                font-weight: 500;
                color: #C8C3B7;
            }
            QPushButton:hover {
                background: #242E27;
                border-color: #6E5D2A;
                color: #F6F3EA;
            }
            QPushButton:disabled {
                background: #222623;
                color: #666666;
                border-color: #333333;
            }
        """)
        self.refresh_btn.clicked.connect(self.reload_sessions)
        bottom_bar.addWidget(self.refresh_btn)

        bottom_bar.addStretch(1)

        self.list_summary_lbl = QLabel("共 0 条提问")
        self.list_summary_lbl.setStyleSheet("color: #8C867A; font-size: 11px;")
        bottom_bar.addWidget(self.list_summary_lbl)

        layout.addLayout(bottom_bar)

        return container


    # ------------------------------------------------------------------
    # Right Panel: Context Summary Card & Conversation Workbench
    # ------------------------------------------------------------------

    def _build_right_panel(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 1. Product & Context Summary Card
        self.context_card = QFrame()
        self.context_card.setObjectName("productContextCard")
        self.context_card.setStyleSheet("""
            QFrame#productContextCard {
                background: #1C231F;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 6px 12px;
            }
            QFrame#productContextCard QLabel {
                border: none;
                background: transparent;
                padding: 0;
            }
        """)
        ctx_layout = QHBoxLayout(self.context_card)
        ctx_layout.setContentsMargins(8, 6, 8, 6)
        ctx_layout.setSpacing(12)

        # 1.1 Product Thumbnail (56x56)
        self.context_thumb_lbl = QLabel()
        self.context_thumb_lbl.setFixedSize(56, 56)
        self.context_thumb_lbl.setStyleSheet("background: #171D1A; border: 1px solid #4E472F; border-radius: 6px;")
        self.context_thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.context_thumb_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.context_thumb_lbl.setToolTip("点击放大查看商品大图")
        self.context_thumb_lbl.mousePressEvent = self._on_context_thumb_clicked
        ctx_layout.addWidget(self.context_thumb_lbl)

        # 1.2 Information Body
        info_col = QVBoxLayout()
        info_col.setSpacing(3)
        info_col.setContentsMargins(0, 0, 0, 0)

        self.context_title_lbl = QLabel("暂无选中的会话")
        self.context_title_lbl.setStyleSheet("color: #F6F3EA; font-size: 13px; font-weight: bold;")
        self.context_title_lbl.setWordWrap(True)
        self.context_title_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context_title_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.context_title_lbl.customContextMenuRequested.connect(
            lambda pos, w=self.context_title_lbl: self._show_simple_copy_menu(pos, w)
        )
        info_col.addWidget(self.context_title_lbl)

        self.context_meta_lbl = QLabel("请在左侧列表中点击任意提问或索赔会话")
        self.context_meta_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.context_meta_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.context_meta_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.context_meta_lbl.customContextMenuRequested.connect(
            lambda pos, w=self.context_meta_lbl: self._show_simple_copy_menu(pos, w)
        )
        info_col.addWidget(self.context_meta_lbl)
        self.context_info_lbl = self.context_meta_lbl  # Alias for backward compatibility

        # 1.2.3 Independent Metadata Chips Row
        self.tags_row_widget = QWidget()
        self.tags_row_layout = QHBoxLayout(self.tags_row_widget)
        self.tags_row_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_row_layout.setSpacing(0)

        # 1) Item Link Chip (Left-Click opens in browser, Right-Click directly copies pure item ID)
        self.chip_item_lbl = QLabel("")
        self.chip_item_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.chip_item_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chip_item_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chip_item_lbl.customContextMenuRequested.connect(
            lambda pos: self._show_target_copy_menu(pos, self.chip_item_lbl, lambda: self._current_session.item_id if self._current_session else "")
        )
        self.chip_item_lbl.mousePressEvent = self._on_chip_item_clicked
        self.tags_row_layout.addWidget(self.chip_item_lbl)

        # Sep 1: between item and order
        self.sep_item_order = QLabel(" · ")
        self.sep_item_order.setStyleSheet("color: #78716C; font-size: 11px;")
        self.tags_row_layout.addWidget(self.sep_item_order)

        # 2) Order ID Chip (Click or Right-Click directly copies pure order ID number)
        self.chip_order_lbl = QLabel("")
        self.chip_order_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.chip_order_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chip_order_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chip_order_lbl.customContextMenuRequested.connect(
            lambda pos: self._show_target_copy_menu(pos, self.chip_order_lbl, lambda: self._current_session.order_id if self._current_session else "")
        )
        self.chip_order_lbl.mousePressEvent = self._make_chip_click_copy_handler(
            self.chip_order_lbl, lambda: self._current_session.order_id if self._current_session else ""
        )
        self.tags_row_layout.addWidget(self.chip_order_lbl)

        # Sep 2: between order and pack
        self.sep_order_pack = QLabel(" · ")
        self.sep_order_pack.setStyleSheet("color: #78716C; font-size: 11px;")
        self.tags_row_layout.addWidget(self.sep_order_pack)

        # 3) Pack ID Chip (Click or Right-Click directly copies pure pack ID)
        self.chip_pack_lbl = QLabel("")
        self.chip_pack_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.chip_pack_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.chip_pack_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chip_pack_lbl.customContextMenuRequested.connect(
            lambda pos: self._show_target_copy_menu(pos, self.chip_pack_lbl, lambda: self._current_session.pack_id if self._current_session else "")
        )
        self.chip_pack_lbl.mousePressEvent = self._make_chip_click_copy_handler(
            self.chip_pack_lbl, lambda: self._current_session.pack_id if self._current_session else ""
        )
        self.tags_row_layout.addWidget(self.chip_pack_lbl)

        # Sep 3: between pack and stage
        self.sep_pack_stage = QLabel(" · ")
        self.sep_pack_stage.setStyleSheet("color: #78716C; font-size: 11px;")
        self.tags_row_layout.addWidget(self.sep_pack_stage)

        # 4) Claim Stage Badge Chip
        self.chip_stage_lbl = QLabel("")
        self.chip_stage_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.chip_stage_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chip_stage_lbl.customContextMenuRequested.connect(
            lambda pos: self._show_target_copy_menu(
                pos, self.chip_stage_lbl,
                lambda: CLAIM_STAGE_NAMES.get(self._current_session.stage.lower(), self._current_session.stage) if (self._current_session and self._current_session.stage) else ""
            )
        )
        self.tags_row_layout.addWidget(self.chip_stage_lbl)

        # Sep 4: between stage and reputation impact
        self.sep_stage_rep = QLabel(" · ")
        self.sep_stage_rep.setStyleSheet("color: #78716C; font-size: 11px;")
        self.tags_row_layout.addWidget(self.sep_stage_rep)

        # 5) Claim Affects Reputation Chip
        self.chip_rep_lbl = QLabel("")
        self.chip_rep_lbl.setStyleSheet("color: #A8A29E; font-size: 11px;")
        self.tags_row_layout.addWidget(self.chip_rep_lbl)

        self.tags_row_layout.addStretch(1)
        info_col.addWidget(self.tags_row_widget)

        # Hidden context_link_lbl kept for backward compatibility with existing tests
        self.context_link_lbl = QLabel("")
        self.context_link_lbl.setOpenExternalLinks(True)
        self.context_link_lbl.hide()
        info_col.addWidget(self.context_link_lbl)

        self.context_card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.context_card.customContextMenuRequested.connect(
            lambda pos, w=self.context_card: self._show_simple_copy_menu(pos, w)
        )

        ctx_layout.addLayout(info_col, 1)

        # 1.3 Claim Action Toolbar (Dispute, Partial Refund, Full Refund - moved to top right)
        self.claim_actions_frame = QFrame()
        self.claim_actions_frame.setObjectName("claimActionsFrame")
        self.claim_actions_frame.setStyleSheet("""
            QFrame#claimActionsFrame {
                background: transparent;
                border: none;
            }
            QFrame#claimActionsFrame QLabel {
                border: none;
                background: transparent;
                padding: 0;
            }
        """)
        ca_layout = QHBoxLayout(self.claim_actions_frame)
        ca_layout.setContentsMargins(0, 0, 0, 0)
        ca_layout.setSpacing(8)

        self.claim_stage_lbl = QLabel("")
        self.claim_stage_lbl.hide()

        # 按钮1: 请求调解
        self.claim_dispute_btn = QPushButton("请求调解")
        self.claim_dispute_btn.setFixedHeight(30)
        self.claim_dispute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.claim_dispute_btn.setToolTip("向美客多官方申请人工介入调解仲裁")
        self.claim_dispute_btn.setStyleSheet("""
            QPushButton {
                background: #3B331E;
                color: #FDE047;
                border: 1px solid #854D0E;
                border-radius: 6px;
                padding: 4px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #544423;
                border-color: #EAB308;
            }
            QPushButton:disabled {
                background: #23221C;
                color: #716854;
                border-color: #38352A;
            }
        """)
        self.claim_dispute_btn.clicked.connect(self._on_request_mediation)
        ca_layout.addWidget(self.claim_dispute_btn)

        # 按钮2: 部分退款
        self.claim_partial_refund_btn = QPushButton("部分退款")
        self.claim_partial_refund_btn.setFixedHeight(30)
        self.claim_partial_refund_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.claim_partial_refund_btn.setToolTip("向买家发起保留商品的百分比部分退款提议")
        self.claim_partial_refund_btn.setStyleSheet("""
            QPushButton {
                background: #163832;
                color: #5EEAD4;
                border: 1px solid #0F766E;
                border-radius: 6px;
                padding: 4px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #1C4D44;
                border-color: #14B8A6;
            }
            QPushButton:disabled {
                background: #1A2421;
                color: #4D6B63;
                border-color: #273934;
            }
        """)
        self.claim_partial_refund_btn.clicked.connect(self._on_partial_refund)
        ca_layout.addWidget(self.claim_partial_refund_btn)

        # 按钮3: 全额退款
        self.claim_full_refund_btn = QPushButton("全额退款")
        self.claim_full_refund_btn.setFixedHeight(30)
        self.claim_full_refund_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.claim_full_refund_btn.setToolTip("直接退款给买家并关闭该笔索赔争议")
        self.claim_full_refund_btn.setStyleSheet("""
            QPushButton {
                background: #451E1E;
                color: #FCA5A5;
                border: 1px solid #991B1B;
                border-radius: 6px;
                padding: 4px 14px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #5E2626;
                border-color: #EF4444;
            }
            QPushButton:disabled {
                background: #281F1F;
                color: #735151;
                border-color: #3F2C2C;
            }
        """)
        self.claim_full_refund_btn.clicked.connect(self._on_full_refund)
        ca_layout.addWidget(self.claim_full_refund_btn)

        self.claim_actions_frame.hide()
        ctx_layout.addWidget(self.claim_actions_frame, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # 刷新消息按钮
        self.refresh_chat_btn = QPushButton("⟳ 刷新消息")
        self.refresh_chat_btn.setFixedHeight(30)
        self.refresh_chat_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_chat_btn.setToolTip("从美客多官方接口实时拉取本会话最新沟通记录与平台调解动态")
        self.refresh_chat_btn.setStyleSheet("""
            QPushButton {
                background: #232C24;
                color: #F6F3EA;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #2D3A30;
                border-color: #6E5D2A;
            }
            QPushButton:disabled {
                background: #181F1A;
                color: #57534E;
                border-color: #292E2A;
            }
        """)
        self.refresh_chat_btn.clicked.connect(self._on_refresh_chat_clicked)
        ctx_layout.addWidget(self.refresh_chat_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Legacy / hidden helper buttons preserved for test compatibility
        self.open_browser_btn = QPushButton("在默认浏览器打开 ↗")
        self.open_browser_btn.hide()
        self.open_browser_btn.clicked.connect(self._open_item_in_browser)
        self.toggle_read_btn = QPushButton("标为已读")
        self.toggle_read_btn.hide()
        self.toggle_read_btn.clicked.connect(self._toggle_current_session_read_status)

        layout.addWidget(self.context_card)

        # 2. Conversation Flow (Scroll Area)
        self.chat_scroll = QScrollArea()
        self.chat_scroll.setObjectName("chatScrollArea")
        self.chat_scroll.setWidgetResizable(True)
        self.chat_scroll.setMinimumHeight(80)
        self.chat_scroll.setStyleSheet("""
            QScrollArea#chatScrollArea {
                background: #171B19;
                border: 1px solid #4E472F;
                border-radius: 8px;
            }
        """)

        self.chat_container = QWidget()
        self.chat_container.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_container.customContextMenuRequested.connect(
            lambda pos: self._show_chat_context_menu(pos, self.chat_container, None)
        )
        self.chat_layout = QVBoxLayout(self.chat_container)
        self.chat_layout.setContentsMargins(16, 16, 16, 16)
        self.chat_layout.setSpacing(12)
        self.chat_layout.addStretch()

        self.chat_scroll.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_scroll.customContextMenuRequested.connect(
            lambda pos: self._show_chat_context_menu(pos, self.chat_scroll, None)
        )
        self.chat_scroll.setWidget(self.chat_container)
        layout.addWidget(self.chat_scroll, 1)

        # 3. Status warning / Blocking banner
        self.status_banner = QLabel("")
        self.status_banner.setWordWrap(True)
        self.status_banner.setStyleSheet("background: rgba(224, 108, 117, 0.15); border: 1px solid #e06c75; border-radius: 4px; padding: 6px 10px; color: #ff7b85; font-size: 11px;")
        self.status_banner.hide()
        layout.addWidget(self.status_banner)

        # 4. Reply Dock
        self.reply_dock = self._build_reply_dock()
        self.reply_dock.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.reply_dock)

        return container

    def _build_reply_dock(self) -> QFrame:
        dock = QFrame()
        dock.setObjectName("replyDockFrame")
        dock.setStyleSheet("""
            QFrame#replyDockFrame {
                background: #1C231F;
                border: 1px solid #4E472F;
                border-radius: 8px;
                padding: 8px 10px;
            }
            QFrame#replyDockFrame QLabel {
                border: none;
                background: transparent;
                padding: 0;
            }
        """)
        layout = QVBoxLayout(dock)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        # Quick action toolbar (top row): Upload image, hint, char counter
        tool_layout = QHBoxLayout()
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_layout.setSpacing(8)

        self.upload_img_btn = QPushButton("📷 上传图片")
        self.upload_img_btn.setFixedHeight(28)
        self.upload_img_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.upload_img_btn.setStyleSheet("""
            QPushButton {
                font-size: 11px;
                padding: 3px 10px;
                background: #202820;
                border: 1px solid #4E472F;
                border-radius: 4px;
                color: #C8C3B7;
            }
            QPushButton:hover {
                background: #2B382B;
                border-color: #A3E635;
                color: #FFFFFF;
            }
        """)
        self.upload_img_btn.setToolTip("选择图片凭证(JPG/PNG，最多3张)，或在输入框按Ctrl+V/Cmd+V粘贴截图")
        self.upload_img_btn.clicked.connect(self._on_choose_image_clicked)
        tool_layout.addWidget(self.upload_img_btn)

        self.img_count_lbl = QLabel("")
        self.img_count_lbl.setStyleSheet("color: #A3E635; font-size: 11px;")
        tool_layout.addWidget(self.img_count_lbl)

        tool_layout.addStretch(1)

        self.char_count_lbl = QLabel("0 / 500")
        self.char_count_lbl.setStyleSheet("color: #777266; font-size: 11px;")
        tool_layout.addWidget(self.char_count_lbl)
        layout.addLayout(tool_layout)

        # Image preview container (hidden by default)
        self.selected_images: list[str] = []
        self.preview_container = QWidget()
        self.preview_layout = QHBoxLayout(self.preview_container)
        self.preview_layout.setContentsMargins(0, 2, 0, 4)
        self.preview_layout.setSpacing(8)
        self.preview_container.hide()
        layout.addWidget(self.preview_container)

        # Single unified input box with smart paste flow
        self.reply_input = ReplyTextEdit()
        self.reply_input.image_pasted.connect(self._on_image_pasted_from_clipboard)
        self.reply_input.setPlaceholderText("输入中文后点击下方【一键翻译】，或直接输入西班牙语/葡萄牙语回复...")
        self.reply_input.setFixedHeight(72)
        self.reply_input.setStyleSheet("""
            QTextEdit {
                background: #171D1A;
                border: 1px solid #4E472F;
                border-radius: 6px;
                padding: 6px 10px;
                color: #F6F3EA;
                font-size: 13px;
                selection-background-color: #2F6B3F;
            }
            QTextEdit:focus {
                border-color: #A3E635;
            }
        """)
        self.reply_input.textChanged.connect(self._on_reply_text_changed)
        layout.addWidget(self.reply_input)

        # Aliases for backward compatibility with existing tests and scripts
        self.zh_input = self.reply_input
        self.target_input = self.reply_input

        # Bottom action row: Left [⚡ 一键翻译为西班牙语 (es)], Right [确认并发送回复 (Ctrl+Enter)]
        send_row = QHBoxLayout()
        send_row.setContentsMargins(0, 0, 0, 0)

        self.translate_action_btn = QPushButton("⚡ 一键翻译为西班牙语 (es)")
        self.translate_action_btn.setFixedHeight(32)
        self.translate_action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.translate_action_btn.setStyleSheet("""
            QPushButton {
                background: #203A2B;
                border: 1px solid #2F6B3F;
                border-radius: 5px;
                color: #A3E635;
                font-weight: bold;
                font-size: 12px;
                padding: 4px 14px;
            }
            QPushButton:hover {
                background: #2A4C38;
                border-color: #3E7B4B;
                color: #BEF264;
            }
            QPushButton:disabled {
                background: #222623;
                border-color: #333333;
                color: #666666;
            }
        """)
        self.translate_action_btn.clicked.connect(self._do_translate)
        send_row.addWidget(self.translate_action_btn)

        send_row.addStretch(1)

        self.send_btn = QPushButton("确认并发送回复 (Ctrl+Enter)")
        self.send_btn.setFixedHeight(32)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet("""
            QPushButton {
                background: #2F6B3F;
                color: #F6F3EA;
                font-weight: bold;
                border: 1px solid #4E472F;
                border-radius: 5px;
                padding: 6px 18px;
                font-size: 12px;
            }
            QPushButton:hover {
                background: #356F45;
                border-color: #6D5B2A;
            }
            QPushButton:disabled {
                background: #2a332d;
                color: #777266;
                border-color: #333;
            }
        """)
        self.send_btn.clicked.connect(self._on_send_clicked)
        send_row.addWidget(self.send_btn)

        layout.addLayout(send_row)

        # Shortcut Ctrl+Enter / Cmd+Enter
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self._on_send_clicked)

        return dock

    # ------------------------------------------------------------------
    # Data Loading & Filtering
    # ------------------------------------------------------------------

    def reload_sessions(self, silent: bool = False) -> None:
        """Fetch all sessions asynchronously across accounts with progressive streaming."""
        if getattr(self, "worker", None) is not None and self.worker.isRunning():
            logger.info("FetchSessionsWorker is already running, skipping reload.")
            return

        self._is_silent_reload = silent
        if not silent:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.show()
            self.refresh_btn.setEnabled(False)

        try:
            accounts = self.auth.list_accounts() if getattr(self, "auth", None) else []
        except Exception as e:
            logger.warning("Failed to list accounts in reload_sessions: %s", e)
            accounts = []

        if self._active_store_filter != "all":
            aids = [self._active_store_filter]
        else:
            aids = [a["account_id"] for a in accounts if isinstance(a, dict) and "account_id" in a]

        self.worker = FetchSessionsWorker(
            self.client,
            self.auth,
            aids,
            session_type="all",
            cached_sessions=self._all_sessions,
        )
        self.worker.sessions_chunk.connect(self._on_sessions_chunk)
        self.worker.finished.connect(self._on_sessions_loaded)
        self.worker.error.connect(self._on_sessions_error)
        self.worker.start()

    def _on_auto_poll_timeout(self) -> None:
        """Periodic background poll to ensure real-time updates even without webhook push."""
        try:
            if getattr(self, "worker", None) is not None and self.worker.isRunning():
                return
            self.reload_sessions(silent=True)
        except Exception as e:
            logger.debug("Auto poll timeout error: %s", e)

    def _on_sessions_chunk(self, chunk: list[ServiceSession]) -> None:
        if not chunk:
            return
        existing_ids = {s.id for s in self._all_sessions}
        for s in chunk:
            if s.is_unread and (s.id not in self._known_unread_ids or s.id not in existing_ids):
                self._known_unread_ids.add(s.id)
                self._alert_new_session(s)
            if s.id not in existing_ids:
                existing_ids.add(s.id)
                self._known_session_ids.add(s.id)
                self._all_sessions.append(s)
            else:
                for idx, old_s in enumerate(self._all_sessions):
                    if old_s.id == s.id:
                        if s.item_thumbnail and s.item_thumbnail != old_s.item_thumbnail:
                            old_s.item_thumbnail = s.item_thumbnail
                        if s.variation_id and s.variation_id != old_s.variation_id:
                            old_s.variation_id = s.variation_id
                        if is_fallback_title(old_s.item_title) and not is_fallback_title(s.item_title):
                            old_s.item_title = s.item_title
                        elif old_s.item_title.startswith("商品 ") and not s.item_title.startswith("商品 "):
                            old_s.item_title = s.item_title
                        if not old_s.item_price and s.item_price:
                            old_s.item_price = s.item_price
                        if not old_s.item_permalink and s.item_permalink:
                            old_s.item_permalink = s.item_permalink
                        if s.sku:
                            old_s.sku = s.sku
                        if s.status != old_s.status:
                            old_s.status = s.status
                            old_s.can_reply = s.can_reply
                        if s.is_unread != old_s.is_unread:
                            old_s.is_unread = s.is_unread
                        if len(s.messages) > len(old_s.messages):
                            old_s.messages = s.messages
                        elif old_s.messages and not s.messages:
                            s.messages = old_s.messages
                        if self._current_session and self._current_session.id == s.id:
                            self._current_session = old_s
                            if s.item_thumbnail:
                                load_image_async(
                                    s.item_thumbnail,
                                    QSize(56, 56),
                                    self.context_thumb_lbl.setPixmap,
                                    radius=6,
                                )
                            if s.item_title:
                                self.context_title_lbl.setText(s.item_title)
                            meta_parts = [
                                f"{old_s.site_display}",
                                f"店铺: <b>{old_s.account_name}</b>",
                                f"买家: <b>{old_s.buyer_nickname}</b>",
                            ]
                            if old_s.item_price:
                                meta_parts.append(f"价格: <b>{old_s.item_price}</b>")
                            if old_s.sku:
                                meta_parts.append(f"SKU: <code>{old_s.sku}</code>")
                            self.context_meta_lbl.setText(" · ".join(meta_parts))
                            if s.item_url:
                                self.open_browser_btn.setEnabled(True)
                                self.open_browser_btn.setText("在默认浏览器打开 ↗")
                        break
        self._all_sessions.sort(key=lambda s: parse_iso_timestamp(s.created_at), reverse=True)
        self._update_tab_counts()
        save_sessions_snapshot(self._all_sessions)
        if not self._current_session:
            self._apply_filter(preserve_selection=True)

    def _on_sessions_loaded(self, sessions: list[ServiceSession]) -> None:
        if not getattr(self, "_is_silent_reload", False):
            self.progress_bar.setRange(0, 100)
            self.progress_bar.hide()
            self.refresh_btn.setEnabled(True)
        self._is_silent_reload = False

        existing_ids = {s.id for s in self._all_sessions}
        for s in sessions:
            if s.is_unread and (s.id not in self._known_unread_ids or s.id not in existing_ids):
                self._known_unread_ids.add(s.id)
                self._alert_new_session(s)
            if s.id not in existing_ids:
                existing_ids.add(s.id)
                self._known_session_ids.add(s.id)
                self._all_sessions.append(s)
            else:
                for idx, old_s in enumerate(self._all_sessions):
                    if old_s.id == s.id:
                        if old_s.messages and not s.messages:
                            s.messages = old_s.messages
                        self._all_sessions[idx] = s
                        break

        self._all_sessions.sort(key=lambda s: parse_iso_timestamp(s.created_at), reverse=True)
        self._update_tab_counts()
        self._apply_filter(preserve_selection=True)
        save_sessions_snapshot(self._all_sessions)

    def _on_sessions_error(self, err: str) -> None:
        if not getattr(self, "_is_silent_reload", False):
            self.progress_bar.setRange(0, 100)
            self.progress_bar.hide()
            self.refresh_btn.setEnabled(True)
        self._is_silent_reload = False
        logger.error("Failed to load sessions: %s", err)
        err_lower = str(err).lower()
        if "401" in err_lower or "token" in err_lower or "unauthorized" in err_lower or "revoked" in err_lower:
            aid = str(getattr(self, "_active_store_filter", "") or "")
            self.oauthExpired.emit(aid, "")

    def _update_tab_counts(self) -> None:
        q_pending = len([s for s in self._all_sessions if s.session_type == "question" and s.is_pending])
        c_pending = len([s for s in self._all_sessions if s.session_type == "claim" and s.is_pending])
        m_pending = len([s for s in self._all_sessions if s.session_type == "message" and s.is_pending])

        self.tab_btn_questions.setText(f"售前咨询 ({q_pending}待回复)" if q_pending > 0 else "售前咨询")
        self.tab_btn_claims.setText(f"订单索赔 ({c_pending}进行中)" if c_pending > 0 else "订单索赔")
        self.tab_btn_messages.setText(f"售后私信 ({m_pending}未读)" if m_pending > 0 else "售后私信")

        total_unread = len([s for s in self._all_sessions if s.is_unread])
        self.unreadCountChanged.emit(total_unread)

    def _update_list_summary(self, count: int) -> None:
        if self._active_tab == "question":
            unit = f"共 {count} 条提问"
        elif self._active_tab == "claim":
            unit = f"共 {count} 笔索赔"
        elif self._active_tab == "message":
            unit = f"共 {count} 条私信"
        else:
            unit = f"共 {count} 个会话"
        if hasattr(self, "list_summary_lbl"):
            self.list_summary_lbl.setText(unit)

    def _switch_tab(self, tab_id: str) -> None:
        self._active_tab = tab_id
        self.tab_btn_questions.setChecked(tab_id == "question")
        self.tab_btn_claims.setChecked(tab_id == "claim")
        self.tab_btn_messages.setChecked(tab_id == "message")
        self._apply_filter()

    def _on_filter_changed(self) -> None:
        self._active_store_filter = str(self.store_combo.currentData() or "all")
        self._active_site_filter = str(self.site_combo.currentData() or "all")
        self._active_status_filter = str(self.status_combo.currentData() or "all")
        self._apply_filter()

    def refresh_accounts(self) -> None:
        """Refresh store filter dropdown when account authorizations update."""
        populate_store_combo(self.store_combo, auth_mgr=self.auth, all_label="全部店铺", all_value="all")

    def open_order_conversation(self, order: Any) -> None:
        """Navigate to After-sales Messages tab and open or create the conversation for the given order."""
        if not order:
            return

        order_id = str(getattr(order, "order_id", "") or "").strip()
        pack_id = str(getattr(order, "pack_id", "") or "").strip() or order_id
        account_id = str(getattr(order, "account_id", "") or "").strip()
        site_id = str(getattr(order, "site_id", "") or "").strip()
        store_name = str(getattr(order, "store_name", "") or "").strip()
        buyer_nickname = str(getattr(order, "buyer_nickname", "") or "买家").strip()
        buyer_id = str(getattr(order, "buyer_id", "") or "").strip()

        # 1. Switch to 'message' tab (售后私信)
        self._switch_tab("message")

        # 2. Reset filters so this session won't be hidden
        if self._active_store_filter != "all" and self._active_store_filter != account_id:
            idx = self.store_combo.findData("all")
            if idx >= 0:
                self.store_combo.setCurrentIndex(idx)
                self._active_store_filter = "all"

        if self._active_site_filter != "all" and self._active_site_filter != site_id:
            idx = self.site_combo.findData("all")
            if idx >= 0:
                self.site_combo.setCurrentIndex(idx)
                self._active_site_filter = "all"

        if self._active_status_filter != "all":
            idx = self.status_combo.findData("all")
            if idx >= 0:
                self.status_combo.setCurrentIndex(idx)
                self._active_status_filter = "all"

        if hasattr(self, "search_edit") and self.search_edit.text():
            self.search_edit.clear()

        # 3. Locate existing session
        target_session: ServiceSession | None = None
        for s in self._all_sessions:
            if s.session_type == "message":
                if (order_id and s.order_id == order_id) or (pack_id and (s.pack_id == pack_id or s.id == f"m_{pack_id}")):
                    target_session = s
                    break

        # 4. If not found, create a synthesized message session for this order
        if not target_session:
            primary_item = getattr(order, "primary_item", None)
            item_id = primary_item.item_id if primary_item else ""
            item_title = primary_item.title if primary_item else ""
            item_thumb = primary_item.thumbnail if primary_item else ""
            sku = primary_item.sku if primary_item else ""
            variation_id = primary_item.variation_id if primary_item else ""
            tot_amt = getattr(order, "total_amount", 0.0)
            curr = getattr(order, "currency_id", "USD")

            sess_id = f"m_{pack_id}"
            created_at = getattr(order, "date_created", "") or datetime.now(timezone.utc).isoformat()
            updated_at = getattr(order, "date_closed", "") or created_at

            target_session = ServiceSession(
                id=sess_id,
                session_type="message",
                account_id=account_id,
                account_name=store_name or account_id,
                site_id=site_id,
                buyer_id=buyer_id,
                buyer_nickname=buyer_nickname,
                item_id=item_id,
                item_title=item_title,
                item_thumbnail=item_thumb,
                item_price=f"{curr} {tot_amt:.2f}" if tot_amt > 0 else "",
                item_permalink="",
                sku=sku,
                variation_id=variation_id,
                order_id=order_id,
                pack_id=pack_id,
                status="unread",
                stage="",
                created_at=created_at,
                updated_at=updated_at,
                summary=f"订单 {order_id} 售后私信",
                summary_translated=f"订单 {order_id} 售后私信",
                deadline=None,
                can_reply=True,
                is_unread=False,
                messages=[],
            )
            self._all_sessions.insert(0, target_session)
            self._update_tab_counts()
            save_sessions_snapshot(self._all_sessions)

        # 5. Refresh view and select item
        self._apply_filter(preserve_selection=False)

        found_row = -1
        for row in range(self.session_list.count()):
            item = self.session_list.item(row)
            if item and item.data(Qt.ItemDataRole.UserRole) == target_session.id:
                found_row = row
                break

        if found_row >= 0:
            self.session_list.setCurrentRow(found_row)
        self._display_session_detail(target_session)

        # Focus reply input
        if hasattr(self, "reply_input"):
            QTimer.singleShot(150, lambda: self.reply_input.setFocus())

    def stop(self) -> None:
        """Gracefully stop all background worker threads before destruction."""
        if hasattr(self, "_poll_timer") and self._poll_timer.isActive():
            self._poll_timer.stop()
        if getattr(self, "worker", None) is not None and self.worker.isRunning():
            try:
                self.worker.quit()
                self.worker.wait(1500)
            except Exception:
                pass
        if getattr(self, "trans_worker", None) is not None and self.trans_worker.isRunning():
            try:
                self.trans_worker.quit()
                self.trans_worker.wait(1500)
            except Exception:
                pass
        if getattr(self, "send_worker", None) is not None and self.send_worker.isRunning():
            try:
                self.send_worker.quit()
                self.send_worker.wait(1500)
            except Exception:
                pass
        for w in list(self._active_msg_workers):
            if w.isRunning():
                try:
                    w.quit()
                    w.wait(1500)
                except Exception:
                    pass
        self._active_msg_workers.clear()
        for w in list(self._active_claim_action_workers):
            if w.isRunning():
                try:
                    w.quit()
                    w.wait(1500)
                except Exception:
                    pass
        self._active_claim_action_workers.clear()

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)

    def _apply_filter(self, preserve_selection: bool = False) -> None:
        selected_session_id = self._current_session.id if (preserve_selection and self._current_session) else None
        query = self.search_edit.text().strip().lower()
        self.session_list.clear()

        filtered = [
            s for s in self._all_sessions
            if (self._active_tab == "all" or s.session_type == self._active_tab)
            and (self._active_store_filter == "all" or s.account_id == self._active_store_filter)
            and (self._active_site_filter == "all" or s.site_id == self._active_site_filter)
        ]

        if self._active_status_filter == "opened":
            filtered = [
                s for s in filtered
                if (s.session_type == "claim" and s.status != "closed")
                or (s.session_type == "question" and s.status == "unanswered")
                or (s.session_type == "message" and (s.is_unread or (s.can_reply and s.status != "closed")))
            ]
        elif self._active_status_filter == "closed":
            filtered = [
                s for s in filtered
                if (s.session_type == "claim" and s.status == "closed")
                or (s.session_type == "question" and s.status == "answered")
                or (s.session_type == "message" and (not s.can_reply or s.status == "closed"))
            ]

        if query:
            filtered = [
                s for s in filtered
                if query in s.item_id.lower()
                or query in s.buyer_nickname.lower()
                or query in s.order_id.lower()
                or query in s.summary.lower()
            ]

        # Sort: opened/unanswered sessions first, then strictly descending by created_at
        def _sort_key(s: ServiceSession) -> tuple[int, float]:
            is_opened = 1 if (
                (s.session_type == "claim" and s.status != "closed") or
                (s.session_type == "question" and s.status == "unanswered") or
                (s.session_type == "message" and s.is_unread)
            ) else 0
            return (is_opened, parse_iso_timestamp(s.created_at))

        filtered.sort(key=_sort_key, reverse=True)

        # Populate list widget
        target_row = -1
        for row_idx, s in enumerate(filtered):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, s.id)

            card_widget = self._create_session_item_widget(s)
            item.setSizeHint(QSize(0, 88))

            self.session_list.addItem(item)
            self.session_list.setItemWidget(item, card_widget)
            if selected_session_id and s.id == selected_session_id:
                target_row = row_idx

        if self.session_list.count() > 0:
            select_idx = target_row if target_row >= 0 else 0
            self.session_list.blockSignals(True)
            self.session_list.setCurrentRow(select_idx)
            self.session_list.blockSignals(False)
            target_sess = filtered[select_idx]
            if not selected_session_id or target_sess.id != self._displayed_session_id:
                self._display_session_detail(target_sess)
        else:
            self._display_session_detail(None)

        self._update_list_summary(len(filtered))

    def _create_session_item_widget(self, session: ServiceSession) -> QWidget:
        widget = QWidget()
        widget.setFixedHeight(88)
        widget.setMinimumWidth(360)
        widget.setStyleSheet("background: transparent; border: none;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        # Left 48x48 Product Thumbnail (click-to-zoom disabled in list, only available in conversation detail)
        thumb_lbl = QLabel()
        thumb_lbl.setFixedSize(48, 48)
        thumb_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        thumb_lbl.setStyleSheet("background: rgba(20, 24, 22, 0.7); border: 1px solid rgba(78, 71, 47, 0.5); border-radius: 6px;")
        thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        load_image_async(
            session.item_thumbnail,
            QSize(48, 48),
            thumb_lbl.setPixmap,
            radius=6,
        )
        layout.addWidget(thumb_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        # Right content column
        right_col = QVBoxLayout()
        right_col.setSpacing(3)
        right_col.setContentsMargins(0, 0, 0, 0)

        # Header: Store pill + Site flag + Elapsed
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        site_flag = SITE_FLAGS.get(session.site_id, "🌐")
        pill_text = f"{site_flag} {session.account_name} · {session.site_id}"
        pill_lbl = QLabel(pill_text)
        pill_lbl.setStyleSheet("background: rgba(78, 71, 47, 0.5); color: #E6E2D8; font-size: 11px; font-weight: 500; padding: 2px 6px; border-radius: 3px;")
        top_row.addWidget(pill_lbl)

        top_row.addStretch()

        if session.created_at:
            time_str = format_message_date(session.created_at)[:16]
            time_lbl = QLabel(time_str)
            time_lbl.setStyleSheet("color: #8C867A; font-size: 11px;")
            top_row.addWidget(time_lbl)

        right_col.addLayout(top_row)

        # Middle: Buyer (left) + Status badge (strictly right-aligned)
        mid_row = QHBoxLayout()
        mid_row.setSpacing(6)
        buyer_lbl = QLabel(f"<b>{session.buyer_nickname}</b>")
        buyer_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        buyer_lbl.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: bold;")
        mid_row.addWidget(buyer_lbl)
        mid_row.addStretch()

        badge = None
        rep_badge = None
        style_gold = "background: rgba(245, 158, 11, 0.16); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.35); font-size: 11px; font-weight: 500; border-radius: 4px;"
        style_green = "background: rgba(16, 185, 129, 0.14); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.30); font-size: 11px; font-weight: 500; border-radius: 4px;"
        style_gray = "background: rgba(100, 116, 139, 0.16); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.28); font-size: 11px; font-weight: 500; border-radius: 4px;"

        if session.session_type == "claim":
            if session.affects_reputation == "affected":
                rep_badge = QLabel("影响信誉: 是")
                rep_badge.setStyleSheet("background: rgba(239, 68, 68, 0.16); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.35); font-size: 11px; font-weight: 500; border-radius: 4px;")
            elif session.affects_reputation == "not_affected":
                rep_badge = QLabel("影响信誉: 否")
                rep_badge.setStyleSheet("background: rgba(16, 185, 129, 0.14); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.30); font-size: 11px; font-weight: 500; border-radius: 4px;")

            if session.status == "closed":
                badge = QLabel("● 已结案")
                badge.setStyleSheet(style_gray)
            else:
                badge = QLabel("● 索赔中")
                badge.setStyleSheet(style_gold)
        elif session.session_type == "question":
            if session.status == "answered":
                badge = QLabel("● 已回复")
                badge.setStyleSheet(style_gray)
            else:
                badge = QLabel("● 待回复")
                badge.setStyleSheet(style_gold)
        elif session.session_type == "message":
            if session.status == "blocked" or not session.can_reply:
                badge = QLabel("● 不可回复")
                badge.setStyleSheet(style_gray)
            elif session.status in ("new_message", "unanswered") or (session.is_unread and session.status != "unread"):
                badge = QLabel("● 待回复")
                badge.setStyleSheet(style_gold)
            elif session.status == "unread":
                badge = QLabel("● 未读")
                badge.setStyleSheet(style_gold)
            else:
                badge = QLabel("● 已读")
                badge.setStyleSheet(style_green)

        if rep_badge:
            rep_badge.setFixedSize(76, 22)
            rep_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mid_row.addWidget(rep_badge)

        if badge:
            badge.setFixedSize(74, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            mid_row.addWidget(badge)
        right_col.addLayout(mid_row)

        # Snippet text or product title / claim order info
        if session.session_type == "claim":
            stage_cn = CLAIM_STAGE_NAMES.get(session.stage, session.stage)
            oid_str = session.order_id or "无关联订单"
            snip_lbl = QLabel(f"订单: <b>{oid_str}</b> · {stage_cn}")
            snip_lbl.setStyleSheet("color: #E2E8F0; font-size: 11px;")
        else:
            display_text = session.item_title if (session.item_title and not session.item_title.startswith("商品 ")) else session.summary
            snip = display_text[:60] + ("..." if len(display_text) > 60 else "")
            snip_lbl = QLabel(snip)
            snip_lbl.setStyleSheet("color: #C8C3B7; font-size: 12px;")
        right_col.addWidget(snip_lbl)

        layout.addLayout(right_col, 1)

        # Card mouse click passthrough to session_list row selection
        def _on_card_mouse_press(event, s=session):
            for r in range(self.session_list.count()):
                it = self.session_list.item(r)
                if it and it.data(Qt.ItemDataRole.UserRole) == s.id:
                    self.session_list.setCurrentRow(r)
                    break
        widget.mousePressEvent = _on_card_mouse_press

        return widget

    def _update_claim_action_buttons(self, session: ServiceSession | None) -> None:
        if not session or session.session_type != "claim":
            self.claim_actions_frame.hide()
            return

        self.claim_actions_frame.show()
        stage_cn = CLAIM_STAGE_NAMES.get(session.stage, session.stage)
        is_opened = (session.status != "closed")

        if not is_opened:
            self.claim_stage_lbl.setText("索赔状态: <b style='color: #94A3B8;'>已完结 (已结案)</b>")
            self.claim_dispute_btn.setEnabled(False)
            self.claim_dispute_btn.setText("请求调解")
            self.claim_dispute_btn.setToolTip("该索赔已结案，无法请求调解")
            self.claim_partial_refund_btn.setEnabled(False)
            self.claim_partial_refund_btn.setToolTip("该索赔已结案，无法发起退款")
            self.claim_full_refund_btn.setEnabled(False)
            self.claim_full_refund_btn.setToolTip("该索赔已结案，无法发起退款")
        else:
            self.claim_stage_lbl.setText(f"索赔状态: <b style='color: #FBBF24;'>进行中 · {stage_cn}</b>")
            if session.stage == "dispute":
                self.claim_dispute_btn.setEnabled(False)
                self.claim_dispute_btn.setText("调解中")
                self.claim_dispute_btn.setToolTip("当前索赔已处于美客多官方平台调解介入中")
            else:
                self.claim_dispute_btn.setEnabled(True)
                self.claim_dispute_btn.setText("请求调解")
                self.claim_dispute_btn.setToolTip("向美客多官方申请人工介入调解仲裁")

            self.claim_partial_refund_btn.setEnabled(True)
            self.claim_full_refund_btn.setEnabled(True)

    def _on_request_mediation(self) -> None:
        session = self._current_session
        if not session or session.session_type != "claim":
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("请求平台调解")
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setText(f"确定要为订单 {session.order_id} 申请美客多平台调解吗？")
        msg_box.setInformativeText("申请调解后，美客多官方客服将直接介入跟进并要求双方提供相关凭证。")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        yes_btn = msg_box.button(QMessageBox.StandardButton.Yes)
        if yes_btn:
            yes_btn.setText("确认申请")
        no_btn = msg_box.button(QMessageBox.StandardButton.No)
        if no_btn:
            no_btn.setText("取消")
        if msg_box.exec() != QMessageBox.StandardButton.Yes:
            return

        self._execute_claim_action(session, "open-dispute", {})

    def _on_partial_refund(self) -> None:
        session = self._current_session
        if not session or session.session_type != "claim":
            return

        dlg = PartialRefundDialog(self.client, session, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            pct = dlg.selected_percentage
            self._execute_claim_action(session, "partial-refund", {"percentage": pct})

    def _on_full_refund(self) -> None:
        session = self._current_session
        if not session or session.session_type != "claim":
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("确认全额退款")
        msg_box.setIcon(QMessageBox.Icon.Warning)
        msg_box.setText(f"⚠️ 确定要为订单 {session.order_id} 执行全额退款吗？")
        msg_box.setInformativeText("款项将直接原路退还给买家，本笔索赔将直接完结关闭。此操作不可逆！")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg_box.setDefaultButton(QMessageBox.StandardButton.No)
        yes_btn = msg_box.button(QMessageBox.StandardButton.Yes)
        if yes_btn:
            yes_btn.setText("确认全额退款")
        no_btn = msg_box.button(QMessageBox.StandardButton.No)
        if no_btn:
            no_btn.setText("取消")
        if msg_box.exec() != QMessageBox.StandardButton.Yes:
            return

        self._execute_claim_action(session, "full-refund", {})

    def _execute_claim_action(self, session: ServiceSession, action: str, extra_params: dict[str, Any]) -> None:
        self.claim_dispute_btn.setEnabled(False)
        self.claim_partial_refund_btn.setEnabled(False)
        self.claim_full_refund_btn.setEnabled(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.show()

        worker = ClaimActionWorker(
            client=self.client,
            account_id=session.account_id,
            claim_id=session.id,
            action=action,
            extra_params=extra_params,
        )
        self._active_claim_action_workers.add(worker)

        def _cleanup(w: ClaimActionWorker = worker) -> None:
            self._active_claim_action_workers.discard(w)
            w.deleteLater()
            self.progress_bar.hide()
            self._update_claim_action_buttons(self._current_session)

        def _on_success(act: str, cid: str, res: dict) -> None:
            _cleanup()
            act_names = {
                "open-dispute": "请求平台调解",
                "partial-refund": "部分退款提议",
                "full-refund": "全额退款",
            }
            name = act_names.get(act, act)
            QMessageBox.information(self, "操作成功", f"{name} 已成功提交美客多平台！")
            self.reload_sessions()

        def _on_err(act: str, cid: str, err: str) -> None:
            _cleanup()
            act_names = {
                "open-dispute": "请求平台调解",
                "partial-refund": "部分退款提议",
                "full-refund": "全额退款",
            }
            name = act_names.get(act, act)
            QMessageBox.critical(self, "操作失败", f"{name} 提交失败：\n{err}")

        worker.finished.connect(_on_success)
        worker.error.connect(_on_err)
        worker.start()

    # ------------------------------------------------------------------
    # Session Details & Chat Bubble Display
    # ------------------------------------------------------------------

    def _on_session_clicked(self, item: QListWidgetItem) -> None:
        sess_id = item.data(Qt.ItemDataRole.UserRole)
        sess = next((s for s in self._all_sessions if s.id == sess_id), None)
        if sess and sess.is_unread:
            sess.is_unread = False
            card_widget = self._create_session_item_widget(sess)
            self.session_list.setItemWidget(item, card_widget)
            self.toggle_read_btn.setText("标为未读")
            self._update_tab_counts()
            save_sessions_snapshot(self._all_sessions)

    def _on_session_selected(self, current: QListWidgetItem | None, previous: QListWidgetItem | None) -> None:
        if not current:
            self._display_session_detail(None)
            return
        sess_id = current.data(Qt.ItemDataRole.UserRole)
        sess = next((s for s in self._all_sessions if s.id == sess_id), None)
        if sess and sess.is_unread:
            sess.is_unread = False
            card_widget = self._create_session_item_widget(sess)
            self.session_list.setItemWidget(current, card_widget)
            self._update_tab_counts()
            save_sessions_snapshot(self._all_sessions)
        self._display_session_detail(sess)

    def _clear_chat_bubbles(self) -> None:
        """Safely delete all chat bubble widgets while maintaining persistent isolated bottom stretch."""
        while self.chat_layout.count() > 0:
            child = self.chat_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        self.chat_layout.addStretch(1)

    def _display_session_detail(self, session: ServiceSession | None, force_rebuild: bool = False) -> None:
        self._current_session = session

        if not session:
            self._displayed_session_id = None
            self._displayed_messages_count = 0
            self._clear_chat_bubbles()
            self.context_thumb_lbl.setPixmap(get_placeholder_pixmap(QSize(56, 56), "📦", radius=6))
            self.context_title_lbl.setText("暂无选中的会话")
            self.context_meta_lbl.setText("请在左侧列表中点击任意提问或索赔会话")
            self.context_link_lbl.setText("")
            self.chip_item_lbl.setText("")
            self.chip_item_lbl.hide()
            self.chip_order_lbl.setText("")
            self.chip_order_lbl.hide()
            self.chip_pack_lbl.setText("")
            self.chip_pack_lbl.hide()
            self.chip_stage_lbl.setText("")
            self.chip_stage_lbl.hide()
            self.chip_rep_lbl.setText("")
            self.chip_rep_lbl.hide()
            self.sep_item_order.hide()
            self.sep_order_pack.hide()
            self.sep_pack_stage.hide()
            self.sep_stage_rep.hide()
            self.open_browser_btn.setEnabled(False)
            self.refresh_chat_btn.setEnabled(False)
            self.refresh_chat_btn.hide()
            self.toggle_read_btn.setEnabled(False)
            self.toggle_read_btn.setText("标为已读")
            self.reply_dock.setEnabled(False)
            self.status_banner.hide()
            self._update_claim_action_buttons(None)
            self.selected_images.clear()
            self._refresh_image_previews()
            return

        is_same_session = (self._displayed_session_id == session.id) and not force_rebuild

        # 1. Update Context Card Header
        if not session.item_id and session.item_permalink:
            m = re.search(r'(ML[A-Z])-?(\d+)', session.item_permalink)
            if m:
                session.item_id = f"{m.group(1)}{m.group(2)}"

        display_title = session.item_title or (f"商品 {session.item_id}" if session.item_id else "美客多会话商品")
        self.context_title_lbl.setText(display_title)

        meta_parts = [
            f"{session.site_display}",
            f"店铺: <b>{session.account_name}</b>",
            f"买家: <b>{session.buyer_nickname}</b>",
        ]
        if session.item_price:
            meta_parts.append(f"价格: <b>{session.item_price}</b>")
        if session.sku:
            meta_parts.append(f"SKU: <code>{session.sku}</code>")
        self.context_meta_lbl.setText(" · ".join(meta_parts))

        # 1.2 Update Independent Chips with unified theme and dot separators
        has_item = bool(session.item_id or session.item_url)
        if has_item:
            display_id = session.item_id or "查看商品"
            self.chip_item_lbl.setText(f"商品链接: <b style='color: #F6F3EA;'>{display_id}</b> <span style='color: #78716C; font-size: 10px;'>↗</span>")
            self.chip_item_lbl.show()
        else:
            self.chip_item_lbl.hide()

        has_order = bool(session.order_id)
        if has_order:
            self.chip_order_lbl.setText(f"关联订单号: <b style='color: #F6F3EA;'>{session.order_id}</b>")
            self.chip_order_lbl.show()
        else:
            self.chip_order_lbl.hide()

        self.sep_item_order.setVisible(has_item and has_order)

        has_pack = bool(session.pack_id and session.pack_id != session.order_id)
        if has_pack:
            self.chip_pack_lbl.setText(f"包裹号: <b style='color: #F6F3EA;'>{session.pack_id}</b>")
            self.chip_pack_lbl.show()
        else:
            self.chip_pack_lbl.hide()

        self.sep_order_pack.setVisible((has_item or has_order) and has_pack)

        has_stage = bool(session.stage)
        if has_stage:
            stage_cn = CLAIM_STAGE_NAMES.get(session.stage.lower(), session.stage)
            self.chip_stage_lbl.setText(f"索赔阶段: <b style='color: #FBBF24;'>{stage_cn}</b>")
            self.chip_stage_lbl.show()
        else:
            self.chip_stage_lbl.hide()

        self.sep_pack_stage.setVisible((has_item or has_order or has_pack) and has_stage)

        has_rep = bool(session.affects_reputation) and session.session_type == "claim"
        if has_rep:
            if session.affects_reputation == "affected":
                self.chip_rep_lbl.setText("信誉影响: <b style='color: #F87171;'>影响信誉 ⚠️</b>")
            elif session.affects_reputation == "not_affected":
                self.chip_rep_lbl.setText("信誉影响: <b style='color: #34D399;'>不影响信誉 🛡️</b>")
            else:
                self.chip_rep_lbl.setText(f"信誉影响: <b>{session.affects_reputation}</b>")
            self.chip_rep_lbl.show()
        else:
            self.chip_rep_lbl.hide()

        self.sep_stage_rep.setVisible((has_item or has_order or has_pack or has_stage) and has_rep)

        # Update backward-compatible context_link_lbl string
        link_parts = []
        if session.item_url:
            display_id = session.item_id or "查看商品"
            link_parts.append(f"商品链接: <a href='{session.item_url}' style='color: #60A5FA; text-decoration: underline; font-weight: bold;'>{display_id} ↗</a>")
        if session.order_id:
            link_parts.append(f"关联订单号: <b>{session.order_id}</b>")
        if session.pack_id and session.pack_id != session.order_id:
            link_parts.append(f"包裹号: <b>{session.pack_id}</b>")
        if session.stage:
            stage_cn = CLAIM_STAGE_NAMES.get(session.stage.lower(), session.stage)
            link_parts.append(f"索赔阶段: <b>{stage_cn}</b>")
        if session.affects_reputation and session.session_type == "claim":
            rep_text = "影响信誉" if session.affects_reputation == "affected" else "不影响信誉"
            link_parts.append(f"信誉影响: <b>{rep_text}</b>")
        self.context_link_lbl.setText(" | ".join(link_parts))

        has_target = bool(session.item_url or session.order_id)
        self.open_browser_btn.setEnabled(has_target)
        if session.item_url:
            self.open_browser_btn.setText("在默认浏览器打开 ↗")
        elif session.order_id:
            self.open_browser_btn.setText("在浏览器打开订单 ↗")

        load_image_async(
            session.item_thumbnail,
            QSize(56, 56),
            self.context_thumb_lbl.setPixmap,
            radius=6,
        )

        self.toggle_read_btn.setEnabled(True)
        self.toggle_read_btn.setText("标为已读" if session.is_unread else "标为未读")

        if hasattr(self, "refresh_chat_btn"):
            self.refresh_chat_btn.setVisible(session.session_type in ("claim", "message"))
            self.refresh_chat_btn.setEnabled(True)
            self.refresh_chat_btn.setText("⟳ 刷新消息")

        if session.session_type == "question":
            self.upload_img_btn.setEnabled(False)
            self.upload_img_btn.setToolTip("美客多官方规则：售前咨询仅支持纯文字问答")
        else:
            self.upload_img_btn.setEnabled(True)
            self.upload_img_btn.setToolTip("选择图片凭证(JPG/PNG，最多3张)，或在输入框按Ctrl+V/Cmd+V粘贴截图")

        target_lang = session.target_language
        lang_name = "葡萄牙语" if target_lang == "pt" else "西班牙语"
        self.translate_action_btn.setText(f"⚡ 一键翻译为{lang_name} ({target_lang})")

        if not session.can_reply:
            self.reply_dock.setEnabled(False)
            self.status_banner.setText(f"⚠️ {session.blocked_reason or '当前会话不可回复'}")
            self.status_banner.show()
        else:
            self.reply_dock.setEnabled(True)
            self.status_banner.hide()

        self._update_claim_action_buttons(session)

        # 2. Chat Layout Updating / Incremental Rendering
        if is_same_session:
            if session.messages and len(session.messages) > self._displayed_messages_count:
                new_msgs = session.messages[self._displayed_messages_count:]
                for msg in new_msgs:
                    bubble = self._create_chat_bubble(msg)
                    self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
                self._displayed_messages_count = len(session.messages)
                QTimer.singleShot(50, self._scroll_chat_to_bottom)
            return

        # True switch to a different session
        self._displayed_session_id = session.id
        self._displayed_messages_count = 0
        self.selected_images.clear()
        self._refresh_image_previews()

        # Cancel previous in-flight message workers when switching
        for old_w in list(self._active_msg_workers):
            try:
                old_w.stop()
            except Exception:
                pass
        self._active_msg_workers.clear()

        # Clear existing chat layout safely
        self._clear_chat_bubbles()

        if session.messages:
            for msg in session.messages:
                bubble = self._create_chat_bubble(msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
            self._displayed_messages_count = len(session.messages)
            QTimer.singleShot(50, self._scroll_chat_to_bottom)

            # Silently fetch latest messages in background to ensure mediator proposals & new replies are seamlessly updated
            if session.session_type in ("claim", "message"):
                self._fetch_conversation_messages(session)
        else:
            if session.session_type == "question":
                init_msg = ServiceMessage(
                    id=session.id,
                    sender_role="buyer",
                    sender_name=session.buyer_nickname,
                    text=session.summary,
                    created_at=session.created_at,
                    is_seller=False,
                )
                bubble = self._create_chat_bubble(init_msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
                self._displayed_messages_count = 1
            else:
                loading_msg = ServiceMessage(
                    id=f"loading_{session.id}",
                    sender_role="system",
                    sender_name="系统提示",
                    text="⏳ 正在加载历史对话记录，请稍候...",
                    created_at=session.created_at,
                    is_seller=False,
                )
                bubble = self._create_chat_bubble(loading_msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
                self._displayed_messages_count = 0
                self._fetch_conversation_messages(session)

    def _on_refresh_chat_clicked(self) -> None:
        """Manually trigger fresh sync of conversation messages from official API."""
        if not self._current_session:
            return
        if self._current_session.session_type not in ("claim", "message"):
            return
        if hasattr(self, "refresh_chat_btn"):
            self.refresh_chat_btn.setEnabled(False)
            self.refresh_chat_btn.setText("⟳ 正在同步...")
        self._fetch_conversation_messages(self._current_session)

    def _fetch_conversation_messages(self, session: ServiceSession) -> None:
        for old_w in list(self._active_msg_workers):
            try:
                old_w.stop()
            except Exception:
                pass
        self._active_msg_workers.clear()

        if hasattr(self, "refresh_chat_btn"):
            self.refresh_chat_btn.setEnabled(False)
            self.refresh_chat_btn.setText("⟳ 正在同步...")

        worker = FetchConversationMessagesWorker(self.client, session, auth_mgr=self.auth)
        self._active_msg_workers.add(worker)

        def _cleanup(w: FetchConversationMessagesWorker = worker) -> None:
            self._active_msg_workers.discard(w)
            w.deleteLater()
            if hasattr(self, "refresh_chat_btn"):
                self.refresh_chat_btn.setEnabled(True)
                self.refresh_chat_btn.setText("⟳ 刷新消息")

        worker.finished.connect(self._on_messages_loaded)
        worker.error.connect(self._on_messages_fetch_error)
        worker.finished.connect(lambda *_: _cleanup())
        worker.error.connect(lambda *_: _cleanup())
        worker.start()

    def _on_messages_fetch_error(self, session_id: str, err: str) -> None:
        if not self._current_session or self._current_session.id != session_id:
            return
        if not self._current_session.messages:
            self._clear_chat_bubbles()
            hint_msg = ServiceMessage(
                id=f"hint_{session_id}",
                sender_role="system",
                sender_name="系统提示",
                text="💬 当前订单暂无历史私信。您可以在下方直接向买家发起私信。",
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                is_seller=False,
            )
            bubble = self._create_chat_bubble(hint_msg)
            self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
            self._displayed_messages_count = 1

    def _on_messages_loaded(
        self,
        session_id: str,
        messages: list[ServiceMessage],
        detected_buyer_name: str | None = None,
    ) -> None:
        target_sess = next((s for s in self._all_sessions if s.id == session_id), None)
        if target_sess:
            target_sess.messages = messages
            if detected_buyer_name and (not target_sess.buyer_nickname or target_sess.buyer_nickname == "买家"):
                target_sess.buyer_nickname = detected_buyer_name
            save_sessions_snapshot(self._all_sessions)

        if not self._current_session or self._current_session.id != session_id:
            return

        if detected_buyer_name and (not self._current_session.buyer_nickname or self._current_session.buyer_nickname == "买家"):
            self._current_session.buyer_nickname = detected_buyer_name

        self._clear_chat_bubbles()

        if messages:
            self._current_session.messages = messages
            if self._current_session.session_type == "message" and not self._current_session.blocked_reason:
                last_m = messages[-1]
                if last_m.is_seller:
                    self._current_session.status = "read" if last_m.is_read else "unread"
                    self._current_session.is_unread = False
                else:
                    self._current_session.status = "new_message"
                    self._current_session.is_unread = True
                self._update_tab_counts()

            for msg in messages:
                bubble = self._create_chat_bubble(msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
            self._displayed_messages_count = len(messages)
        else:
            self._current_session.messages = []
            self._displayed_messages_count = 1
            if self._current_session.session_type == "message":
                hint_msg = ServiceMessage(
                    id=f"hint_{self._current_session.id}",
                    sender_role="system",
                    sender_name="系统提示",
                    text="💬 当前订单暂无历史私信记录。您可以在下方直接输入内容向买家发送消息。",
                    created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    is_seller=False,
                )
                bubble = self._create_chat_bubble(hint_msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)
            elif self._current_session.session_type == "claim":
                hint_msg = ServiceMessage(
                    id=f"hint_{self._current_session.id}",
                    sender_role="system",
                    sender_name="系统提示",
                    text="📋 当前索赔暂无历史调解沟通记录。",
                    created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    is_seller=False,
                )
                bubble = self._create_chat_bubble(hint_msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble, 0, Qt.AlignmentFlag.AlignTop)

        meta_parts = [
            f"{self._current_session.site_display}",
            f"店铺: <b>{self._current_session.account_name}</b>",
            f"买家: <b>{self._current_session.buyer_nickname}</b>",
        ]
        if self._current_session.item_price:
            meta_parts.append(f"价格: <b>{self._current_session.item_price}</b>")
        if self._current_session.sku:
            meta_parts.append(f"SKU: <code>{self._current_session.sku}</code>")
        self.context_meta_lbl.setText(" · ".join(meta_parts))

        if self._current_session.session_type == "claim":
            has_rep = bool(self._current_session.affects_reputation)
            if has_rep:
                if self._current_session.affects_reputation == "affected":
                    self.chip_rep_lbl.setText("信誉影响: <b style='color: #F87171;'>影响信誉 ⚠️</b>")
                elif self._current_session.affects_reputation == "not_affected":
                    self.chip_rep_lbl.setText("信誉影响: <b style='color: #34D399;'>不影响信誉 🛡️</b>")
                else:
                    self.chip_rep_lbl.setText(f"信誉影响: <b>{self._current_session.affects_reputation}</b>")
                self.chip_rep_lbl.show()
            else:
                self.chip_rep_lbl.hide()
            has_item = bool(self._current_session.item_id or self._current_session.item_url)
            has_order = bool(self._current_session.order_id)
            has_pack = bool(self._current_session.pack_id and self._current_session.pack_id != self._current_session.order_id)
            has_stage = bool(self._current_session.stage)
            self.sep_stage_rep.setVisible((has_item or has_order or has_pack or has_stage) and has_rep)

        # Refresh thumbnail in case an alternate non-conflicting picture was selected
        load_image_async(
            self._current_session.item_thumbnail,
            QSize(56, 56),
            self.context_thumb_lbl.setPixmap,
            radius=6,
        )
        curr_row = self.session_list.currentRow()
        if curr_row >= 0:
            curr_item = self.session_list.item(curr_row)
            if curr_item:
                card = self._create_session_item_widget(self._current_session)
                self.session_list.setItemWidget(curr_item, card)

        QTimer.singleShot(50, self._scroll_chat_to_bottom)

    def _scroll_chat_to_bottom(self) -> None:
        try:
            bar = self.chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    def _create_chat_bubble(self, msg: ServiceMessage) -> QWidget:
        container = QWidget()
        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        c_layout = QHBoxLayout(container)
        c_layout.setContentsMargins(0, 2, 0, 2)

        bubble_frame = QFrame()
        bubble_frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        b_layout = QVBoxLayout(bubble_frame)
        b_layout.setContentsMargins(12, 8, 12, 8)
        b_layout.setSpacing(4)
        b_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)

        # Sender header
        is_mediator = (msg.sender_role == "mediator")
        is_system = (msg.sender_role == "system")
        if is_mediator:
            notice_lbl = QLabel("⚖️ 平台仲裁调解专员通知")
            notice_lbl.setStyleSheet("color: #FBBF24; font-size: 11px; font-weight: bold; padding-bottom: 2px;")
            b_layout.addWidget(notice_lbl)

        read_badge = ""
        is_post_sale = bool(self._current_session and self._current_session.session_type == "message")
        if msg.is_seller and is_post_sale:
            if msg.is_read:
                read_time = f" {msg.read_date[11:16]}" if len(msg.read_date) >= 16 else ""
                read_badge = f" · <span style='color: #34d399; font-weight: 600;'>已读{read_time}</span>"
            else:
                read_badge = " · <span style='color: #fbbf24; font-weight: 500;'>未读</span>"

        if is_mediator:
            hdr_lbl = QLabel(f"<b>美客多官方调解员</b> · <span style='color: #FCD34D;'>平台官方仲裁</span> · <span style='color: #A8A29E;'>{msg.created_at[:16].replace('T', ' ')}</span>")
        elif is_system:
            time_part = f" · <span style='color: #78716C;'>{msg.created_at[:16].replace('T', ' ')}</span>" if msg.created_at else ""
            hdr_lbl = QLabel(f"<b>系统提示</b>{time_part}")
        else:
            hdr_lbl = QLabel(f"<b>{msg.sender_name}</b> · <span style='color: #777266;'>{msg.created_at[:16].replace('T', ' ')}</span>{read_badge}")
        hdr_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        hdr_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        hdr_lbl.setStyleSheet("font-size: 11px;")
        b_layout.addWidget(hdr_lbl)

        # Message text
        text_lbl = QLabel(msg.text)
        text_lbl.setWordWrap(True)
        text_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        text_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        text_lbl.setStyleSheet("font-size: 13px; line-height: 1.4;")
        b_layout.addWidget(text_lbl)

        # Attachments preview grid (if any)
        if msg.attachments:
            att_box = QHBoxLayout()
            att_box.setSpacing(8)
            for att_path in msg.attachments:
                att_lbl = QLabel()
                att_lbl.setFixedSize(76, 76)
                att_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
                att_lbl.setStyleSheet("border: 1px solid #4E472F; border-radius: 6px; background: #171D1A;")
                att_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                att_lbl.setToolTip("点击放大查看买家上传凭据图片")
                load_image_async(
                    att_path,
                    QSize(76, 76),
                    att_lbl.setPixmap,
                    radius=6,
                )
                att_lbl.mousePressEvent = lambda e, p=att_path: show_image_preview(p, title="买家凭据原图", parent=self)
                att_box.addWidget(att_lbl)
            att_box.addStretch()
            b_layout.addLayout(att_box)

        # Translated snippet if available (clean sans-serif typography, no italics)
        has_valid_trans = (
            bool(msg.text_translated)
            and msg.text_translated.strip().lower() != msg.text.strip().lower()
        )
        if has_valid_trans:
            trans_lbl = QLabel(f"译: {msg.text_translated}")
            trans_lbl.setWordWrap(True)
            trans_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            trans_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            trans_lbl.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            trans_lbl.setStyleSheet("color: #C8C3B7; font-size: 11px; line-height: 1.4; font-style: normal;")
            trans_lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            trans_lbl.customContextMenuRequested.connect(
                lambda pos, target=trans_lbl, m=msg: self._show_chat_context_menu(pos, target, m)
            )
            b_layout.addWidget(trans_lbl)

        # Attach right-click context menu to bubble frame, text, and header
        for target_w in (bubble_frame, text_lbl, hdr_lbl):
            target_w.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            target_w.customContextMenuRequested.connect(
                lambda pos, target=target_w, m=msg: self._show_chat_context_menu(pos, target, m)
            )

        if msg.is_seller:
            # Right-aligned seller message (forest green)
            c_layout.addStretch(1)
            bubble_frame.setObjectName("sellerBubble")
            bubble_frame.setStyleSheet("""
                QFrame#sellerBubble {
                    background-color: #1e3d27;
                    border: 1px solid #2F6B3F;
                    border-radius: 8px;
                }
                QFrame#sellerBubble QLabel { color: #F6F3EA; background: transparent; border: none; padding: 0; }
            """)
            c_layout.addWidget(bubble_frame, 0, Qt.AlignmentFlag.AlignTop)
        elif is_mediator:
            # Left-aligned mediator message (amber / gold official arbitration frame)
            bubble_frame.setObjectName("mediatorBubble")
            bubble_frame.setStyleSheet("""
                QFrame#mediatorBubble {
                    background-color: #262117;
                    border: 1px solid #D97706;
                    border-left: 4px solid #F59E0B;
                    border-radius: 8px;
                }
                QFrame#mediatorBubble QLabel { color: #FEF3C7; background: transparent; border: none; padding: 0; }
            """)
            c_layout.addWidget(bubble_frame, 0, Qt.AlignmentFlag.AlignTop)
            c_layout.addStretch(1)
        elif is_system:
            # Centered neutral system notice
            c_layout.addStretch(1)
            bubble_frame.setObjectName("systemBubble")
            bubble_frame.setStyleSheet("""
                QFrame#systemBubble {
                    background-color: #161C19;
                    border: 1px dashed #3D4A41;
                    border-radius: 8px;
                }
                QFrame#systemBubble QLabel { color: #A8A29E; background: transparent; border: none; padding: 0; }
            """)
            c_layout.addWidget(bubble_frame, 0, Qt.AlignmentFlag.AlignTop)
            c_layout.addStretch(1)
        else:
            # Left-aligned buyer message (dark slate)
            bubble_frame.setObjectName("buyerBubble")
            bubble_frame.setStyleSheet("""
                QFrame#buyerBubble {
                    background-color: #202820;
                    border: 1px solid #4E472F;
                    border-radius: 8px;
                }
                QFrame#buyerBubble QLabel { color: #E6E2D8; background: transparent; border: none; padding: 0; }
            """)
            c_layout.addWidget(bubble_frame, 0, Qt.AlignmentFlag.AlignTop)
            c_layout.addStretch(1)

        return container

    def _format_single_message_text(self, msg: ServiceMessage, include_header: bool = False) -> str:
        """Format a single message, optionally with sender header and translated text."""
        parts: list[str] = []
        if include_header:
            time_str = msg.created_at[:16].replace("T", " ") if msg.created_at else ""
            parts.append(f"[{msg.sender_name} {time_str}]".strip())
        parts.append(msg.text)
        if msg.text_translated and msg.text_translated.strip().lower() != msg.text.strip().lower():
            parts.append(f"(译: {msg.text_translated})")
        return "\n".join(parts)

    def _copy_single_message(self, msg: ServiceMessage, include_header: bool = False) -> None:
        """Copy current message to system clipboard silently."""
        text = self._format_single_message_text(msg, include_header=include_header)
        copy_pure_text(text)

    def _copy_all_messages(self) -> None:
        """Copy all messages in the active conversation session to system clipboard silently."""
        if not self._current_session:
            return
        msgs = self._current_session.messages
        if not msgs:
            fallback = ServiceMessage(
                id=self._current_session.id,
                sender_role="buyer",
                sender_name=self._current_session.buyer_nickname,
                text=self._current_session.summary,
                created_at=self._current_session.created_at,
                is_seller=False,
            )
            msgs = [fallback]

        blocks = [self._format_single_message_text(m, include_header=True) for m in msgs]
        full_text = "\n\n".join(blocks)
        copy_pure_text(full_text)

    def _make_chip_click_copy_handler(self, widget: QLabel, get_val_fn: Any):
        return make_click_copy_handler(widget, get_val_fn)

    def _show_target_copy_menu(self, pos: QPoint, target_widget: QWidget, get_val_fn: Any) -> None:
        """Show clean unified dark menu with single '复制' copying the exact target value without fluff."""
        menu = show_pure_copy_menu(target_widget, pos, getter=get_val_fn, popup=True)
        self._last_context_menu = menu

    def _show_simple_copy_menu(self, pos: QPoint, target_widget: QWidget) -> None:
        """Show unified dark-themed context menu with a single '复制' option and silent execution."""
        def _get():
            if isinstance(target_widget, QLabel):
                if target_widget.hasSelectedText() and target_widget.selectedText().strip():
                    return target_widget.selectedText().strip()
                if target_widget == self.context_title_lbl and self._current_session and self._current_session.item_title:
                    return self._current_session.item_title.strip()
                return extract_copyable_text(target_widget)
            if self._current_session:
                return self._current_session.order_id or self._current_session.item_id or self._current_session.item_title or ""
            return ""

        menu = show_pure_copy_menu(target_widget, pos, getter=_get, popup=True)
        self._last_context_menu = menu

    def _show_chat_context_menu(self, pos: QPoint, source_widget: QWidget, msg: ServiceMessage | None = None) -> None:
        """Right-click menu on message bubble: [复制] / [🌐 翻译为中文] / [复制全部]."""
        if not self._current_session:
            return

        menu = QMenu(source_widget)

        if msg is not None:
            act_copy = menu.addAction("复制")
            act_copy.triggered.connect(lambda: self._copy_single_message(msg, include_header=False))

            act_trans = menu.addAction("🌐 翻译为中文")
            act_trans.triggered.connect(lambda: self._translate_single_message(msg))
        else:
            act = menu.addAction("复制全部")
            act.triggered.connect(self._copy_all_messages)

        global_pos = source_widget.mapToGlobal(pos)
        self._last_context_menu = menu
        menu.popup(global_pos)

    def _translate_single_message(self, msg: ServiceMessage) -> None:
        """Translate a single message to Chinese on demand."""
        if not msg or not msg.text:
            return

        def _do_trans():
            try:
                zh = translate_text(msg.text, target_lang="zh", timeout=3.5)
                if zh and zh.strip().lower() != msg.text.strip().lower():
                    msg.text_translated = zh
                    if self._current_session:
                        save_sessions_snapshot(self._all_sessions)
                    QTimer.singleShot(0, lambda: self._display_session_detail(self._current_session, force_rebuild=True))
                else:
                    QTimer.singleShot(0, lambda: QToolTip.showText(QCursor.pos(), "已是中文或无需翻译", self))
            except Exception as e:
                QTimer.singleShot(0, lambda: QToolTip.showText(QCursor.pos(), f"翻译失败: {e}", self))

        threading.Thread(target=_do_trans, daemon=True).start()

    # ------------------------------------------------------------------
    # Translation & Template Handling
    # ------------------------------------------------------------------

    def _on_choose_image_clicked(self) -> None:
        if not self._current_session:
            return
        if self._current_session.session_type == "question":
            QMessageBox.information(
                self,
                "提示",
                "美客多官方规则限制：售前咨询仅支持纯文字问答，订单索赔与售后私信支持上传图片凭证。",
            )
            return

        current_count = len(self.selected_images)
        if current_count >= 3:
            QMessageBox.warning(self, "图片数量已满", "每次回复最多支持上传 3 张图片凭证。")
            return

        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片凭证",
            "",
            "图片文件 (*.jpg *.jpeg *.png *.webp);;所有文件 (*.*)",
        )
        if not files:
            return

        for f in files:
            if len(self.selected_images) >= 3:
                break
            if f not in self.selected_images:
                self.selected_images.append(f)

        self._refresh_image_previews()

    def _on_image_pasted_from_clipboard(self, tmp_path: str) -> None:
        if not self._current_session or self._current_session.session_type == "question":
            QToolTip.showText(QCursor.pos(), "美客多售前咨询不支持发送图片", self)
            return
        if len(self.selected_images) >= 3:
            QToolTip.showText(QCursor.pos(), "已达到最多 3 张图片上限", self)
            return
        self.selected_images.append(tmp_path)
        self._refresh_image_previews()

    def _remove_selected_image(self, index: int) -> None:
        if 0 <= index < len(self.selected_images):
            self.selected_images.pop(index)
            self._refresh_image_previews()

    def _refresh_image_previews(self) -> None:
        while self.preview_layout.count():
            item = self.preview_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

        if not self.selected_images:
            self.preview_container.hide()
            self.img_count_lbl.setText("")
            return

        self.preview_container.show()
        self.img_count_lbl.setText(f"已选 {len(self.selected_images)}/3 张图片")

        for idx, img_path in enumerate(self.selected_images):
            card = QFrame()
            card.setStyleSheet("background: #171D1A; border: 1px solid #4E472F; border-radius: 6px; padding: 2px;")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(4, 2, 6, 2)
            card_layout.setSpacing(6)

            thumb_lbl = QLabel()
            pix = QPixmap(img_path)
            if not pix.isNull():
                thumb_lbl.setPixmap(pix.scaled(36, 36, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            else:
                thumb_lbl.setText("🖼️")
            card_layout.addWidget(thumb_lbl)

            name_lbl = QLabel(os.path.basename(img_path))
            name_lbl.setStyleSheet("color: #E2DFD2; font-size: 11px;")
            card_layout.addWidget(name_lbl)

            del_btn = QPushButton("✕")
            del_btn.setFixedSize(18, 18)
            del_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            del_btn.setStyleSheet("""
                QPushButton {
                    background: #381A1A;
                    color: #EF4444;
                    border: none;
                    border-radius: 9px;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 0;
                }
                QPushButton:hover {
                    background: #DC2626;
                    color: #FFFFFF;
                }
            """)
            del_btn.clicked.connect(lambda _, i=idx: self._remove_selected_image(i))
            card_layout.addWidget(del_btn)

            self.preview_layout.addWidget(card)

        self.preview_layout.addStretch(1)

    def _show_template_menu(self) -> None:
        """Deprecated compatibility stub."""
        pass

    def _apply_template(self, tpl: Any) -> None:
        """Deprecated compatibility stub."""
        pass

    def _on_reply_text_changed(self) -> None:
        text = self.reply_input.toPlainText()
        self.char_count_lbl.setText(f"{len(text)} / 500")

    def _on_zh_text_changed(self) -> None:
        self._on_reply_text_changed()

    def _do_translate(self) -> None:
        if getattr(self, "trans_worker", None) is not None and self.trans_worker.isRunning():
            return
        raw_text = self.reply_input.toPlainText().strip()
        if not raw_text:
            return
        if not self._current_session:
            return

        target_lang = self._current_session.target_language
        self.translate_action_btn.setEnabled(False)
        self.translate_action_btn.setText("翻译中...")

        self.trans_worker = TranslateWorker(raw_text, target_lang)
        self.trans_worker.finished.connect(self._on_translate_finished)
        self.trans_worker.error.connect(self._on_translate_error)
        self.trans_worker.start()

    def _on_translate_finished(self, translated: str) -> None:
        self.translate_action_btn.setEnabled(True)
        target_lang = self._current_session.target_language if self._current_session else "es"
        lang_name = "葡萄牙语" if target_lang == "pt" else "西班牙语"
        self.translate_action_btn.setText(f"⚡ 一键翻译为{lang_name} ({target_lang})")
        self.reply_input.setPlainText(translated)

    def _on_translate_error(self, err: str) -> None:
        self.translate_action_btn.setEnabled(True)
        self.translate_action_btn.setText("翻译失败，点击重试")
        logger.warning("Translation error: %s", err)

    # ------------------------------------------------------------------
    # Send Reply Action with Double Confirmation
    # ------------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        if getattr(self, "send_worker", None) is not None and self.send_worker.isRunning():
            return
        if not self._current_session:
            return

        final_text = self.reply_input.toPlainText().strip()
        if not final_text:
            QMessageBox.warning(self, "发送被阻止", "回复内容不能为空！请输入回复文本。")
            return

        # Double confirmation dialog
        target_lang = self._current_session.target_language
        lang_desc = "葡萄牙语" if target_lang == "pt" else "西班牙语"

        is_claim_dispute = (self._current_session.session_type == "claim") and (
            (self._current_session.stage == "dispute")
            or any(m.sender_role == "mediator" for m in (self._current_session.messages or []))
        )
        if is_claim_dispute:
            target_party = "美客多平台官方调解员"
            note = "注意: 索赔当前由美客多官方介入仲裁，回复将直接提交给官方调解员审核。"
        elif self._current_session.session_type == "question":
            target_party = f"买家【{self._current_session.buyer_nickname}】"
            note = "注意: 美客多平台售前咨询一旦回复不可撤回或修改。"
        else:
            target_party = f"买家【{self._current_session.buyer_nickname}】"
            note = "注意: 售后私信回复将发送至买家订单沟通专区。"

        has_chinese = any('\u4e00' <= char <= '\u9fff' for char in final_text)
        if has_chinese:
            # Offer auto-translation to Spanish / Portuguese
            auto_trans = ""
            try:
                auto_trans = translate_text(final_text, target_lang=target_lang, timeout=3.5)
            except Exception:
                auto_trans = ""

            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("确认发送回复")
            msg_box.setIcon(QMessageBox.Icon.Question)
            if auto_trans and auto_trans != final_text:
                msg_box.setText(f"确定向【{target_party}】发送回复吗？\n\n检测到输入包含中文，建议以{lang_desc}发送，以便拉美官方调解员/买家准确理解。")
                msg_box.setInformativeText(f"【中文原文】:\n{final_text}\n\n【{lang_desc}译文】:\n{auto_trans}\n\n{note}")
                btn_trans = msg_box.addButton(f"以{lang_desc}发送 (推荐)", QMessageBox.ButtonRole.AcceptRole)
                btn_orig = msg_box.addButton("按中文原文发送", QMessageBox.ButtonRole.RejectRole)
                btn_cancel = msg_box.addButton("取消", QMessageBox.ButtonRole.DestructiveRole)
                msg_box.setDefaultButton(btn_trans)
                msg_box.exec()
                clicked = msg_box.clickedButton()
                if clicked == btn_cancel or clicked is None:
                    return
                elif clicked == btn_trans:
                    final_text = auto_trans
                    self.reply_input.setPlainText(final_text)
            else:
                prompt = (
                    f"确定向【{target_party}】发送以下内容吗？\n\n"
                    f"【内容预览】:\n{final_text}\n\n"
                    f"{note}"
                )
                reply = QMessageBox.question(
                    self,
                    "确认发送回复",
                    prompt,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
        else:
            prompt = (
                f"确定向【{target_party}】发送以下{lang_desc}回复吗？\n\n"
                f"【内容预览】:\n{final_text}\n\n"
                f"{note}"
            )
            reply = QMessageBox.question(
                self,
                "确认发送回复",
                prompt,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self.send_btn.setEnabled(False)
        self.send_btn.setText("发送中...")

        self.send_worker = SendReplyWorker(
            self.client,
            self._current_session,
            final_text,
            image_paths=self.selected_images,
        )
        self.send_worker.finished.connect(self._on_send_finished)
        self.send_worker.start()

    def _on_send_finished(self, success: bool, msg: str) -> None:
        self.send_btn.setEnabled(True)
        self.send_btn.setText("确认并发送回复 (Ctrl+Enter)")

        if success:
            QToolTip.showText(QCursor.pos(), "✅ 回复已发送", self)
            # Add seller message bubble locally
            if self._current_session:
                seller_msg = ServiceMessage(
                    id=f"rep_{datetime.now().timestamp()}",
                    sender_role="seller",
                    sender_name="卖家",
                    text=self.reply_input.toPlainText().strip(),
                    attachments=list(self.selected_images),
                    created_at=datetime.now().isoformat(),
                    is_seller=True,
                    is_read=False,
                )
                self._current_session.messages.append(seller_msg)
                bubble = self._create_chat_bubble(seller_msg)
                self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)

                # If question, it can only be answered once
                if self._current_session.session_type == "question":
                    self._current_session.can_reply = False
                    self._current_session.status = "answered"
                    self._current_session.is_unread = False
                    self._current_session.blocked_reason = "该提问已回复（美客多限制仅能回复一次）"
                    self.reply_dock.setEnabled(False)
                    self.status_banner.setText(f"⚠️ {self._current_session.blocked_reason}")
                    self.status_banner.show()
                elif self._current_session.session_type == "message":
                    self._current_session.status = "unread"
                    self._current_session.is_unread = False

                self._update_tab_counts()
                self._apply_filter(preserve_selection=True)
                save_sessions_snapshot(self._all_sessions)

            self.selected_images.clear()
            self._refresh_image_previews()
            self.reply_input.clear()
        else:
            QMessageBox.critical(self, "发送失败", f"接口返回错误: {msg}")

    # ------------------------------------------------------------------
    # Sound Toggle & Real-time Webhook Event Handler
    # ------------------------------------------------------------------

    def _toggle_sound(self) -> None:
        self._sound_enabled = not self._sound_enabled
        if hasattr(self, "_qsettings"):
            self._qsettings.setValue("sound_enabled", self._sound_enabled)
        self.sound_btn.setText("🔔 提示音: 开" if self._sound_enabled else "🔕 提示音: 关")
        if self._sound_enabled:
            play_notification_chime()

    def _alert_new_session(self, sess: ServiceSession) -> None:
        """Trigger chime sound and toast notification for newly arrived unread session."""
        import time
        now = time.time()
        if len(self._recent_alert_ids) > 200:
            self._recent_alert_ids = {k: v for k, v in self._recent_alert_ids.items() if now - v < 60.0}

        if sess.id in self._recent_alert_ids:
            return
        self._recent_alert_ids[sess.id] = now

        if self._sound_enabled:
            play_notification_chime()

        if sess.session_type == "claim":
            title = "收到新索赔"
            site_tag = "订单索赔"
        elif sess.session_type == "message":
            title = "收到买家新私信"
            site_tag = "售后私信"
        else:
            title = "收到买家新提问"
            site_tag = "售前咨询"

        toast = ToastNotification(
            parent=self.window(),
            title=title,
            message=f"{sess.buyer_nickname}: {sess.summary[:45]}",
            store_name=sess.account_name,
            site_name=site_tag,
            session_id=sess.id,
            session_type=sess.session_type,
        )
        toast.clicked.connect(self._on_toast_clicked)
        toast.show_at_corner(self.window())

    def handle_incoming_webhook_event(self, event: dict[str, Any]) -> None:
        """Handle incoming real-time push from WebhookWorker."""
        raw_topic = str(event.get("topic") or "").strip().lower()
        cs_topics = (
            "questions", "marketplace_questions",
            "claims", "marketplace_claims",
            "messages", "marketplace_messages",
            "orders", "orders_v2", "marketplace_orders",
        )
        if raw_topic not in cs_topics:
            return

        resource = str(event.get("resource") or "")
        user_id = str(event.get("user_id") or "")
        app_id = str(event.get("application_id") or "")

        # Find matching store name
        store_name = resolve_store_name(self.auth, user_id)

        # Distinguish session type and title
        if "claim" in raw_topic:
            sess_type = "claim"
            title = "收到新索赔"
            site_label = "订单索赔"
        elif "message" in raw_topic or "order" in raw_topic:
            sess_type = "message"
            title = "收到买家新私信"
            site_label = "售后私信"
        else:
            sess_type = "question"
            title = "收到买家新提问"
            site_label = "售前咨询"

        # Deduplicate alert
        import time
        now = time.time()
        if resource and resource not in self._recent_alert_ids:
            self._recent_alert_ids[resource] = now
            if self._sound_enabled:
                play_notification_chime()

            toast = ToastNotification(
                parent=self.window(),
                title=title,
                message=f"资源: {resource} (主题: {raw_topic})",
                store_name=store_name,
                site_name=site_label,
                session_id=resource,
                session_type=sess_type,
            )
            toast.clicked.connect(self._on_toast_clicked)
            toast.show_at_corner(self.window())

        # Automatically refresh list silently
        self.reload_sessions(silent=True)

    def _on_toast_clicked(self, session_id: str, session_type: str) -> None:
        # Switch tab to matching type
        self._switch_tab(session_type)
        # Switch main window view to service center page if embedded
        win = self.window()
        if hasattr(win, "_show_page"):
            win._show_page(3)
        # Select session if present in list
        clean_res = session_id.split("/")[-1] if "/" in session_id else session_id
        for idx in range(self.session_list.count()):
            item = self.session_list.item(idx)
            val = str(item.data(Qt.ItemDataRole.UserRole))
            if session_id in val or (clean_res and clean_res in val):
                self.session_list.setCurrentRow(idx)
                break

    def _open_item_in_browser(self) -> None:
        """Open official product listing URL or seller order in user's default web browser."""
        if not self._current_session:
            return
        url = self._current_session.item_url
        if not url and self._current_session.order_id:
            url = f"https://myaccount.mercadolibre.com/orders/{self._current_session.order_id}"
        if url:
            try:
                if not QDesktopServices.openUrl(QUrl(url)):
                    webbrowser.open(url)
            except Exception:
                webbrowser.open(url)

    def _on_chip_item_clicked(self, event) -> None:
        """Left-click on item link chip reliably opens product listing in user's browser."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_item_in_browser()

    def _on_link_activated(self, link: str) -> None:
        """Reliably open rich-text clicked hyperlink in external browser."""
        if link:
            try:
                if not QDesktopServices.openUrl(QUrl(link)):
                    webbrowser.open(link)
            except Exception:
                webbrowser.open(link)

    def _on_context_thumb_clicked(self, event) -> None:
        """Enlarge top context product thumbnail in full-resolution lightbox."""
        if self._current_session and self._current_session.item_thumbnail:
            show_image_preview(self._current_session.item_thumbnail, title=self._current_session.item_title or "商品大图", parent=self)

    def _toggle_current_session_read_status(self) -> None:
        """Manually toggle unread/read state for currently active session."""
        if not self._current_session:
            return
        self._current_session.is_unread = not self._current_session.is_unread
        self.toggle_read_btn.setText("标为已读" if self._current_session.is_unread else "标为未读")

        curr_row = self.session_list.currentRow()
        if curr_row >= 0:
            curr_item = self.session_list.item(curr_row)
            if curr_item:
                card = self._create_session_item_widget(self._current_session)
                self.session_list.setItemWidget(curr_item, card)

        self._update_tab_counts()
        save_sessions_snapshot(self._all_sessions)
