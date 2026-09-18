from __future__ import annotations

from collections.abc import Iterable


WEBHOOK_CALLBACK_ORIGIN = "https://webhook.xingtupro1020.com"
DEFAULT_WEBHOOK_CALLBACK_URL = f"{WEBHOOK_CALLBACK_ORIGIN}/webhook/mercado-libre/"
DEFAULT_ACTIVITY_CALLBACK_CLAIM_URL = f"{WEBHOOK_CALLBACK_ORIGIN}/meli-callback/consumer/claim"
DEFAULT_ACTIVITY_CALLBACK_ACK_URL = f"{WEBHOOK_CALLBACK_ORIGIN}/meli-callback/consumer/ack"

LEGACY_ACTIVITY_CALLBACK_CLAIM_URLS = frozenset(
    {"https://xingtupro1020.com/meli-callback/consumer/claim"}
)
LEGACY_ACTIVITY_CALLBACK_ACK_URLS = frozenset(
    {"https://xingtupro1020.com/meli-callback/consumer/ack"}
)

DEFAULT_OAUTH_REDIRECT_URI = "https://xingtupro1020.com/oauth/callback/"
LEGACY_OAUTH_REDIRECT_URIS = frozenset({
    "https://xingtupro1020.com/callback/",
    "https://xingtupro1020.com/callback",
    "http://xingtupro1020.com/callback/",
    "http://xingtupro1020.com/callback",
})


def migrate_callback_endpoint(
    value: object,
    fallback: str,
    legacy_values: Iterable[str] = (),
) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate in legacy_values:
        return fallback
    return candidate


def migrate_oauth_redirect_uri(
    value: object,
    fallback: str = DEFAULT_OAUTH_REDIRECT_URI,
) -> str:
    candidate = str(value or "").strip()
    if not candidate or candidate in LEGACY_OAUTH_REDIRECT_URIS:
        return fallback
    return candidate

