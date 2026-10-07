from __future__ import annotations

from collections.abc import Iterable


WEBHOOK_CALLBACK_ORIGIN = ""
DEFAULT_WEBHOOK_CALLBACK_URL = ""
DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL = ""
DEFAULT_ACTIVITY_CALLBACK_ACK_URL = ""

LEGACY_ACTIVITY_CALLBACK_CLAIM_URLS = frozenset({
    "https://xingtupro1020.com/meli-callback/consumer/claim",
    "https://webhook.xingtupro1020.com/meli-callback/consumer/claim",
})
LEGACY_ACTIVITY_CALLBACK_ACK_URLS = frozenset({
    "https://xingtupro1020.com/meli-callback/consumer/ack",
    "https://webhook.xingtupro1020.com/meli-callback/consumer/ack",
})

DEFAULT_OAUTH_REDIRECT_URI = ""
LEGACY_OAUTH_REDIRECT_URIS = frozenset({
    "https://xingtupro1020.com/oauth/callback/",
    "https://xingtupro1020.com/callback/",
    "https://xingtupro1020.com/callback",
    "http://xingtupro1020.com/callback/",
    "http://xingtupro1020.com/callback",
})


def migrate_callback_endpoint(
    value: object,
    fallback: str = "",
    legacy_values: Iterable[str] = (),
) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate in legacy_values or "xingtupro1020.com" in candidate:
        return fallback
    return candidate


def migrate_oauth_redirect_uri(
    value: object,
    fallback: str = DEFAULT_OAUTH_REDIRECT_URI,
) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate in LEGACY_OAUTH_REDIRECT_URIS or "xingtupro1020.com" in candidate:
        return fallback
    return candidate

