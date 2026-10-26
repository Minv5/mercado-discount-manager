from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import uuid


def get_machine_id() -> str:
    """Retrieve a unique, stable hardware machine identifier formatted as MDM-XXXX-XXXX-XXXX-XXXX."""
    raw_id = ""
    system = platform.system().lower()

    if system == "darwin":
        try:
            out = subprocess.check_output(["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], text=True)
            for line in out.splitlines():
                if "IOPlatformUUID" in line:
                    raw_id = line.split("=")[-1].strip().replace('"', "")
                    break
        except Exception:
            pass
    elif system == "windows":
        try:
            out = subprocess.check_output("wmic csproduct get uuid", shell=True, text=True)
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            if len(lines) > 1 and lines[1].lower() != "uuid":
                raw_id = lines[1]
        except Exception:
            pass
        if not raw_id:
            try:
                out = subprocess.check_output(
                    ["powershell", "-NoProfile", "-Command", "(Get-CimInstance -Class Win32_ComputerSystemProduct).UUID"],
                    text=True,
                )
                raw_id = out.strip()
            except Exception:
                pass
    elif system == "linux":
        for p in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        raw_id = f.read().strip()
                        if raw_id:
                            break
                except Exception:
                    pass

    if not raw_id or len(raw_id) < 5:
        raw_id = f"{uuid.getnode()}_{platform.node()}"

    digest = hashlib.sha256(raw_id.strip().encode("utf-8")).hexdigest().upper()
    return f"MDM-{digest[0:4]}-{digest[4:8]}-{digest[8:12]}-{digest[12:16]}"
