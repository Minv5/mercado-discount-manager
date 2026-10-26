from __future__ import annotations

import html
import json
import logging
import re
import ssl
import urllib.parse
import urllib.request
from typing import Literal

logger = logging.getLogger(__name__)

_TRANSLATION_CACHE: dict[tuple[str, str, str], str] = {}

_ERROR_SIGNATURES = (
    "QUERY LENGTH LIMIT EXCEEDED",
    "MYMEMORY WARNING",
    "INVALID TARGET LANGUAGE",
    "INVALID SOURCE LANGUAGE",
    "NO QUERY SPECIFIED",
    "TOO MANY REQUESTS",
    "DAILY LIMIT REACHED",
    "HTTP ERROR",
    "PLEASE SELECT TWO DISTINCT LANGUAGES",
)


def is_error_translation(translated: str) -> bool:
    """Check if the translated string contains API error responses or warnings."""
    if not translated or not translated.strip():
        return True
    upper = translated.strip().upper()
    return any(sig in upper for sig in _ERROR_SIGNATURES)


def _detect_source_lang(text: str) -> str:
    """Heuristic detector for source language (Chinese vs Portuguese vs Spanish)."""
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        return "zh-CN"
    lower = text.lower()
    # Distinct Portuguese diacritics and common words
    if any(c in lower for c in ("ã", "õ", "ç", "ê")):
        return "pt"
    pt_tokens = {"não", "voce", "você", "tambem", "também", "obrigado", "obrigada", "espelho", "quero", "chegou", "devolver"}
    words = set(re.findall(r"\b\w+\b", lower))
    if words & pt_tokens:
        return "pt"
    # Default to Spanish for other Latin American countries (MLM, MLC, MCO, MLA, etc.)
    return "es"


def _split_into_chunks(text: str, max_chars: int = 300) -> list[str]:
    """Split text into smaller chunks by paragraphs or punctuation for safe API translation."""
    text = text.strip()
    if len(text) <= max_chars and "\n" not in text:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n")
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
            curr = ""
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if len(curr) + len(s) + 1 <= max_chars:
                    curr = f"{curr} {s}".strip() if curr else s
                else:
                    if curr:
                        chunks.append(curr)
                    if len(s) > max_chars:
                        for i in range(0, len(s), max_chars):
                            sub = s[i : i + max_chars].strip()
                            if sub:
                                chunks.append(sub)
                        curr = ""
                    else:
                        curr = s
            if curr:
                chunks.append(curr)

    return chunks if chunks else [text]


def _translate_single_chunk(
    chunk: str,
    target_lang: str,
    source_lang: str,
    timeout: float,
) -> str:
    """Translate a small single text chunk (< 350 chars) with fallback.

    Returns the clean translated string if successful, or an empty string '' on failure.
    NEVER returns the raw source text when translation fails.
    """
    clean_chunk = chunk.strip()
    if not clean_chunk:
        return ""

    cache_key = (clean_chunk, source_lang, target_lang)
    if cache_key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[cache_key]

    target_code = "zh-CN" if target_lang == "zh" else target_lang
    source_code = source_lang

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # 1. Primary: Google Translate single endpoint via dict-chrome-ex client
    try:
        encoded = urllib.parse.quote(clean_chunk)
        url = f"https://translate.googleapis.com/translate_a/single?client=dict-chrome-ex&sl={source_code}&tl={target_code}&dt=t&q={encoded}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=min(timeout, 3.0), context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data and isinstance(data[0], list):
                result = "".join([part[0] for part in data[0] if isinstance(part, list) and len(part) > 0 and part[0]])
                clean_result = result.strip()
                if clean_result and not is_error_translation(clean_result):
                    if clean_result.lower() != clean_chunk.lower():
                        _TRANSLATION_CACHE[cache_key] = clean_result
                        return clean_result
    except Exception as err:
        logger.debug("Primary Google translation failed: %s, trying MyMemory fallback", err)

    # 2. Secondary: MyMemory API fallback with intelligent source language pair
    try:
        resolved_src = _detect_source_lang(clean_chunk) if source_code == "auto" else source_code
        if resolved_src == target_code or (resolved_src.startswith("zh") and target_code.startswith("zh")):
            return ""
        pair = f"{resolved_src}|{target_code}"
        encoded = urllib.parse.quote(clean_chunk)
        url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair={pair}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MercadoDiscountManager/2.0)"})
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_trans = data.get("responseData", {}).get("translatedText")
            if raw_trans and raw_trans.strip() and not is_error_translation(raw_trans):
                unescaped = html.unescape(raw_trans.strip())
                if unescaped.lower() != clean_chunk.lower():
                    _TRANSLATION_CACHE[cache_key] = unescaped
                    return unescaped
    except Exception as err:
        logger.debug("Secondary MyMemory translation failed: %s", err)

    # All translation providers failed -> return empty string to indicate no translation
    return ""


def translate_text(
    text: str,
    target_lang: Literal["es", "pt", "en", "zh"] = "es",
    source_lang: str = "auto",
    timeout: float = 6.0,
) -> str:
    """Translate text to target language with intelligent chunking and multi-provider fallback.

    Returns translated text if successful, or '' if translation failed.
    """
    clean_text = str(text or "").strip()
    if not clean_text:
        return ""

    cache_key = (clean_text, source_lang, target_lang)
    if cache_key in _TRANSLATION_CACHE:
        cached_val = _TRANSLATION_CACHE[cache_key]
        if not is_error_translation(cached_val):
            return cached_val
        _TRANSLATION_CACHE.pop(cache_key, None)

    chunks = _split_into_chunks(clean_text, max_chars=300)
    if len(chunks) == 1:
        res = _translate_single_chunk(clean_text, target_lang, source_lang, timeout)
        if res and not is_error_translation(res) and res.strip().lower() != clean_text.lower():
            _TRANSLATION_CACHE[cache_key] = res
            return res
        return ""

    translated_chunks: list[str] = []
    per_chunk_timeout = max(3.0, min(timeout, 8.0))
    for c in chunks:
        trans_c = _translate_single_chunk(c, target_lang, source_lang, per_chunk_timeout)
        if trans_c:
            translated_chunks.append(trans_c)
        else:
            return ""

    final_result = "\n".join(translated_chunks).strip()
    if final_result and not is_error_translation(final_result) and final_result.lower() != clean_text.lower():
        _TRANSLATION_CACHE[cache_key] = final_result
        return final_result

    return ""
