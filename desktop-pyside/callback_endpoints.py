from __future__ import annotations

from collections.abc import Iterable


WEBHOOK_CALLBACK_ORIGIN = ""
DEFAULT_WEBHOOK_CALLBACK_URL = ""
DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL = ""
DEFAULT_ACTIVITY_CALLBACK_ACK_URL = ""

LEGACY_ACTIVITY_CALLBACK_CLAIM_URLS = frozenset()
LEGACY_ACTIVITY_CALLBACK_ACK_URLS = frozenset()

DEFAULT_OAUTH_REDIRECT_URI = ""
LEGACY_OAUTH_REDIRECT_URIS = frozenset()


def migrate_callback_endpoint(
    value: object,
    fallback: str = "",
    legacy_values: Iterable[str] = (),
) -> str:
    candidate = str(value or "").strip()
    return candidate or fallback


def migrate_oauth_redirect_uri(
    value: object,
    fallback: str = DEFAULT_OAUTH_REDIRECT_URI,
) -> str:
    candidate = str(value or "").strip()
    return candidate or fallback
