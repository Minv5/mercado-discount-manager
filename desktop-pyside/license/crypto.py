from __future__ import annotations

import base64
import json
import logging
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger(__name__)

# Public RSA Key embedded into client (private key is held strictly by the seller)
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAsVSTW1kXfEzqfhULC1d6
WQqaZnLTSOKbYvKRpRr25fH/bjUAs68Kczy8A8D7ElNUp9NEUQ5i18oDfXOVeJcG
Ho2LgfkdL4OTe7KR8lT76BfkdeSrYxhWSyTT/BmV0pN50KzYIFMP8nEru3vjjTs2
ckQCuU/u/CHxk3LjSOL/t6bopS3o4gwjC2qmfvKk59oebiNwSd+y5EaOF6nzjvXX
JTAjS1w/XSO+t8Mwl9hJunfsrztVXgSfvWKLbTirm/dhSCKtnBOh2eFIgT04kY/x
zEBcEfLJ67+xgb5pIEYEcQHxiYjySPoc3uwPy+keBcElfTeRFvvHdFT8+flPOupM
NwIDAQAB
-----END PUBLIC KEY-----"""


def verify_license_code(code: str, expected_mid: str) -> dict[str, Any] | None:
    """Verify an RSA-signed license code against the expected hardware machine ID.

    Returns the decoded payload dict if valid, or None if invalid/corrupt.
    Payload structure:
    {
        "mid": "MDM-XXXX-XXXX-XXXX-XXXX",
        "exp": "YYYY-MM-DD" or "PERMANENT",
        "type": "commercial" | "trial" | "developer",
        "name": "Customer Name / Store",
        "created": "YYYY-MM-DD"
    }
    """
    clean_code = code.strip().replace("\r", "").replace("\n", "").replace(" ", "")
    if "." not in clean_code:
        return None

    try:
        payload_b64, sig_b64 = clean_code.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
        sig_bytes = base64.urlsafe_b64decode(sig_b64.encode("utf-8"))

        pub_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode("utf-8"))
        pub_key.verify(sig_bytes, payload_bytes, padding.PKCS1v15(), hashes.SHA256())

        payload = json.loads(payload_bytes.decode("utf-8"))
        if not isinstance(payload, dict):
            return None

        # Verify hardware Machine ID match (case-insensitive)
        license_mid = str(payload.get("mid") or "").strip().upper()
        if license_mid != expected_mid.strip().upper():
            logger.warning("License machine ID mismatch: got %s, expected %s", license_mid, expected_mid)
            return None

        # Check expiration date
        exp_str = str(payload.get("exp") or "").strip().upper()
        if exp_str != "PERMANENT":
            try:
                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                if datetime.now().date() > exp_date:
                    logger.info("License has expired on %s", exp_str)
                    return {"expired": True, "exp_date": exp_str, **payload}
            except ValueError:
                logger.error("Invalid expiration date format in license: %s", exp_str)
                return None

        return {"expired": False, **payload}
    except Exception as err:
        logger.debug("License verification failed: %s", err)
        return None
