from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
PROJ_ROOT = ROOT_DIR.parent
if str(PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJ_ROOT))

from license.crypto import verify_license_code
from license.machine import get_machine_id
from license.manager import LicenseManager, LicenseStatus
from license.storage import (
    TRIAL_DURATION_DAYS,
    _compute_checksum,
    clear_license,
    get_trial_status,
    load_license,
    save_license,
)
from tools.license_generator import generate_license_code


class TestLicenseSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import license.crypto as crypto

        priv_path = PROJ_ROOT / "tools" / "license_private_key.pem"
        cls.temp_key_dir = None
        cls.priv_patcher = None
        cls.pub_patcher = None

        if not priv_path.exists():
            cls.temp_key_dir = tempfile.mkdtemp()
            temp_priv = Path(cls.temp_key_dir) / "license_private_key.pem"
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            priv_pem = key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
            pub_pem = key.public_key().public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode("utf-8")
            temp_priv.write_bytes(priv_pem)

            cls.priv_patcher = patch("tools.license_generator.get_private_key_path", return_value=temp_priv)
            cls.priv_patcher.start()

            cls.pub_patcher = patch.object(crypto, "PUBLIC_KEY_PEM", pub_pem)
            cls.pub_patcher.start()

    @classmethod
    def tearDownClass(cls):
        if cls.priv_patcher:
            cls.priv_patcher.stop()
        if cls.pub_patcher:
            cls.pub_patcher.stop()
        if cls.temp_key_dir:
            shutil.rmtree(cls.temp_key_dir, ignore_errors=True)

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.patcher = patch("license.storage.get_data_dir", return_value=Path(self.test_dir))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_machine_id_format(self):
        mid = get_machine_id()
        self.assertTrue(mid.startswith("MDM-"))
        parts = mid.split("-")
        self.assertEqual(len(parts), 5)
        for part in parts[1:]:
            self.assertEqual(len(part), 4)

    def test_generate_and_verify_commercial_license(self):
        mid = "MDM-TEST-AAAA-BBBB-CCCC"
        code = generate_license_code(mid, days=30, customer_name="张三商贸")
        self.assertIn(".", code)

        # Verify correct match
        payload = verify_license_code(code, mid)
        self.assertIsNotNone(payload)
        self.assertFalse(payload["expired"])
        self.assertEqual(payload["mid"], mid)
        self.assertEqual(payload["name"], "张三商贸")

        # Verify wrong machine ID is rejected
        mismatch = verify_license_code(code, "MDM-WRONG-1111-2222-3333")
        self.assertIsNone(mismatch)

    def test_permanent_license(self):
        mid = "MDM-PERM-1234-5678-90AB"
        code = generate_license_code(mid, is_permanent=True, customer_name="终身VIP")
        payload = verify_license_code(code, mid)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["exp"], "PERMANENT")
        self.assertFalse(payload["expired"])

    def test_expired_license(self):
        mid = "MDM-EXPD-1234-5678-90AB"
        # Generate with past date
        past_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        code = generate_license_code(mid, custom_date_str=past_date)
        payload = verify_license_code(code, mid)
        self.assertIsNotNone(payload)
        self.assertTrue(payload["expired"])

    def test_tampered_license_signature(self):
        mid = "MDM-TEST-AAAA-BBBB-CCCC"
        code = generate_license_code(mid, days=30)
        p, s = code.split(".", 1)
        # Tamper payload
        tampered_code = p[:-2] + "==" + "." + s
        self.assertIsNone(verify_license_code(tampered_code, mid))

    def test_trial_flow_and_clock_rollback(self):
        mid = "MDM-TRIAL-0001-0002-0003"
        # First run: trial starts
        st = get_trial_status(mid)
        self.assertTrue(st["is_trial"])
        self.assertFalse(st["is_expired"])
        self.assertEqual(st["remaining_days"], TRIAL_DURATION_DAYS)

        # Second run: still within trial
        st2 = get_trial_status(mid)
        self.assertFalse(st2["is_expired"])

        # Tampering: simulate clock rollback into past
        p = Path(self.test_dir) / ".trial_checkpoint.dat"
        import json
        data = json.loads(p.read_text())
        future_time = (datetime.now() + timedelta(days=10)).isoformat()
        data["last_run"] = future_time
        p.write_text(json.dumps(data))

        # Next check should detect rollback and force expiration
        st3 = get_trial_status(mid)
        self.assertTrue(st3["is_expired"])

    def test_license_manager_activation(self):
        mgr = LicenseManager.get_instance()
        mid = mgr.machine_id

        # Generate valid code for current mid
        code = generate_license_code(mid, days=60, customer_name="当前客户")
        ok, msg = mgr.activate(code)
        self.assertTrue(ok)
        self.assertIn("激活成功", msg)

        # Check license info
        info = mgr.check_license()
        self.assertTrue(info.is_valid)
        self.assertFalse(info.is_expired)


if __name__ == "__main__":
    unittest.main()
