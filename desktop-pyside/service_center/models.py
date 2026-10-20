from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


SITE_NAMES = {
    "MLB": "巴西",
    "MLM": "墨西哥",
    "MLC": "智利",
    "MCO": "哥伦比亚",
    "MLA": "阿根廷",
    "MLU": "乌拉圭",
    "MPE": "秘鲁",
}

SITE_LANGUAGES = {
    "MLB": "pt",
    "MLM": "es",
    "MLC": "es",
    "MCO": "es",
    "MLA": "es",
    "MLU": "es",
    "MPE": "es",
}


def resolve_site_language(site_id: str) -> str:
    return SITE_LANGUAGES.get(site_id, "es")

SITE_FLAGS = {
    "MLB": "🇧🇷",
    "MLM": "🇲🇽",
    "MLC": "🇨🇱",
    "MCO": "🇨🇴",
    "MLA": "🇦🇷",
    "MLU": "🇺🇾",
    "MPE": "🇵🇪",
}


@dataclass
class ServiceMessage:
    id: str
    sender_role: str  # "buyer" | "seller" | "mediator" | "complainant" | "respondent"
    sender_name: str
    text: str
    text_translated: str = ""
    created_at: str = ""
    attachments: list[str] = field(default_factory=list)
    stage: str = ""
    is_seller: bool = False
    is_read: bool = False
    read_date: str = ""


@dataclass
class ServiceSession:
    id: str
    session_type: str  # "question" | "claim" | "message"
    account_id: str
    account_name: str
    site_id: str
    child_user_id: str = ""
    buyer_id: str = ""
    buyer_nickname: str = ""
    item_id: str = ""
    item_title: str = ""
    item_thumbnail: str = ""
    item_price: str = ""
    item_permalink: str = ""
    sku: str = ""
    variation_id: str = ""
    order_id: str = ""
    pack_id: str = ""
    status: str = ""
    stage: str = ""
    created_at: str = ""
    updated_at: str = ""
    summary: str = ""
    summary_translated: str = ""
    deadline: str | None = None
    can_reply: bool = True
    blocked_reason: str | None = None
    is_unread: bool = False
    affects_reputation: str = ""  # "not_affected" | "affected" | ""
    messages: list[ServiceMessage] = field(default_factory=list)
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def affects_reputation_display(self) -> str:
        if self.affects_reputation == "affected":
            return "是"
        elif self.affects_reputation == "not_affected":
            return "否"
        return ""

    @property
    def site_display(self) -> str:
        flag = SITE_FLAGS.get(self.site_id, "🌐")
        name = SITE_NAMES.get(self.site_id, self.site_id)
        return f"{flag} {name}"

    @property
    def target_language(self) -> str:
        return SITE_LANGUAGES.get(self.site_id, "es")

    @property
    def item_url(self) -> str:
        return get_item_url(self.site_id, self.item_id, self.item_permalink)

    @property
    def is_pending(self) -> bool:
        """Whether this session requires seller action (pending/to-be-handled)."""
        if self.session_type == "question":
            st = (self.status or "").lower()
            return st == "unanswered" or (self.can_reply and st != "answered")
        elif self.session_type == "claim":
            return (self.status or "").lower() == "opened"
        elif self.session_type == "message":
            return bool(self.is_unread)
        return False


def get_item_url(site_id: str, item_id: str, permalink: str = "") -> str:
    """Resolve clickable official product URL for default browser opening."""
    if permalink and permalink.startswith("http"):
        return permalink
    s = str(site_id or "MLB").upper()
    clean_id = str(item_id or "").strip()
    if not clean_id:
        return ""
    if len(clean_id) > 3 and clean_id[:3].isalpha() and clean_id[3] != "-":
        formatted_id = f"{clean_id[:3]}-{clean_id[3:]}"
    else:
        formatted_id = clean_id
    domains = {
        "MLB": "produto.mercadolivre.com.br",
        "MLM": "articulo.mercadolibre.com.mx",
        "MLC": "articulo.mercadolibre.cl",
        "MCO": "articulo.mercadolibre.com.co",
        "MLA": "articulo.mercadolibre.com.ar",
        "MLU": "articulo.mercadolibre.com.uy",
        "MPE": "articulo.mercadolibre.com.pe",
    }
    domain = domains.get(s, "articulo.mercadolibre.com")
    return f"https://{domain}/{formatted_id}"
