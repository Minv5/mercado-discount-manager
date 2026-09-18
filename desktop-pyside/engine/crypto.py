from __future__ import annotations

import base64
import hashlib
import os
import secrets
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def get_data_dir() -> Path:
    if "MDM_DATA_DIR" in os.environ:
        return Path(os.environ["MDM_DATA_DIR"])
    if os.name == "posix":
        import sys
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path.home() / ".local" / "share"
    else:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return base / "MercadoDiscountManagerStandalone" / "data"


def get_key_path() -> Path:
    if "MDM_KEY_PATH" in os.environ:
        return Path(os.environ["MDM_KEY_PATH"])
    return get_data_dir() / "local.key"


def get_encryption_key() -> bytes:
    master = os.environ.get("MDM_MASTER_KEY")
    if master:
        return hashlib.sha256(master.encode("utf-8")).digest()
    key_file = get_key_path()
    if not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        raw_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        key_file.write_text(raw_b64, encoding="utf-8")
    raw = key_file.read_text("utf-8").strip()
    return hashlib.sha256(raw.encode("utf-8")).digest()


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    key = get_encryption_key()
    aesgcm = AESGCM(key)
    iv = secrets.token_bytes(12)
    # AESGCM.encrypt returns ciphertext + 16-byte tag
    ct_and_tag = aesgcm.encrypt(iv, value.encode("utf-8"), None)
    ct = ct_and_tag[:-16]
    tag = ct_and_tag[-16:]
    iv_b64 = base64.b64encode(iv).decode("ascii")
    tag_b64 = base64.b64encode(tag).decode("ascii")
    ct_b64 = base64.b64encode(ct).decode("ascii")
    return f"v1:{iv_b64}:{tag_b64}:{ct_b64}"


def decrypt_secret(payload: str | None) -> str | None:
    if not payload:
        return None
    parts = str(payload).split(":")
    if len(parts) != 4 or parts[0] != "v1":
        raise ValueError(f"不支持的本地加密格式: {payload[:20] if payload else ''}")
    iv = base64.b64decode(parts[1])
    tag = base64.b64decode(parts[2])
    ct = base64.b64decode(parts[3])
    key = get_encryption_key()
    aesgcm = AESGCM(key)
    # AESGCM.decrypt expects ct + tag
    plaintext = aesgcm.decrypt(iv, ct + tag, None)
    return plaintext.decode("utf-8")


def create_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def create_state() -> str:
    return secrets.token_urlsafe(24)
