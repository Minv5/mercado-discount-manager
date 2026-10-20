from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Literal

logger = logging.getLogger(__name__)

_TRANSLATION_CACHE: dict[tuple[str, str, str], str] = {}


def translate_text(
    text: str,
    target_lang: Literal["es", "pt", "en", "zh"] = "es",
    source_lang: str = "auto",
    timeout: float = 6.0,
) -> str:
    """Translate text to target language (es for Spanish, pt for Portuguese).
    
    Uses high-availability translation with multi-provider fallback.
    """
    clean_text = str(text or "").strip()
    if not clean_text:
        return ""

    cache_key = (clean_text, source_lang, target_lang)
    if cache_key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[cache_key]

    # 1. Primary: Google Translate single endpoint
    try:
        encoded = urllib.parse.quote(clean_text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t&q={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data and isinstance(data[0], list):
                result = "".join([part[0] for part in data[0] if isinstance(part, list) and len(part) > 0 and part[0]])
                if result.strip():
                    _TRANSLATION_CACHE[cache_key] = result.strip()
                    return result.strip()
    except Exception as err:
        logger.warning("Primary translation failed: %s, trying fallback", err)

    # 2. Secondary: MyMemory API fallback
    try:
        pair = f"zh-CN|{target_lang}" if source_lang == "auto" else f"{source_lang}|{target_lang}"
        encoded = urllib.parse.quote(clean_text)
        url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair={pair}"
        req = urllib.request.Request(url, headers={"User-Agent": "MercadoDiscountManager/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            translated = data.get("responseData", {}).get("translatedText")
            if translated and translated.strip():
                _TRANSLATION_CACHE[cache_key] = translated.strip()
                return translated.strip()
    except Exception as err:
        logger.warning("Secondary translation failed: %s", err)

    # Return original text if offline
    return clean_text
