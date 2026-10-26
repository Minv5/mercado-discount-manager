from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from engine.crypto import get_data_dir

logger = logging.getLogger(__name__)

TRIAL_DURATION_DAYS = 3
TRIAL_SALT = "MDM_SECURE_TRIAL_PROTECTION_2026_@!"


def _license_file() -> Path:
    d = get_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "license.json"


def _trial_file() -> Path:
    d = get_data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / ".trial_checkpoint.dat"


def save_license(code: str) -> None:
    """Save raw license code to storage."""
    try:
        p = _license_file()
        p.write_text(json.dumps({"code": code.strip(), "saved_at": datetime.now().isoformat()}, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("Failed to save license: %s", e)


def load_license() -> str:
    """Load raw license code from storage if present."""
    try:
        p = _license_file()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return str(data.get("code") or "").strip()
    except Exception as e:
        logger.debug("Failed to load license: %s", e)
    return ""


def clear_license() -> None:
    """Clear local license file."""
    try:
        p = _license_file()
        if p.exists():
            p.unlink()
    except Exception as e:
        logger.debug("Failed to clear license: %s", e)


def _compute_checksum(first_run_iso: str, mid: str) -> str:
    raw = f"{first_run_iso}:{mid}:{TRIAL_SALT}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_trial_status(mid: str) -> dict[str, Any]:
    """Check trial status (Option B: 3 days free trial).

    Returns:
    {
        "is_trial": bool,
        "is_expired": bool,
        "remaining_seconds": int,
        "remaining_days": int,
        "remaining_hours": int,
        "first_run": datetime,
    }
    """
    p = _trial_file()
    now = datetime.now()

    if not p.exists():
        # First time opening the application! Initialize trial checkpoint
        first_run_iso = now.isoformat()
        checksum = _compute_checksum(first_run_iso, mid)
        data = {
            "mid": mid,
            "first_run": first_run_iso,
            "last_run": now.isoformat(),
            "checksum": checksum,
        }
        try:
            p.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to initialize trial file: %s", e)

        total_secs = TRIAL_DURATION_DAYS * 86400
        return {
            "is_trial": True,
            "is_expired": False,
            "remaining_seconds": total_secs,
            "remaining_days": TRIAL_DURATION_DAYS,
            "remaining_hours": TRIAL_DURATION_DAYS * 24,
            "first_run": now,
        }

    # Trial file exists, verify integrity
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        saved_mid = str(data.get("mid") or "")
        first_run_str = str(data.get("first_run") or "")
        last_run_str = str(data.get("last_run") or "")
        saved_checksum = str(data.get("checksum") or "")

        # Verify checksum and mid match
        if saved_mid != mid or saved_checksum != _compute_checksum(first_run_str, mid):
            logger.warning("Trial checkpoint tampering detected! Forcing expiration.")
            return {
                "is_trial": True,
                "is_expired": True,
                "remaining_seconds": 0,
                "remaining_days": 0,
                "remaining_hours": 0,
                "first_run": now,
            }

        first_run = datetime.fromisoformat(first_run_str)
        last_run = datetime.fromisoformat(last_run_str)

        # Detect clock rollback tampering
        if now < last_run - timedelta(minutes=5):
            logger.warning("System clock rollback detected! Forcing trial expiration.")
            return {
                "is_trial": True,
                "is_expired": True,
                "remaining_seconds": 0,
                "remaining_days": 0,
                "remaining_hours": 0,
                "first_run": first_run,
            }

        # Update last run timestamp
        data["last_run"] = now.isoformat()
        p.write_text(json.dumps(data), encoding="utf-8")

        elapsed = now - first_run
        trial_delta = timedelta(days=TRIAL_DURATION_DAYS)

        if elapsed >= trial_delta:
            return {
                "is_trial": True,
                "is_expired": True,
                "remaining_seconds": 0,
                "remaining_days": 0,
                "remaining_hours": 0,
                "first_run": first_run,
            }

        remaining = trial_delta - elapsed
        remaining_seconds = max(0, int(remaining.total_seconds()))
        remaining_days = remaining.days
        remaining_hours = int(remaining_seconds // 3600)

        return {
            "is_trial": True,
            "is_expired": False,
            "remaining_seconds": remaining_seconds,
            "remaining_days": remaining_days,
            "remaining_hours": remaining_hours,
            "first_run": first_run,
        }
    except Exception as e:
        logger.error("Error reading trial checkpoint: %s", e)
        return {
            "is_trial": True,
            "is_expired": True,
            "remaining_seconds": 0,
            "remaining_days": 0,
            "remaining_hours": 0,
            "first_run": now,
        }
