from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .crypto import verify_license_code
from .machine import get_machine_id
from .storage import get_trial_status, load_license, save_license

logger = logging.getLogger(__name__)

# Developer / Owner machine exemptions (permanently authorized)
DEVELOPER_MACHINE_IDS = {
    "MDM-6F57-29F3-CEB8-2BC5",  # Current developer workstation
}


class LicenseStatus(str, Enum):
    LICENSED = "LICENSED"      # Valid permanent or commercial license
    TRIAL = "TRIAL"            # Within 3 days free trial
    EXPIRED = "EXPIRED"        # Trial expired or license expired


@dataclass
class LicenseInfo:
    status: LicenseStatus
    is_valid: bool
    is_trial: bool
    is_expired: bool
    is_developer: bool
    machine_id: str
    license_type: str
    expiration_str: str
    remaining_days: int
    remaining_hours: int
    customer_name: str
    message: str


class LicenseManager:
    """Central licensing and copy protection coordinator."""

    _instance: LicenseManager | None = None

    def __init__(self) -> None:
        self._machine_id = get_machine_id()

    @classmethod
    def get_instance(cls) -> LicenseManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def machine_id(self) -> str:
        return self._machine_id

    def check_license(self) -> LicenseInfo:
        """Inspect current machine license state."""
        mid = self._machine_id

        # 1. Developer machine auto-exemption
        if mid.upper() in DEVELOPER_MACHINE_IDS:
            return LicenseInfo(
                status=LicenseStatus.LICENSED,
                is_valid=True,
                is_trial=False,
                is_expired=False,
                is_developer=True,
                machine_id=mid,
                license_type="开发者永久版",
                expiration_str="终身永久授权",
                remaining_days=99999,
                remaining_hours=99999,
                customer_name="开发者专属",
                message="当前设备已获开发者终身永久授权",
            )

        # 2. Check saved formal license
        code = load_license()
        if code:
            payload = verify_license_code(code, mid)
            if payload:
                if payload.get("expired"):
                    exp_date = str(payload.get("exp_date") or payload.get("exp") or "")
                    return LicenseInfo(
                        status=LicenseStatus.EXPIRED,
                        is_valid=False,
                        is_trial=False,
                        is_expired=True,
                        is_developer=False,
                        machine_id=mid,
                        license_type="商业授权版",
                        expiration_str=f"已于 {exp_date} 到期",
                        remaining_days=0,
                        remaining_hours=0,
                        customer_name=str(payload.get("name") or "正式用户"),
                        message="您的商业授权已到期，请联系客服续费激活。",
                    )

                exp_str = str(payload.get("exp") or "PERMANENT").upper()
                cname = str(payload.get("name") or "正式用户")
                ltype = "商业永久版" if exp_str == "PERMANENT" else "商业授权版"

                rem_days = 99999
                if exp_str != "PERMANENT":
                    try:
                        ed = datetime.strptime(exp_str, "%Y-%m-%d").date()
                        rem_days = max(0, (ed - datetime.now().date()).days)
                    except Exception:
                        pass

                return LicenseInfo(
                    status=LicenseStatus.LICENSED,
                    is_valid=True,
                    is_trial=False,
                    is_expired=False,
                    is_developer=False,
                    machine_id=mid,
                    license_type=ltype,
                    expiration_str="终身永久" if exp_str == "PERMANENT" else exp_str,
                    remaining_days=rem_days,
                    remaining_hours=rem_days * 24,
                    customer_name=cname,
                    message="正式商业授权有效",
                )

        # 3. Check 3-day free trial (Option B)
        trial = get_trial_status(mid)
        if trial.get("is_expired"):
            return LicenseInfo(
                status=LicenseStatus.EXPIRED,
                is_valid=False,
                is_trial=True,
                is_expired=True,
                is_developer=False,
                machine_id=mid,
                license_type="免费试用版",
                expiration_str="试用期已结束",
                remaining_days=0,
                remaining_hours=0,
                customer_name="试用用户",
                message="3 天免费试用期已结束，请联系客服获取正式激活码以继续使用。",
            )

        rem_days = int(trial.get("remaining_days") or 0)
        rem_hours = int(trial.get("remaining_hours") or 0)
        return LicenseInfo(
            status=LicenseStatus.TRIAL,
            is_valid=True,
            is_trial=True,
            is_expired=False,
            is_developer=False,
            machine_id=mid,
            license_type="3天免费试用版",
            expiration_str=f"剩余 {rem_days} 天 ({rem_hours} 小时)",
            remaining_days=rem_days,
            remaining_hours=rem_hours,
            customer_name="试用用户",
            message=f"当前处于 3 天免费试用期，剩余 {rem_hours} 小时。",
        )

    def activate(self, code: str) -> tuple[bool, str]:
        """Validate and apply an activation code."""
        clean_code = code.strip().replace("\r", "").replace("\n", "").replace(" ", "")
        if not clean_code:
            return False, "请输入有效的激活码。"

        payload = verify_license_code(clean_code, self._machine_id)
        if not payload:
            return False, "激活码无效，或与当前电脑的机器码不匹配，请核对后重试。"

        if payload.get("expired"):
            return False, f"该激活码已于 {payload.get('exp_date')} 过期，请联系客服获取最新有效激活码。"

        # Save valid license
        save_license(clean_code)
        exp_str = str(payload.get("exp") or "PERMANENT").upper()
        disp_exp = "终身永久版" if exp_str == "PERMANENT" else f"有效期至 {exp_str}"
        return True, f"激活成功！已成功解锁【{disp_exp}】商业授权。"
