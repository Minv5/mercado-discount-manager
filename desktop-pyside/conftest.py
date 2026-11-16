"""Global pytest configuration and test environment isolation.

Strictly protects user production data from being modified or read during test execution.
"""
import os
import tempfile
from pathlib import Path
import pytest


def _ensure_qt_platform_plugins() -> None:
    """Qt cannot load its platform plugin from a non-ASCII path, so expose it through an ASCII symlink dir."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PySide6
    except ImportError:
        return
    qt_plugins = os.path.join(os.path.dirname(PySide6.__file__), "Qt", "plugins")
    platforms_dir = os.path.join(qt_plugins, "platforms")
    if not os.path.isdir(platforms_dir):
        return
    os.environ.setdefault("QT_PLUGIN_PATH", qt_plugins)
    try:
        platforms_dir.encode("ascii")
        os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", platforms_dir)
    except UnicodeEncodeError:
        tmp_platforms = "/tmp/qt_platforms"
        os.makedirs(tmp_platforms, exist_ok=True)
        for item in os.listdir(platforms_dir):
            dst = os.path.join(tmp_platforms, item)
            if not os.path.exists(dst):
                os.symlink(os.path.join(platforms_dir, item), dst)
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = tmp_platforms


_ensure_qt_platform_plugins()


@pytest.fixture(autouse=True, scope="session")
def collect_qt_garbage_on_main_thread_only():
    """Keep Python's cyclic GC from destroying Qt objects on background threads.

    Tests leave widgets with running QTimers in reference cycles. If the automatic collector happens
    to fire on a worker thread, Qt cannot unregister those timers from that thread, and the next
    processEvents() delivers a timer event to freed memory (segfault seen on the macOS CI runner).
    """
    import gc

    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()


@pytest.fixture(autouse=True)
def _collect_garbage_after_each_test(collect_qt_garbage_on_main_thread_only):
    import gc

    yield
    gc.collect()


@pytest.fixture(autouse=True, scope="session")
def block_image_downloads():
    """Tests use made-up image URLs; never let the image cache fetch them from the real network.

    Real fetches stall worker threads for minutes at interpreter exit and deliver results into
    unrelated tests' event loops.
    """
    import types
    import urllib.error
    import urllib.request

    from ui_common import image_cache

    def _blocked(*_args, **_kwargs):
        raise urllib.error.URLError("image downloads are disabled in tests")

    offline_urllib = types.SimpleNamespace(
        request=types.SimpleNamespace(Request=urllib.request.Request, urlopen=_blocked)
    )
    original = image_cache.urllib
    image_cache.urllib = offline_urllib
    try:
        yield
    finally:
        image_cache.urllib = original


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
