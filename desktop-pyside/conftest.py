"""Global pytest configuration and test environment isolation.

Strictly protects user production data from being modified or read during test execution.
"""
import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture(autouse=True, scope="session")
def isolate_test_data_dir():
    """Ensure MDM_DATA_DIR always points to an isolated temp directory for test sessions."""
    with tempfile.TemporaryDirectory(prefix="mdm_test_env_") as tmp_dir:
        test_data_dir = str(Path(tmp_dir) / "data")
        old_val = os.environ.get("MDM_DATA_DIR")
        os.environ["MDM_DATA_DIR"] = test_data_dir
        try:
            yield test_data_dir
        finally:
            if old_val is not None:
                os.environ["MDM_DATA_DIR"] = old_val
            else:
                os.environ.pop("MDM_DATA_DIR", None)
