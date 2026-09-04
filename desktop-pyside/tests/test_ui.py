from __future__ import annotations

import calendar
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QDate, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QAbstractItemView, QDialog, QFrame, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QStyle, QStyleOptionSpinBox  # noqa: E402

from app import create_application  # noqa: E402
from core import Account, account_from_json, completed_execution_for_scope, execution_completion_text, parse_targeted_cancel_item_ids, targeted_cancel_filters  # noqa: E402
from core import execution_group_payload  # noqa: E402
from dialogs import ConfirmDialog, ItemQueryDialog, SellerCampaignCreateDialog, SettingsDialog, TargetedCancelDialog, render_item_status_text, target_label  # noqa: E402
from main_window import (  # noqa: E402
    TASK_HEADERS,
    MainWindow,
    benchmark_text,
    business_details_text,
    business_reason_text,
    business_task_text,
    execution_log_message,
    execution_result_text,
    product_error,
    startup_refresh_success_text,
    startup_refresh_blocked_text,
    task_detail_text,
    product_version,
    status_text,
)
from api_client import ApiError  # noqa: E402
from service_manager import ServiceError  # noqa: E402
from theme import APP_QSS, COLORS  # noqa: E402


class FakeService:
    def __init__(self) -> None:
        self.stopped = False
        self.detached = False
        self.ensure_calls = 0
        self.start_result = False
        self.start_error: Exception | None = None
        self.owns_process = False

    def is_healthy(self, timeout: float = 1.0) -> bool:
        _ = timeout
        return False

    def ensure_started(self, **_kwargs) -> bool:
        self.ensure_calls += 1
        if self.start_error:
            raise self.start_error
        return self.start_result

    def stop(self) -> None:
        self.stopped = True

    def detach(self) -> None:
        self.detached = True


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, path: str, **_kwargs):
        self.calls.append(("GET", path, None))
        if path.startswith("/api/tasks"):
            return {"ok": True, "tasks": []}
        return {"ok": True}

    def post(self, path: str, body=None, **_kwargs):
        self.calls.append(("POST", path, body or {}))
        return {"ok": True, "decision": {"action": "update"}}


class QtUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = create_application(["test-ui"])

    def setUp(self) -> None:
        self.api = FakeApi()
        self.service = FakeService()
        self.window = MainWindow(self.api, self.service, auto_start=False)
        self.window.settings = {"sellerDefaultDiscount": 5, "officialDefaultDiscount": 6}
        self.window.accounts = [Account("A1", "A1", "CBT", "测试店")]
        self.window._fill_store_combo()
        self.window.site_combo.addItem("全部站点", "")
        self.window.seller_combo.addItem("全部自建活动", "")
        self.window.official_combo.addItem("全部官方活动", "")
        self.window.today_completion_ready = True
        # Unit tests that exercise ordinary submission controls model a startup
        # refresh which has already passed the new readiness gate.
        self.window.startup_ready = True
        self.window.scope_inputs_ready = True

    def test_partial_execution_group_is_terminal_and_shown_as_partial_completion(self) -> None:
        self.assertEqual(status_text("partial_or_failed"), "部分完成")
        source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        self.assertIn('"partial_or_failed": "部分完成"', source)
        self.assertIn('{"completed", "partial_or_failed", "failed", "cancelled", "interrupted"}', source)

    def _completed_update(self, *, finished_at: str = "2026-07-15T13:23:48.975Z", site_id: str = "") -> dict:
        return {
            "id": "G-TODAY",
            "status": "completed",
            "action": "update",
            "finished_at": finished_at,
            "scope": {
                "account_ids": ["A1"],
                "site_id": site_id,
                "selected_site_name": "全部站点" if not site_id else "墨西哥站",
                "seller_activity_names": [],
                "official_activity_names": [],
                "exclude_seller": False,
                "exclude_official": False,
                "seller_discount_percent": 10,
                "official_discount_percent": 10,
            },
            "result": {"total": 1307, "success": 809, "failed": 9, "skipped": 489},
        }

    def tearDown(self) -> None:
        self.window.close()

    def test_confirm_dialog_short_current_and_long_text_fit_or_scroll(self) -> None:
        messages = [
            "确认执行。",
            "店铺范围：全部店铺\n站点范围：全部站点\n执行动作：批量更新\n自建折扣：6% 官方折扣：7%",
            "\n".join(f"测试店 / 站点 {index}" for index in range(120)),
        ]
        for message in messages:
            dialog = ConfirmDialog("最终执行确认", message)
            dialog.show()
            self.app.processEvents()
            screen_height = dialog.screen().availableGeometry().height()
            self.assertLessEqual(dialog.height(), screen_height)
            dialog.reject()

    def test_prepared_submission_auto_commits_without_final_confirmation(self) -> None:
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.api.post = lambda path, body=None, **_kwargs: self.api.calls.append(("POST", path, body or {})) or {
            "accepted": True,
            "prepare": {"prepare_id": "P1", "state": "committing"},
        }  # type: ignore[method-assign]
        with patch.object(ConfirmDialog, "exec") as final_confirmation:
            self.window._submission_prepared({"prepare": {
                "prepare_id": "P1", "resolved_action": "update",
                "confirmation_summary": "批量更新最终范围",
                "confirmation_token": "TOKEN-1",
                "seller_input": {"selected_targets": []},
            }})
        final_confirmation.assert_not_called()
        commits = [(path, body) for method, path, body in self.api.calls if method == "POST" and path.endswith("/commit")]
        self.assertEqual(commits, [("/api/execution/submissions/P1/commit", {
            "confirmText": "REAL_SUBMIT",
            "confirmationToken": "TOKEN-1",
        })])
        self.assertIn("准备完成，正在启动任务", self.window.log_box.toPlainText())
        self.window.poll_timer.stop()
        self.window.pending_group_payload = None

    def test_cancel_mode_disables_both_discount_inputs(self) -> None:
        self.window.mode_combo.setCurrentText("批量取消")
        self.app.processEvents()
        self.assertFalse(self.window.seller_discount.isEnabled())
        self.assertFalse(self.window.official_discount.isEnabled())

    def test_auto_mode_restores_global_today_discount(self) -> None:
        self.window.global_seller_discount = 6
        self.window.global_official_discount = 7
        self.window.mode_combo.setCurrentText("批量更新")
        self.window.seller_discount.setValue(9)
        self.window.mode_combo.setCurrentText("自动判断")
        self.assertEqual(self.window.seller_discount.value(), 6)
        self.assertEqual(self.window.official_discount.value(), 7)

    def test_startup_discount_survives_scoped_action_conflict(self) -> None:
        self.window.refresh_scope = lambda: None  # type: ignore[method-assign]
        self.window._apply_initial_bundle(
            {
                "settings": {"sellerDefaultDiscount": 5, "officialDefaultDiscount": 6},
                "accounts": [],
                "discount": {"seller_discount": 8, "official_discount": 9},
                "tasks": [],
            }
        )
        self.assertEqual(self.window.seller_discount.value(), 8)
        self.assertEqual(self.window.official_discount.value(), 9)

        self.window._auto_action_error("不同店铺需要不同动作，本次自动判断已停止。")
        self.assertEqual(self.window.seller_discount.value(), 8)
        self.assertEqual(self.window.official_discount.value(), 9)
        self.assertIn("今日折扣：自建8%，官方9%", self.window.today_label.text())

    def test_auto_shutdown_defaults_off_once_per_software_session(self) -> None:
        self.window.refresh_scope = lambda: None  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.api.post = lambda path, body=None, **_kwargs: self.api.calls.append(("POST", path, body or {})) or {
            "settings": {"autoShutdownAfterExecution": bool((body or {}).get("autoShutdownAfterExecution"))}
        }  # type: ignore[method-assign]
        self.window._apply_initial_bundle({
            "settings": {"autoShutdownAfterExecution": True}, "accounts": [], "discount": {},
        })
        self.assertFalse(self.window.auto_shutdown_check.isChecked())
        self.assertIn(("POST", "/api/settings", {"autoShutdownAfterExecution": False}), self.api.calls)

        self.window._apply_initial_bundle({
            "settings": {"autoShutdownAfterExecution": True}, "accounts": [], "discount": {},
        })
        self.assertTrue(self.window.auto_shutdown_check.isChecked())

    def test_first_empty_account_sites_use_one_bounded_discovery_then_local_cache(self) -> None:
        responses = {
            "/api/accounts/A1/sites": [
                {"ok": True, "sites": []},
                {"ok": True, "sites": []},
            ],
            "/api/accounts/A1/sites?refresh=1": [
                {"ok": True, "sites": [{
                    "child_user_id": "CH1",
                    "site_id": "MLM",
                    "logistic_type": "remote",
                }]},
            ],
            "/api/accounts/A1/promotions": [
                {"ok": True, "promotions": []},
                {"ok": True, "promotions": []},
            ],
        }

        def get(path: str, **_kwargs):
            self.api.calls.append(("GET", path, None))
            key = path.split("?siteId=", 1)[0]
            values = responses.get(key, [{"ok": True}])
            return values.pop(0)

        self.api.get = get  # type: ignore[method-assign]
        first = self.window._load_scope_bundle(["A1"], "", discover_missing_sites=True)
        second = self.window._load_scope_bundle(["A1"], "", discover_missing_sites=True)
        self.assertEqual([row["site_id"] for row in first["sites"]], ["MLM"])
        self.assertEqual(second["sites"], [])
        refresh_calls = [
            path for method, path, _body in self.api.calls
            if method == "GET" and path.endswith("/sites?refresh=1")
        ]
        self.assertEqual(refresh_calls, ["/api/accounts/A1/sites?refresh=1"])

    def test_cached_account_sites_never_trigger_startup_discovery(self) -> None:
        def get(path: str, **_kwargs):
            self.api.calls.append(("GET", path, None))
            if path == "/api/accounts/A1/sites":
                return {"ok": True, "sites": [{
                    "child_user_id": "CH1",
                    "site_id": "MLM",
                    "logistic_type": "remote",
                }]}
            return {"ok": True, "promotions": []}

        self.api.get = get  # type: ignore[method-assign]
        result = self.window._load_scope_bundle(["A1"], "", discover_missing_sites=True)
        self.assertEqual([row["site_id"] for row in result["sites"]], ["MLM"])
        self.assertFalse(any(path.endswith("/sites?refresh=1") for _method, path, _body in self.api.calls))

    def test_initial_bundle_arms_discovery_only_for_its_first_scope_load(self) -> None:
        discovery_flags: list[bool] = []

        def capture_refresh() -> None:
            discovery_flags.append(self.window.initial_site_discovery_pending)
            self.window.initial_site_discovery_pending = False

        self.window.refresh_scope = capture_refresh  # type: ignore[method-assign]
        self.window._apply_initial_bundle({
            "settings": {},
            "accounts": [],
            "discount": {},
        })
        self.window._apply_initial_bundle({
            "settings": {},
            "accounts": [],
            "discount": {},
        })
        self.assertEqual(discovery_flags, [True, False])
        self.assertTrue(self.window.initial_site_discovery_consumed)
        self.assertFalse(self.window.initial_site_discovery_pending)

    def test_today_completion_matches_only_same_business_day_and_exact_scope(self) -> None:
        group = self._completed_update()
        matched = completed_execution_for_scope(
            [group], ["A1"], self.window.current_filters(), business_date="2026-07-15"
        )
        self.assertEqual(matched["id"], "G-TODAY")
        self.assertIn("今日已完成：批量更新10%/10%", execution_completion_text(matched))

        different_site = self.window.current_filters() | {"siteId": "MLM", "siteIds": ["MLM"]}
        self.assertIsNone(completed_execution_for_scope([group], ["A1"], different_site, business_date="2026-07-15"))
        self.assertIsNone(completed_execution_for_scope([group], ["A1"], self.window.current_filters(), business_date="2026-07-16"))

    def test_auto_mode_shows_today_completion_and_blocks_repeat_prepare(self) -> None:
        self.window._apply_today_execution_groups([
            self._completed_update(finished_at=datetime.now(timezone.utc).isoformat())
        ])
        self.assertIn("今日已完成：批量更新10%/10%", self.window.today_label.text())
        self.assertRegex(self.window.today_label.text(), r"（\d{2}:\d{2}）")
        self.assertIn("商品1307，成功809，失败9，跳过489", self.window.today_label.text())
        self.assertFalse(self.window.execute_button.isEnabled())
        self.window._on_execute_clicked()
        self.assertFalse(any(method == "POST" and path == "/api/execution/submissions/prepare" for method, path, _body in self.api.calls))

    def test_background_recovery_summary_logs_partial_enroll_success_once(self) -> None:
        group = self._completed_update(finished_at=datetime.now(timezone.utc).isoformat())
        group.update({
            "id": "G-RECOVERY",
            "status": "cancelled",
            "action": "enroll",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "result": {
                "action": "enroll",
                "success": 15704,
                "failed": 3347,
                "skipped": 2823,
                "pending": 158,
                "pending_verification_count": 158,
                "accounting_complete": False,
            },
        })
        self.window._apply_today_execution_groups([group])
        self.window._apply_today_execution_groups([group])
        text = self.window.log_box.toPlainText()
        line = "后台批量报名结果已更新：成功 15704，失败 3347，跳过 2823，待平台确认 158；当前结果尚未全部确认。"
        self.assertEqual(text.count(line), 1)
        self.assertIn("仍待平台确认 158", execution_result_text(group["result"], "enroll"))
        self.assertTrue(self.window.background_group_timer.isActive())

    def test_background_group_poll_appends_changed_recovery_counts_without_focus(self) -> None:
        initial = self._completed_update(finished_at=datetime.now(timezone.utc).isoformat())
        initial.update({
            "id": "G-BACKGROUND",
            "status": "cancelled",
            "action": "enroll",
            "updated_at": "2026-08-28T10:30:00Z",
            "result": {"action": "enroll", "success": 15482, "failed": 3347, "skipped": 2823, "pending": 158, "accounting_complete": False},
        })
        updated = {**initial, "updated_at": "2026-08-28T10:31:00Z", "result": {**initial["result"], "success": 15704}}
        self.window._apply_today_execution_groups([initial])
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.api.get = lambda path, **_kwargs: {"group": updated}  # type: ignore[method-assign]
        self.window._poll_background_execution_groups()
        updated_again = {**updated, "updated_at": "2026-08-28T10:32:00Z"}
        self.api.get = lambda path, **_kwargs: {"group": updated_again}  # type: ignore[method-assign]
        self.window._poll_background_execution_groups()
        text = self.window.log_box.toPlainText()
        self.assertIn("后台批量报名结果已更新：成功 15482", text)
        self.assertIn("后台批量报名结果已更新：成功 15704", text)
        self.assertEqual(text.count("后台批量报名结果已更新：成功 15704"), 1)
        self.assertTrue(self.window.background_group_timer.isActive())

    @unittest.skip("same-day secondary confirmation retired")
    def test_manual_action_change_warning_cancel_creates_zero_prepare(self) -> None:
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window.pending_prepare_payload = {
            "client_submission_id": "CLIENT-1", "action": "enroll", "requested_action": "enroll"
        }
        self.window.preparing_submission = {"client_submission_id": "CLIENT-1", "state": "starting"}
        error = ApiError(
            "今天已有真实操作。", 409,
            {
                "code": "CONFIRM_SAME_DAY_ACTION",
                "details": {
                    "confirmation_token": "TOKEN-1",
                    "same_action": False,
                    "completed": self._completed_update(),
                },
            },
        )
        with patch("main_window.ConfirmDialog") as dialog_class:
            dialog_class.return_value.exec.return_value = 0
            self.window._prepare_start_failed(error)
        warning_text = dialog_class.call_args.args[1]
        self.assertIn("今日已完成：批量更新10%/10%", warning_text)
        self.assertIn("现在将准备批量报名，这是今天的另一项真实操作", warning_text)
        self.assertFalse(any(path == "/api/execution/submissions/prepare" for _method, path, _body in self.api.calls))
        self.assertTrue(any(path.endswith("/same-day-confirmations/cancel") for _method, path, _body in self.api.calls))
        self.assertEqual(self.window.preparing_submission, {})

    @unittest.skip("same-day secondary confirmation retired")
    def test_manual_same_action_can_explicitly_continue_after_warning(self) -> None:
        self.window._poll_prepare = lambda: None  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window.pending_prepare_payload = {
            "client_submission_id": "CLIENT-2", "action": "update", "requested_action": "update"
        }
        self.window.preparing_submission = {"client_submission_id": "CLIENT-2", "state": "starting"}
        self.api.post = lambda path, body=None, **_kwargs: self.api.calls.append(("POST", path, body or {})) or {
            "prepare": {"prepare_id": "P-MANUAL", "state": "preparing"}
        }  # type: ignore[method-assign]
        error = ApiError(
            "今天已有真实操作。", 409,
            {
                "code": "CONFIRM_SAME_DAY_ACTION",
                "details": {
                    "confirmation_token": "TOKEN-2",
                    "same_action": True,
                    "completed": self._completed_update(),
                },
            },
        )
        with patch("main_window.ConfirmDialog") as dialog_class:
            dialog_class.return_value.exec.return_value = 1
            self.window._prepare_start_failed(error)
        warning_text = dialog_class.call_args.args[1]
        self.assertIn("今日已完成：批量更新10%/10%", warning_text)
        self.assertIn("现在仍将准备批量更新，可能重复处理同一范围", warning_text)
        self.assertEqual(
            sum(method == "POST" and path == "/api/execution/submissions/prepare" for method, path, _body in self.api.calls),
            1,
        )
        prepare_body = next(body for method, path, body in self.api.calls if method == "POST" and path == "/api/execution/submissions/prepare")
        self.assertEqual(prepare_body["client_submission_id"], "CLIENT-2")
        self.assertEqual(prepare_body["same_day_confirmation_token"], "TOKEN-2")
        self.window.prepare_poll_timer.stop()

    def test_manual_completion_label_has_no_second_confirmation_hint(self) -> None:
        self.window.mode_combo.blockSignals(True)
        self.window.mode_combo.setCurrentIndex(1)
        self.window.mode_combo.blockSignals(False)
        self.window._apply_today_execution_groups([self._completed_update()])
        self.assertNotIn("另一项真实操作", self.window.today_label.text())
        self.assertNotIn("再次提示", self.window.today_label.text())
        self.assertFalse(hasattr(self.window, "_confirm_server_same_day_action"))

    def test_server_today_completed_response_clears_prepare_without_retry(self) -> None:
        self.window.pending_prepare_payload = {
            "client_submission_id": "CLIENT-AUTO", "action": "auto", "requested_action": "auto"
        }
        self.window.preparing_submission = {"client_submission_id": "CLIENT-AUTO", "state": "starting"}
        error = ApiError(
            "今天已完成。", 409,
            {"code": "TODAY_COMPLETED", "details": {"completed": self._completed_update()}},
        )
        with patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
            self.window._prepare_start_failed(error)
        self.assertEqual(self.window.preparing_submission, {})
        self.assertIsNone(self.window.pending_prepare_payload)
        self.assertFalse(any(path == "/api/execution/submissions/prepare" for _method, path, _body in self.api.calls))
        self.assertIn("今日已完成：批量更新10%/10%", self.window.today_label.text())

    def test_recent_workbench_records_hydrate_completed_group_state(self) -> None:
        group = self._completed_update(finished_at=datetime.now(timezone.utc).isoformat())
        self.api.get = lambda path, **_kwargs: {"group": group} if path == "/api/execution/groups/G-TODAY?compact=1" else {"tasks": []}  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window.records_request_token = 7
        self.window._records_loaded({"recent": [{
            "id": 1254,
            "mode": "real",
            "action": "update",
            "execution_group_id": "G-TODAY",
            "updated_at": group["finished_at"],
        }]}, 7)
        self.assertTrue(self.window.today_completion_ready)
        self.assertEqual(self.window.current_today_completion["id"], "G-TODAY")
        self.assertIn("今日已完成：批量更新10%/10%", self.window.today_label.text())

    def test_different_scope_does_not_warn_or_block_manual_prepare(self) -> None:
        self.window._apply_today_execution_groups([self._completed_update()])
        self.window.mode_combo.setCurrentText("批量更新")
        self.window.site_combo.blockSignals(True)
        self.window.site_combo.addItem("墨西哥站", "MLM")
        self.window.site_combo.setCurrentIndex(self.window.site_combo.count() - 1)
        self.window.site_combo.blockSignals(False)
        self.window._poll_prepare = lambda: None  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.api.post = lambda path, body=None, **_kwargs: self.api.calls.append(("POST", path, body or {})) or {
            "prepare": {"prepare_id": "P-OTHER-SCOPE", "state": "preparing"}
        }  # type: ignore[method-assign]
        self.window._on_execute_clicked()
        self.assertEqual(
            sum(method == "POST" and path == "/api/execution/submissions/prepare" for method, path, _body in self.api.calls),
            1,
        )
        self.window.prepare_poll_timer.stop()

    def test_execution_records_views_are_lazy_loaded_and_cached_in_one_table(self) -> None:
        recent = [{"id": 20, "action": "update"}]
        history = [{"id": 300, "action": "cancel"}]
        requested: list[str] = []

        def get_tasks(path: str, **_kwargs):
            requested.append(path)
            return {
                "/api/tasks?limit=20": {"tasks": recent},
                "/api/tasks?limit=300": {"tasks": history},
            }[path]

        self.api.get = get_tasks  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]

        self.window.refresh_records()
        self.assertEqual(self.window.records, recent)
        self.assertEqual(self.window.records_table.item(0, 0).data(Qt.ItemDataRole.UserRole)["id"], 20)
        self.window.records_view_combo.setCurrentIndex(1)
        self.assertEqual(self.window.records, history)
        self.assertEqual(self.window.records_table.item(0, 0).data(Qt.ItemDataRole.UserRole)["id"], 300)
        self.window.records_view_combo.setCurrentIndex(0)
        self.window.records_view_combo.setCurrentIndex(1)
        self.assertEqual(requested, ["/api/tasks?limit=20", "/api/tasks?limit=300"])

    def test_fast_view_switch_ignores_stale_response(self) -> None:
        pending: list[tuple[object, object]] = []
        self.api.get = lambda path, **_kwargs: {  # type: ignore[method-assign]
            "/api/tasks?limit=20": {"tasks": [{"id": 20, "action": "update"}]},
            "/api/tasks?limit=300": {"tasks": [{"id": 300, "action": "cancel"}]},
        }[path]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: pending.append((operation, success))  # type: ignore[method-assign]

        self.window.refresh_records()
        operation, success = pending.pop(0)
        success(operation())
        self.window.records_view_combo.setCurrentIndex(1)
        history_operation, history_success = pending.pop(0)
        self.window.records_view_combo.setCurrentIndex(0)
        history_success(history_operation())
        self.assertEqual(self.window.records_view, "recent")
        self.assertEqual(self.window.records_table.item(0, 0).data(Qt.ItemDataRole.UserRole)["id"], 20)
        self.assertNotIn("all", self.window.records_cache)

    def test_initial_bundle_schedules_recent_records_without_loading_all_history(self) -> None:
        scheduled: list[object] = []
        self.window.refresh_scope = lambda: None  # type: ignore[method-assign]
        with patch("main_window.QTimer.singleShot", side_effect=lambda _delay, callback: scheduled.append(callback)):
            self.window._apply_initial_bundle({"settings": {}, "accounts": [], "discount": {}})
        self.assertIn(self.window.refresh_records, scheduled)
        self.assertEqual(self.window.records_view, "recent")
        self.assertNotIn("all", self.window.records_cache)

    def test_execution_records_has_one_table_one_refresh_and_no_history_navigation(self) -> None:
        calls: list[str] = []
        self.window.refresh_records = lambda: calls.append(self.window.records_view)  # type: ignore[method-assign]
        self.window.records_refresh_button.clicked.emit()
        self.assertEqual(calls, ["recent"])
        self.assertEqual([button.text() for button in self.window.nav_buttons], ["工作台", "活动管理"])
        self.assertFalse(hasattr(self.window, "history_table"))
        self.assertFalse(hasattr(self.window, "workbench_table"))
        self.assertEqual(self.window.pages.count(), 2)

    def test_execution_record_details_and_selection_work_in_both_views(self) -> None:
        paths: list[str] = []
        self.api.get = lambda path, **_kwargs: paths.append(path) or {"details": []}  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        for view, task_id in (("recent", 20), ("all", 300)):
            self.window.records_view = view
            self.window._apply_current_records([{
                "id": task_id,
                "action": "update",
                "failure_reason": f"{view} failure",
            }])
            self.window.records_table.selectRow(0)
            self.window._show_selected_summary()
            with patch("main_window.DetailsDialog.exec", return_value=QDialog.DialogCode.Rejected):
                self.window._show_task_details()
        self.assertEqual(
            [path for path in paths if path.startswith("/api/tasks/details")],
            ["/api/tasks/details?taskIds=20", "/api/tasks/details?taskIds=300"],
        )
        log_text = self.window.log_box.toPlainText()
        self.assertIn("recent failure", log_text)
        self.assertIn("all failure", log_text)

    def test_execution_record_view_switch_is_keyboard_accessible(self) -> None:
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window.show()
        self.app.setActiveWindow(self.window)
        self.app.processEvents()
        self.window.records_view_combo.setFocus()
        QTest.keyClick(self.window.records_view_combo, Qt.Key.Key_End)
        self.app.processEvents()
        self.assertEqual(self.window.records_view_combo.currentData(), "all")
        self.assertEqual(self.window.records_view, "all")
        QTest.keyClick(self.window.records_view_combo, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertTrue(self.window.records_refresh_button.hasFocus())

    def test_execution_records_use_business_result_contract_without_mixing_counts(self) -> None:
        self.window._apply_current_records([
            {
                "id": 1, "action": "cancel", "mode": "real", "created_at": "2026-07-26T01:02:03.456Z",
                "relation_count": 140, "unique_item_count": 100, "activity_failure_count": 2,
                "request_success_count": 100, "live_verified_removed_count": 80, "pending_verification_count": 20,
                "success_count": 80, "failed_count": 5, "skipped_count": 15,
                "short_failure_reason": "商品失败5，活动失败2",
            },
            {
                "id": 2, "action": "update", "mode": "real",
                "seller_activity_text": "6%", "official_activity_text": "7%",
                "relation_count": 120, "unique_item_count": 90, "activity_failure_count": 1,
                "request_success_count": 88, "live_verified_removed_count": 77, "pending_verification_count": 11,
                "platform_pending_count": 11,
                "success_count": 84, "failed_count": 1, "skipped_count": 5,
            },
            {
                "id": 3, "action": "cancel", "mode": "real", "total_count": 12,
                "success_count": 12, "failed_count": 3, "skipped_count": 0,
                "relation_count": None, "unique_item_count": None, "activity_failure_count": None,
                "request_success_count": None, "live_verified_removed_count": None, "pending_verification_count": None,
            },
        ])
        headers = [self.window.records_table.horizontalHeaderItem(index).text() for index in range(self.window.records_table.columnCount())]
        self.assertEqual(headers, ["时间", "动作", "折扣", "活动", "类型", "商品 / 处理项", "结果", "失败", "失败原因"])
        self.assertEqual(self.window.records_table.item(0, 2).text(), "-")
        self.assertEqual(self.window.records_table.item(1, 2).text(), "自建6% / 官方7%")
        self.assertIn("参加多个活动会生成多条任务", self.window.records_table.horizontalHeaderItem(5).toolTip())
        self.assertIn("商品×活动", self.window.records_table.horizontalHeaderItem(5).toolTip())
        self.assertIn("活动失败不计入商品失败", self.window.records_table.horizontalHeaderItem(7).toolTip())
        self.assertIn("09:02:03", self.window.records_table.item(0, 0).text())
        self.assertIn("2026-07-26T01:02:03.456Z", self.window.records_table.item(0, 0).toolTip())
        self.assertEqual(self.window.records_table.item(0, 5).text(), "100 件 / 140 项")
        self.assertEqual(self.window.records_table.item(0, 6).text(), "取消请求 100\n成功取消 80\n待平台确认 20")
        self.assertEqual(self.window.records_table.item(0, 7).text(), "商品 5 / 活动 2")
        self.assertEqual(self.window.records_table.item(1, 6).text(), "更新成功 84\n平台待生效 11\n跳过 5")
        self.assertNotIn("成功取消", self.window.records_table.item(1, 6).text())
        self.assertEqual(self.window.records_table.item(2, 5).text(), "旧记录未区分 / -")
        self.assertEqual(self.window.records_table.item(2, 6).text(), "旧记录未区分")

    def test_execution_record_columns_fit_minimum_window_without_horizontal_scroll(self) -> None:
        self.window.resize(1180, 720)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.records_table.horizontalScrollBar().maximum(), 0)
        visible_width = self.window.records_table.viewport().width()
        used_width = sum(self.window.records_table.columnWidth(index) for index in range(self.window.records_table.columnCount()))
        self.assertLessEqual(used_width, visible_width + 2)
        header = self.window.records_table.horizontalHeader()
        for column in (3, 5, 6, 8):
            self.assertEqual(header.sectionResizeMode(column), QHeaderView.ResizeMode.Stretch)
        for column in range(self.window.records_table.columnCount()):
            self.assertGreaterEqual(
                self.window.records_table.columnWidth(column),
                header.fontMetrics().horizontalAdvance(TASK_HEADERS[column]) + 18,
            )
        self.window.thread_pool.waitForDone(5000)

    def test_records_show_daily_item_delta_or_explicit_insufficient_snapshot(self) -> None:
        self.window._apply_current_records([])
        self.assertEqual(self.window.records_delta_label.text(), "较昨日商品变化：完整快照不足，暂不统计新增/减少")
        self.window._apply_current_records([{
            "id": 2,
            "daily_item_delta": {
                "status": "insufficient",
                "reason": "current_snapshot_missing",
                "reason_cn": "当前经营路由缺少今日商品身份快照。",
                "insufficient_routes": [{"account_id": "A", "child_user_id": "CH-2", "site_id": "MLM"}],
            },
        }])
        self.assertIn("完整快照不足", self.window.records_delta_label.text())
        self.assertIn("A / CH-2 / MLM", self.window.records_delta_label.toolTip())
        self.window._apply_current_records([{
            "id": 1,
            "daily_item_delta": {
                "status": "ready",
                "current_date": "2026-07-26",
                "baseline_date": "2026-07-25",
                "added_count": 18,
                "removed_count": 7,
            },
        }])
        self.assertEqual(self.window.records_delta_label.text(), "较昨日：新增 18 件，减少 7 件")
        self.assertIn("2026-07-25", self.window.records_delta_label.toolTip())
        self.assertIn("2026-07-26", self.window.records_delta_label.toolTip())

    def test_startup_refresh_message_distinguishes_activity_cache_and_route_snapshot_state(self) -> None:
        message = startup_refresh_success_text({
            "readiness": {"ready": True},
            "account_audits": [
                {"store_name": "湖北店", "status": "ok"},
                {"store_name": "湖南店", "status": "ok"},
                {"store_name": "广东店", "status": "ok"},
            ],
            "cbt_callback": {
                "backlog_event_count": 257,
                "backlog_unique_resource_count": 237,
                "historical_outside_recent": {"event_count": 3392, "item_count": 2899},
                "last_replay": {"budget_used": 200, "replayed": 180, "failed": 5, "unresolved": 15, "remaining_events": 57},
            },
            "webhook_item_summary": {"refreshed_item_count": 1851, "changed_item_count": 1195},
            "daily_item_delta": {
                "status": "insufficient",
                "reason": "current_snapshot_missing",
            },
        })
        self.assertIn("商品和活动缓存同步完成", message)
        self.assertIn("3家店铺均完成", message)
        self.assertIn("今日商品快照已更新 1851 个，其中 1195 个检测到数据变化", message)
        self.assertNotIn("backlog", message)
        self.assertNotIn("48小时外历史", message)
        self.assertNotIn("补偿本批", message)
        self.assertNotIn("GET", message)
        self.assertNotIn("完整商品快照不足", message)
        self.assertNotIn("无新增", message)

    def test_global_parent_without_actionable_local_child_is_non_blocking_and_explicit(self) -> None:
        message = startup_refresh_success_text({
            "readiness": {"ready": True},
            "account_audits": [
                {"store_name": "湖北", "status": "ok"},
                {"store_name": "广州", "status": "ok"},
                {"store_name": "湖南", "status": "ok"},
            ],
            "cbt_replay": {
                "terminal_categories": {
                    "quarantined_unknown": 42,
                    "terminal_irrelevant": 5,
                },
                "classification_item_counts": {
                    "terminal_no_actionable_global_parent": 36,
                    "terminal_irrelevant": 7,
                },
            },
        })
        self.assertIn("全球父商品 36 个暂无可操作站点子商品，已隔离且不影响活动", message)
        self.assertNotIn("字段不足或多候选 42 个", message)
        self.assertNotIn("全球父商品 36 个已删除", message)

    def test_startup_final_classification_summary_is_logged_once_and_status_bar_stays_compact(self) -> None:
        data = {
            "status": "blocked",
            "readiness": {
                "ready": False,
                "reasons": [{"reason_cn": "有 52 个商品字段不足或多候选，已隔离等待人工核对。"}],
            },
            "account_audits": [
                {"store_name": "湖北", "status": "ok"},
                {"store_name": "广州", "status": "ok"},
                {"store_name": "湖南", "status": "ok"},
            ],
            "cbt_replay": {
                "unique_resource_succeeded": 51,
                "cache_updated_count": 236,
                "classification_item_counts": {
                    "quarantined_unknown": 52,
                    "terminal_irrelevant": 6,
                    "eligible_partial": 1,
                },
                "remaining_eligible_events": 1,
                "remaining_eligible_resources": 1,
                "physical_get_used": 367,
                "physical_budget": 1000,
            },
        }
        self.window.show()
        self.window.resize(640, 420)
        self.app.processEvents()
        self.window._apply_startup_refresh_status(data)
        self.window._apply_startup_refresh_status(data)
        self.app.processEvents()
        text = self.window.log_box.toPlainText()
        self.assertEqual(text.count("CBT成功 51 个"), 1)
        self.assertIn("子缓存更新 236 个", text)
        self.assertIn("字段不足 52 个", text)
        self.assertIn("无关/不可用排除 6 个", text)
        self.assertIn("待重试 1 个", text)
        self.assertIn("GET 367/1000", text)
        self.assertEqual(self.window.statusBar().currentMessage(), "")
        self.assertEqual(self.window.statusBar().toolTip(), "")
        self.assertNotIn("字段不足 52 个", self.window.statusBar().currentMessage())
        self.assertTrue(self.window.version_label.isVisible())
        self.assertEqual(self.window.log_box.verticalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def test_startup_readiness_blocks_execution_until_all_shop_audits_and_replay_finish(self) -> None:
        self.window.startup_ready = False
        self.window.scope_ready = True
        self.window.today_completion_ready = True
        self.assertFalse(self.window._can_start_submission())
        blocked = startup_refresh_blocked_text({
            "status": "blocked",
            "readiness": {
                "ready": False,
                "reasons": [
                    {"reason_cn": "广东店：商品明细阶段失败。"},
                    {"reason_cn": "最近48小时仍有 365 条商品通知待确认。"},
                ],
            },
            "account_audits": [{"store_name": "广东店", "status": "failed"}],
        })
        self.assertIn("广东店：商品明细阶段失败", blocked)
        self.assertIn("365 条商品通知", blocked)
        self.assertIn("执行条件", blocked)

    def test_startup_replay_progress_shows_counts_instead_of_fixed_percent_only(self) -> None:
        self.window._log_startup_refresh_progress({
            "stage_label": "补偿最近48小时商品通知",
            "replay_progress": {
                "status": "running",
                "wave": 1,
                "page": 2,
                "attempted": 250,
                "total": 392,
                "total_known": True,
                "succeeded": 0,
                "unresolved": 220,
                "retryable_failed": 30,
                "unique_cbt": 180,
                "route_targets": 240,
                "cache_updated": 200,
                "physical_get_used": 700,
                "physical_budget": 1000,
            },
        })
        text = self.window.log_box.toPlainText()
        self.assertIn("商品补偿第1波第2页：已处理250/392条通知", text)
        self.assertIn("CBT 180 个", text)
        self.assertIn("匹配子商品 240 个", text)
        self.assertIn("缓存更新 200 个", text)
        self.assertIn("GET 700/1000", text)
        self.assertNotIn("92%", text)
        self.assertNotIn("99%", text)

    def test_startup_replay_terminal_progress_uses_business_classification_not_zero_attempts(self) -> None:
        self.window._log_startup_refresh_progress({
            "stage_label": "补偿最近48小时商品通知",
            "replay_progress": {
                "status": "ready",
                "attempted": 0,
                "total": 60,
                "total_known": True,
                "remaining_resources": 57,
                "targeted_fallback_planned": 60,
                "remaining_events": 60,
                "physical_get_used": 0,
                "physical_budget": 1000,
            },
        })
        text = self.window.log_box.toPlainText()
        self.assertIn("CBT父商品通知：60条已完成本地分类", text)
        self.assertIn("57个无可操作站点子商品已隔离", text)
        self.assertIn("可处理剩余0条", text)
        self.assertNotIn("已处理0/60", text)
        self.assertNotIn("待定向补查", text)
        self.assertNotIn("剩余 60", text)

    def test_startup_account_progress_uses_completed_count_and_final_store_line(self) -> None:
        self.window._log_startup_refresh_progress({
            "stage_label": "刷新已报名商品",
            "account_progress": {"completed": 1, "total": 3, "active_store": "广州"},
        })
        self.window._log_startup_refresh_progress({
            "stage_label": "刷新已报名商品",
            "account_progress": {
                "completed": 3,
                "total": 3,
                "final_line": "活动缓存：3/3 家完成｜湖北：完成｜广州：完成｜湖南：完成",
            },
        })
        text = self.window.log_box.toPlainText()
        self.assertIn("活动缓存：已完成 1/3 家｜正在处理 广州 家", text)
        self.assertIn("活动缓存：3/3 家完成｜湖北：完成｜广州：完成｜湖南：完成", text)
        self.assertNotIn("广州 3/3", text)
        self.assertNotIn("99%", text)

    def test_startup_replay_progress_without_total_has_no_synthetic_denominator(self) -> None:
        self.window._log_startup_refresh_progress({
            "stage_label": "补偿最近48小时商品通知",
            "replay_progress": {"status": "counting", "total_known": False},
        })
        text = self.window.log_box.toPlainText()
        self.assertIn("商品通知补偿：正在统计最近48小时待处理数据…", text)
        self.assertNotIn("92", text)
        self.assertNotIn("99", text)

    def test_startup_ready_allows_execution_only_after_explicit_readiness(self) -> None:
        self.window.scope_ready = True
        self.window.today_completion_ready = True
        self.window.mode_combo.setCurrentIndex(2)
        self.window.ui_busy = False
        self.window.auto_action = "update"
        self.window.startup_ready = False
        self.assertFalse(self.window._can_start_submission())
        self.window._apply_startup_refresh_status({
            "status": "ok",
            "readiness": {"ready": True, "reasons": []},
        })
        self.assertTrue(self.window.startup_ready)
        self.assertTrue(self.window._can_start_submission())

    def test_scope_completion_triggers_idempotent_startup_refresh(self) -> None:
        source = Path(__file__).resolve().parents[1].joinpath("main_window.py").read_text(encoding="utf-8")
        self.assertIn('self._request_startup_refresh_after_scope()', source)
        self.assertIn('self.api.post("/api/startup-refresh/start", {}, timeout=10)', source)
        self.assertIn('if self.startup_refresh_start_requested or self.startup_status_finalized:', source)

    def test_cancel_details_distinguish_request_verified_pending_and_legacy(self) -> None:
        cancel = {
            "action": "cancel", "relation_count": 100, "unique_item_count": 100, "activity_failure_count": 2,
            "request_success_count": 100, "live_verified_removed_count": 0, "pending_verification_count": 100,
            "success_count": 0, "failed_count": 4, "skipped_count": 100,
            "failure_reason": "商品失败4，活动失败2",
        }
        text = business_task_text(cancel)
        self.assertIn("取消请求成功：100", text)
        self.assertIn("成功取消：0", text)
        self.assertIn("取消请求已提交，待平台回查确认：100", text)
        self.assertIn("商品失败：4", text)
        self.assertIn("活动失败：2", text)
        self.assertNotIn("成功：100", text.splitlines())

        old = {"action": "cancel", "total_count": 12, "success_count": 12, "failed_count": 3}
        old_text = business_task_text(old)
        self.assertIn("涉及商品：旧记录未区分", old_text)
        self.assertIn("成功取消：旧记录未区分", old_text)
        self.assertNotIn("成功取消：12", old_text)

        details = business_details_text(cancel, [{**cancel, "store_name": "测试店", "site_name": "墨西哥站", "promotion_name": "活动A"}])
        self.assertIn("需处理项：100 项（商品×活动）", details)
        self.assertIn("商品失败 4，活动失败 2", details)

    def test_backend_legacy_terminal_summary_is_not_shown_before_authoritative_ui_summary(self) -> None:
        self.assertEqual(execution_log_message("结束：总商品 100，成功 100，失败 0，跳过 0，用时 3 秒。"), "")
        self.assertEqual(execution_log_message("正在处理活动。"), "")

    def test_technical_reasons_are_translated_without_calling_cancel_a_failure(self) -> None:
        self.assertEqual(
            business_reason_text("PROMOTION_ITEMS_UNREADABLE"),
            "平台未返回可读取的商品清单，暂无法确认取消结果。",
        )
        self.assertEqual(product_error("pending_relations_present"), "存在待平台确认的商品关系。")
        log_text = execution_log_message("批量回读失败：results=null；pending_relations_present；accounting_complete=false")
        self.assertIn("平台未返回可读取的商品清单", log_text)
        self.assertIn("存在待平台确认的商品关系", log_text)
        self.assertIn("本次结果尚未全部确认", log_text)
        self.assertNotIn("results=null", log_text)

        cancel = {
            "action": "cancel",
            "relation_count": 3,
            "unique_item_count": 2,
            "request_success_count": 3,
            "live_verified_removed_count": 0,
            "pending_verification_count": 3,
            "failed_count": 3,
            "skipped_count": 0,
            "failure_reasons": [{"reason": "PROMOTION_ITEMS_UNREADABLE", "count": 3}],
            "accounting_complete": False,
            "incomplete_reasons": ["pending_relations_present"],
            "summary_json": '{"diagnostic":{"stable_code":"PROMOTION_ITEMS_UNREADABLE","results":null}}',
        }
        result_text = execution_result_text(cancel, "cancel")
        self.assertIn("取消请求已成功", result_text)
        self.assertIn("部分商品仍待平台确认", result_text)
        self.assertIn("本次结果尚未全部确认", result_text)
        self.assertNotIn("商品失败 3", result_text)
        self.assertNotIn("PROMOTION_ITEMS_UNREADABLE", result_text)

        detail_text = task_detail_text(
            cancel,
            [{**cancel, "store_name": "测试店", "site_name": "墨西哥站", "promotion_name": "活动A"}],
            {"failed_items": [{"item_id": "MLB1", "reason": "PROMOTION_ITEMS_UNREADABLE"}]},
        )
        self.assertIn("平台未返回可读取的商品清单", detail_text)
        self.assertNotIn("PROMOTION_ITEMS_UNREADABLE", detail_text)
        self.assertIn("PROMOTION_ITEMS_UNREADABLE", cancel["summary_json"])

    def test_initial_bundle_does_not_load_history(self) -> None:
        self.api.get = lambda path, **_kwargs: {  # type: ignore[method-assign]
            "/api/settings": {"settings": {}},
            "/api/accounts": {"accounts": []},
            "/api/today/global-discount": {"discount": {}},
            "/api/execution/groups/active": {"active": False, "group": None},
            "/api/execution/submissions/active": {"active": False, "prepare": None},
            "/api/startup-refresh/status": {"refresh": {"status": "ok"}},
        }[path]
        self.window._load_initial_bundle()
        self.assertNotIn(("GET", "/api/tasks?limit=20", None), self.api.calls)
        self.assertFalse(any(call[1] == "/api/startup-refresh/status" for call in self.api.calls))

    def test_group_payload_contains_all_accounts_and_one_submission_id(self) -> None:
        payload = execution_group_payload(
            account_ids=["A", "B", "C"], action="update", filters={"siteId": "MLM"},
            store_names={"A": "店A", "B": "店B", "C": "店C"}, site_name_text="墨西哥站",
            seller_discount=8, official_discount=9, read_concurrency=3,
            activity_concurrency=4, write_concurrency=20, client_submission_id="SUB-1",
        )
        self.assertEqual(payload["accountIds"], ["A", "B", "C"])
        self.assertEqual(payload["client_submission_id"], "SUB-1")
        self.assertEqual(payload["confirmText"], "REAL_SUBMIT")
        self.assertEqual(payload["mode"], "real")
        self.assertNotIn("accountId", payload)

    def test_targeted_cancel_ids_are_validated_deduplicated_and_unfiltered_by_activity_name(self) -> None:
        self.assertEqual(
            parse_targeted_cancel_item_ids("mlb123, MLB123\nMLM456；MCO789"),
            ["MLB123", "MLM456", "MCO789"],
        )
        with self.assertRaisesRegex(ValueError, "格式不正确"):
            parse_targeted_cancel_item_ids("MLB123, not-an-item")
        filters = targeted_cancel_filters("mlb")
        self.assertEqual(filters["siteIds"], ["MLB"])
        self.assertEqual(filters["promotionTypes"], [])
        self.assertEqual(filters["sellerActivityNames"], [])
        self.assertEqual(filters["officialActivityNames"], [])
        self.assertFalse(filters["excludeSeller"])
        self.assertFalse(filters["excludeOfficial"])

    def test_targeted_cancel_dialog_collects_multiple_ids(self) -> None:
        dialog = TargetedCancelDialog("测试店；全部站点", self.window, seller_discount=17, official_discount=18)
        button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertIn("开始核对并报名", button_texts)
        self.assertNotIn("核对取消范围", button_texts)
        self.assertEqual(dialog.action(), "enroll")
        self.assertIn("自建活动 17%｜官方活动 18%", dialog.discount_note.text())
        dialog.action_combo.setCurrentIndex(1)
        self.assertEqual(dialog.action(), "cancel")
        self.assertEqual(dialog.submit_button.text(), "开始核对并取消")
        self.assertIn("取消不使用折扣", dialog.discount_note.text())
        dialog.item_input.setPlainText("MLB123\nMLM456\nMLB123")
        dialog._validate_and_accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.item_ids(), ["MLB123", "MLM456"])

    def test_targeted_cancel_dialog_opens_during_cache_refresh_and_enables_submit_afterwards(self) -> None:
        ready = [False]
        dialog = TargetedCancelDialog("测试店；全部站点", self.window, submission_ready=lambda: ready[0])
        self.assertFalse(dialog.submit_button.isEnabled())
        self.assertIn("缓存补偿仍在运行", dialog.note.text())
        ready[0] = True
        dialog._sync_submit_state()
        self.assertTrue(dialog.submit_button.isEnabled())
        self.assertIn("只读取命中的活动", dialog.note.text())

        self.window._set_refresh_busy(True)
        self.assertTrue(self.window.targeted_cancel_button.isEnabled())
        with (
            patch("main_window.TargetedCancelDialog.exec", return_value=QDialog.DialogCode.Rejected) as opened,
            patch.object(QMessageBox, "information") as information,
        ):
            self.window._open_targeted_cancel()
        self.assertTrue(opened.called)
        self.assertFalse(information.called, information.call_args)
        for child in self.window.findChildren(TargetedCancelDialog):
            child.close()
        self.window._set_refresh_busy(False)

    def test_targeted_cancel_start_no_longer_depends_on_global_startup_readiness(self) -> None:
        self.window.ui_busy = False
        self.window.refresh_busy = False
        self.window.startup_ready = False
        self.window.scope_ready = False
        self.window.today_completion_ready = False
        self.assertTrue(self.window._can_start_targeted_cancel())
        self.window.refresh_busy = True
        self.assertFalse(self.window._can_start_targeted_cancel())

    def test_targeted_cancel_entry_only_starts_readonly_prepare_with_exact_ids(self) -> None:
        self.window.scope_ready = True
        self.window.startup_readiness = {}
        self.window.startup_ready = True
        self.window.today_completion_ready = True
        self.window.ui_busy = False
        self.window.seller_combo.addItem("自建活动95", "自建活动95")
        self.window.seller_combo.setCurrentIndex(self.window.seller_combo.count() - 1)
        self.window.official_combo.addItem("官方活动A", "官方活动A")
        self.window.official_combo.setCurrentIndex(self.window.official_combo.count() - 1)
        self.window.seller_discount.setValue(17)
        self.window.official_discount.setValue(18)
        self.assertTrue(self.window._can_start_targeted_cancel())
        queued: list[object] = []
        self.window._run_worker = lambda operation, _success, _failure, **_kwargs: queued.append(operation)  # type: ignore[method-assign]
        with (
            patch("main_window.TargetedCancelDialog.exec", return_value=QDialog.DialogCode.Accepted),
            patch("main_window.TargetedCancelDialog.item_ids", return_value=["MLB123", "MLB456"]),
            patch.object(QMessageBox, "information") as information,
        ):
            self.window._open_targeted_cancel()
        self.assertFalse(information.called, information.call_args)
        payload = dict(self.window.pending_prepare_payload or {})
        self.assertEqual(payload["action"], "enroll")
        self.assertEqual(payload["requested_action"], "enroll")
        self.assertTrue(payload["targetedItemAction"])
        self.assertFalse(payload["targetedCancelAllActivities"])
        self.assertEqual(payload["itemIds"], ["MLB123", "MLB456"])
        self.assertEqual(payload["filters"]["promotionTypes"], [])
        self.assertEqual(payload["filters"]["sellerActivityNames"], ["自建活动95"])
        self.assertEqual(payload["filters"]["officialActivityNames"], ["官方活动A"])
        self.assertEqual(payload["sellerDiscountPercent"], 17)
        self.assertEqual(payload["officialDiscountPercent"], 18)
        self.assertEqual(len(queued), 1)
        self.assertFalse(any("/commit" in path or path.endswith("/groups/start") for _method, path, _body in self.api.calls))

    def test_targeted_item_action_can_select_cancel_all_activities(self) -> None:
        self.window.ui_busy = False
        self.window.refresh_busy = False
        queued: list[object] = []
        self.window._run_worker = lambda operation, _success, _failure, **_kwargs: queued.append(operation)  # type: ignore[method-assign]
        with (
            patch("main_window.TargetedCancelDialog.exec", return_value=QDialog.DialogCode.Accepted),
            patch("main_window.TargetedCancelDialog.item_ids", return_value=["MLB123"]),
            patch("main_window.TargetedCancelDialog.action", return_value="cancel"),
        ):
            self.window._open_targeted_cancel()
        payload = dict(self.window.pending_prepare_payload or {})
        self.assertEqual(payload["action"], "cancel")
        self.assertTrue(payload["targetedItemAction"])
        self.assertTrue(payload["targetedCancelAllActivities"])
        self.assertEqual(payload["filters"]["sellerActivityNames"], [])
        self.assertEqual(len(queued), 1)

    def test_targeted_cancel_submits_after_prepare_without_second_confirmation(self) -> None:
        prepare = {
            "prepare_id": "P-TARGETED",
            "resolved_action": "cancel",
            "seller_detection": {},
            "targeted_cancel": {
                "enabled": True,
                "requested_item_count": 3,
                "matched_item_count": 2,
                "unmatched_item_count": 1,
                "unmatched_item_ids": ["MLB999"],
                "relation_count": 3,
                "activity_count": 3,
                "account_count": 1,
                "accounts": [{
                    "store_name": "测试店",
                    "unique_item_count": 2,
                    "relation_count": 3,
                    "activity_count": 3,
                }],
            },
        }
        submitted: list[dict] = []
        self.window._submit_prepared_submission = lambda value: submitted.append(value)  # type: ignore[method-assign]
        self.window._submission_prepared({"prepare": prepare})
        self.assertEqual(submitted, [prepare])
        self.assertIn("未找到 1 个留待重查或人工核实", self.window.log_box.toPlainText())
        self.assertIn("按商品 ID 取消采用单次确认", self.window.log_box.toPlainText())

    def test_targeted_enroll_submits_after_prepare_without_creating_campaign(self) -> None:
        prepare = {
            "prepare_id": "P-TARGETED-ENROLL",
            "resolved_action": "enroll",
            "seller_detection": {"confirmed_absent": []},
            "targeted_item_action": {
                "enabled": True, "action": "enroll", "matched_item_count": 2,
                "unmatched_item_count": 0, "relation_count": 3, "activity_count": 2,
                "account_count": 1, "accounts": [],
            },
        }
        submitted: list[dict] = []
        self.window._submit_prepared_submission = lambda value: submitted.append(value)  # type: ignore[method-assign]
        self.window._submission_prepared({"prepare": prepare})
        self.assertEqual(submitted, [prepare])
        self.assertIn("按商品 ID 报名采用单次确认", self.window.log_box.toPlainText())

    def test_startup_restores_one_active_group(self) -> None:
        self.window.refresh_scope = lambda: None  # type: ignore[method-assign]
        self.window.refresh_records = lambda: None  # type: ignore[method-assign]
        self.window._poll_group = lambda: None  # type: ignore[method-assign]
        self.window._apply_initial_bundle({
            "settings": {}, "accounts": [], "discount": {},
            "execution": {"active": True, "group": {"id": "G1", "status": "running", "children": []}},
        })
        self.assertEqual(self.window.running_group["id"], "G1")
        self.assertEqual(self.window.execute_button.text(), "停止任务")
        self.window.poll_timer.stop()
        self.window.running_group.clear()

    def test_response_loss_recovers_commit_by_get_without_reposting(self) -> None:
        payload = {"prepare_id": "P-LOST", "commit_body": {"confirmText": "REAL_SUBMIT"}}
        self.window.pending_group_payload = dict(payload)
        gets: list[str] = []
        posts: list[tuple[str, object]] = []
        self.api.get = lambda path, **_kwargs: gets.append(path) or {  # type: ignore[method-assign]
            "prepare": {"prepare_id": "P-LOST", "state": "committing", "progress": {"message": "正在重新核对最终范围"}}
        }
        self.api.post = lambda path, body=None, **_kwargs: posts.append((path, body)) or {}  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window._poll_group()
        self.assertEqual(gets, ["/api/execution/submissions/P-LOST"])
        self.assertEqual(posts, [])
        self.assertEqual(self.window.pending_group_payload["prepare_id"], "P-LOST")
        self.assertTrue(self.window.poll_timer.isActive())
        self.window.poll_timer.stop()
        self.window.pending_group_payload = None

    def test_commit_in_progress_409_switches_to_get_recovery_without_repost(self) -> None:
        self.window.pending_group_payload = {"prepare_id": "P-COMMIT", "commit_body": {"confirmText": "REAL_SUBMIT"}}
        self.window._group_start_failed(ApiError(
            "后台正在处理同一次提交。", 409,
            {"code": "COMMIT_IN_PROGRESS", "prepare_id": "P-COMMIT"},
            kind="http", retryable=False,
        ))
        self.assertIsNotNone(self.window.pending_group_payload)
        self.assertEqual(self.window.pending_group_payload["prepare_id"], "P-COMMIT")
        self.assertTrue(self.window.poll_timer.isActive())
        self.assertIn("后台正在处理", self.window.log_box.toPlainText())
        self.window.poll_timer.stop()
        self.window.pending_group_payload = None

    def test_commit_202_is_polled_and_reconfirm_required_never_opens_a_second_confirmation(self) -> None:
        self.window.pending_group_payload = {"prepare_id": "P-ASYNC", "commit_body": {"confirmText": "REAL_SUBMIT"}}
        self.window._commit_accepted({
            "prepare": {"prepare_id": "P-ASYNC", "state": "committing", "progress": {"message": "正在重新核对最终范围"}},
            "accepted": True,
        })
        self.assertTrue(self.window.poll_timer.isActive())
        self.assertEqual(self.window.pending_group_payload["prepare_id"], "P-ASYNC")

        automatic_submissions: list[dict[str, object]] = []
        errors: list[str] = []
        self.window._submit_prepared_submission = lambda prepare: automatic_submissions.append(prepare)  # type: ignore[method-assign]
        self.window._operation_error = lambda _title, message, **_kwargs: errors.append(message)  # type: ignore[method-assign]
        self.window._commit_submission_polled({
            "prepare_id": "P-ASYNC", "state": "reconfirm_required",
            "confirmation_summary": "候选商品数量已变化，请再次确认。",
            "reconfirm_changes": ["候选商品数量已变化"],
        })
        self.assertEqual(automatic_submissions, [])
        self.assertTrue(any("重新开始核对范围" in message for message in errors))
        self.assertIsNone(self.window.pending_group_payload)
        self.window.poll_timer.stop()

    def test_commit_524_and_repeated_recovery_polls_never_post_commit_again(self) -> None:
        self.window.pending_group_payload = {"prepare_id": "P-524", "commit_body": {"confirmText": "REAL_SUBMIT"}}
        posts: list[str] = []
        gets: list[str] = []
        self.api.post = lambda path, body=None, **_kwargs: posts.append(path) or {}  # type: ignore[method-assign]
        self.api.get = lambda path, **_kwargs: gets.append(path) or {  # type: ignore[method-assign]
            "prepare": {"prepare_id": "P-524", "state": "committing", "progress": {"message": "正在核对最终范围"}}
        }
        self.window._group_start_failed(ApiError("网关响应延迟", 524, kind="http", retryable=True))
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        for _ in range(4):
            self.window._poll_group()
        self.assertEqual(posts, [])
        self.assertEqual(gets, ["/api/execution/submissions/P-524"] * 4)
        self.assertTrue(self.window.poll_timer.isActive())
        self.window.poll_timer.stop()
        self.window.pending_group_payload = None

    def test_pending_commit_stop_calls_submission_cancel_once_and_never_starts_group(self) -> None:
        self.window.pending_group_payload = {"prepare_id": "P-STOP", "commit_sent": True}
        calls: list[str] = []
        self.api.post = lambda path, body=None, **_kwargs: calls.append(path) or {  # type: ignore[method-assign]
            "prepare": {"prepare_id": "P-STOP", "state": "cancelled"}
        }
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.window._request_cancel_jobs()
        self.assertEqual(calls, ["/api/execution/submissions/P-STOP/cancel"])
        self.assertIsNone(self.window.pending_group_payload)
        self.assertFalse(self.window.running_group)

    def test_prepare_202_polls_persisted_state_without_starting_group(self) -> None:
        self.window._poll_prepare = lambda: None  # type: ignore[method-assign]
        self.window._prepare_started({"prepare": {
            "prepare_id": "P-ASYNC", "state": "preparing",
            "progress": {"stage": "accounts", "percent": 20, "message": "正在核对店铺"},
        }})
        self.assertEqual(self.window.preparing_submission["prepare_id"], "P-ASYNC")
        self.assertTrue(self.window.prepare_poll_timer.isActive())
        self.assertTrue(self.window.execute_button.isEnabled())
        self.assertEqual(self.window.execute_button.text(), "停止准备")
        self.assertFalse(any(path.endswith("/commit") for _method, path, _body in self.api.calls))
        self.window.prepare_poll_timer.stop()

    def test_cache_refresh_reuses_the_single_execute_button_for_stop_action(self) -> None:
        self.assertFalse(hasattr(self.window, "refresh_stop_button"))
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window._set_refresh_busy(True)
        self.assertEqual(self.window.execute_button.text(), "刷新缓存中…")
        self.assertTrue(self.window.execute_button.isEnabled())
        self.window._on_execute_clicked()
        self.assertEqual(self.window.execute_button.text(), "正在停止缓存补偿…")
        self.assertFalse(self.window.execute_button.isEnabled())
        self.assertTrue(any(
            method == "POST" and path == "/api/startup-refresh/stop"
            for method, path, _body in self.api.calls
        ))
        self.assertFalse(any(
            method == "POST" and path == "/api/execution/submissions/prepare"
            for method, path, _body in self.api.calls
        ))
        self.window._set_refresh_busy(False)
        self.assertEqual(self.window.execute_button.text(), "开始执行")

    def test_pending_startup_refresh_uses_primary_refreshing_button_text(self) -> None:
        self.window._apply_startup_refresh_status({"status": "pending", "readiness": {"ready": False}})
        self.assertTrue(self.window.refresh_busy)
        self.assertTrue(self.window.execute_button.isEnabled())
        self.assertEqual(self.window.execute_button.text(), "刷新缓存中…")

    def test_prepare_poll_timeout_keeps_prepare_locked_and_uses_prepare_message(self) -> None:
        self.window.preparing_submission = {"prepare_id": "P-ASYNC", "state": "preparing"}
        self.window._set_prepare_busy(True)
        self.window._prepare_poll_failed(ApiError(
            "准备进度查询延迟，后台仍在核对范围。", kind="timeout", retryable=True,
        ))
        self.assertEqual(self.window.preparing_submission["state"], "preparing")
        self.assertTrue(self.window.prepare_poll_timer.isActive())
        self.assertTrue(self.window.execute_button.isEnabled())
        self.assertEqual(self.window.execute_button.text(), "停止准备")
        self.assertIn("后台仍在核对范围", self.window.log_box.toPlainText())
        self.window.prepare_poll_timer.stop()

    def test_prepare_progress_displays_business_scheduler_metrics_without_account_ids(self) -> None:
        self.window._log_prepare_progress({"progress": {
            "stage": "items", "percent": 82, "message": "正在核对可报名商品",
            "read_scheduler": {
                "dynamic_limit": 120, "max_limit": 125, "inflight": 96, "peak": 125,
                "detail_inflight": 80, "detail_limit": 125,
                "fallback_active": 2, "fallback_per_account": 2, "queued": 21,
                "cooldown_ms": 2500, "rate_limit_count": 3, "network_error_count": 2,
                "service_error_count": 1, "timeout_error_count": 4, "failure_count": 1, "retry_count": 11,
                "local_work_concurrency": 3, "local_db_batch_queries": 2,
                "per_account": [
                    {"store_name": "湖北", "inflight": 4},
                    {"store_name": "广州", "inflight": 3},
                ],
            },
        }})
        rendered = self.window.log_box.toPlainText()
        self.assertIn("本地整理并行 3（批量查询 2）", rendered)
        self.assertIn("平台读取并发 96/120（峰值 125，上限 125）", rendered)
        self.assertIn("详情 80/125，库存兜底 2（每店上限 2），排队 21", rendered)
        self.assertIn("限流 3 次，网络异常 2 次，服务异常 1 次，超时 4 次，最终失败 1 次，重试 11 次，冷却 3 秒", rendered)
        self.assertIn("湖北 4、广州 3", rendered)
        self.assertNotIn("2651442567", rendered)

    def test_targeted_cancel_progress_uses_index_and_matched_activity_wording(self) -> None:
        self.window._log_prepare_progress({"progress": {
            "stage": "targeted_lookup", "percent": 8,
            "message": "正在从本地索引定位 12 个指定商品的活动关系",
        }})
        self.window._log_prepare_progress({"progress": {
            "stage": "targeted_verify", "percent": 55, "completed": 3, "total": 8,
            "message": "正在定向核对指定商品命中的活动（3/8）",
            "current_store": "湖南", "current_site": "MLB",
        }})
        rendered = self.window.log_box.toPlainText()
        self.assertIn("[定位指定商品]", rendered)
        self.assertIn("[核对命中活动]", rendered)
        self.assertIn("正在定向核对指定商品命中的活动（3/8）", rendered)
        self.assertNotIn("核对店铺范围", rendered)
        self.assertNotIn("刷新店铺活动", rendered)

    def test_prepared_poll_enters_existing_confirmation_and_failed_unlocks(self) -> None:
        prepared = []
        self.window._submission_prepared = lambda response: prepared.append(response)  # type: ignore[method-assign]
        self.window.preparing_submission = {"prepare_id": "P-ASYNC", "state": "preparing"}
        self.window._prepare_polled({"prepare": {"prepare_id": "P-ASYNC", "state": "prepared"}})
        self.assertTrue(any(dict(row.get("prepare") or {}).get("state") == "prepared" for row in prepared))
        self.assertFalse(self.window.preparing_submission)
        self.window.preparing_submission = {"prepare_id": "P-FAIL", "state": "preparing"}
        with patch.object(QMessageBox, "warning"):
            self.window._prepare_polled({"prepare": {"prepare_id": "P-FAIL", "state": "failed", "error": "活动读取失败"}})
        self.assertFalse(self.window.preparing_submission)
        self.assertEqual(self.window.execute_button.text(), "开始执行")

    def test_prepare_failure_uses_specific_safe_business_messages(self) -> None:
        expected = {
            "rate_limit": "平台读取触发限流，请稍后重新核对。",
            "service": "平台服务暂时异常，请稍后重新核对。",
            "timeout": "平台读取超时，请稍后重新核对。",
            "network": "网络连接暂时异常，请检查网络后重新核对。",
            "local_contract": "程序处理平台数据时发现格式异常，已安全停止准备。",
            "local_storage": "本地状态暂时无法保存，已安全停止准备，请稍后重新核对。",
            "unknown": "准备范围时发生未分类异常，已安全停止。",
        }
        shown = []
        self.window._operation_error = lambda title, message: shown.append((title, message))  # type: ignore[method-assign]
        for kind, message in expected.items():
            self.window.preparing_submission = {"prepare_id": f"P-{kind}", "state": "preparing"}
            self.window._prepare_polled({"prepare": {
                "prepare_id": f"P-{kind}", "state": "failed", "error": "平台接口临时异常", "error_kind": kind,
            }})
            self.assertEqual(shown[-1], ("准备执行", message))

    def test_startup_restores_preparing_submission_poll(self) -> None:
        self.window.refresh_scope = lambda: None  # type: ignore[method-assign]
        self.window.refresh_records = lambda: None  # type: ignore[method-assign]
        self.window._poll_prepare = lambda: None  # type: ignore[method-assign]
        self.window._apply_initial_bundle({
            "settings": {}, "accounts": [], "discount": {},
            "execution": {"active": False, "group": None},
            "submission": {"active": True, "prepare": {"prepare_id": "P-RESTORE", "state": "preparing"}},
        })
        self.assertEqual(self.window.preparing_submission["prepare_id"], "P-RESTORE")
        self.assertTrue(self.window.prepare_poll_timer.isActive())
        self.window.prepare_poll_timer.stop()

    def test_prepare_response_loss_retries_same_client_submission_id(self) -> None:
        payload = {"client_submission_id": "SUB-SAME", "accountIds": ["A1"], "requested_action": "update"}
        self.window.preparing_submission = {"client_submission_id": "SUB-SAME", "state": "starting"}
        self.window.pending_prepare_payload = dict(payload)
        calls: list[tuple[str, object]] = []
        self.api.get = lambda _path, **_kwargs: {"active": False, "prepare": None}  # type: ignore[method-assign]
        self.api.post = lambda path, body=None, **_kwargs: calls.append((path, body)) or {  # type: ignore[method-assign]
            "prepare": {"prepare_id": "P-SAME", "client_submission_id": "SUB-SAME", "state": "preparing"}
        }
        self.window._run_worker = lambda operation, success, _failure, **_kwargs: success(operation())  # type: ignore[method-assign]
        self.window._poll_prepare()
        self.assertEqual(calls, [("/api/execution/submissions/prepare", payload)])
        self.assertEqual(self.window.preparing_submission["prepare_id"], "P-SAME")
        self.window.prepare_poll_timer.stop()

    def test_prepare_button_stops_once_and_restores_without_starting_group(self) -> None:
        self.window.preparing_submission = {"prepare_id": "P-LOCK", "state": "preparing"}
        self.window._set_prepare_busy(True)
        self.assertEqual(self.window.execute_button.text(), "停止准备")
        self.assertTrue(self.window.execute_button.isEnabled())
        calls: list[str] = []
        self.api.post = lambda path, body=None, **_kwargs: calls.append(path) or {  # type: ignore[method-assign]
            "prepare": {"prepare_id": "P-LOCK", "state": "cancelled"}
        }
        pending = []
        self.window._run_worker = lambda operation, success, failure, **_kwargs: pending.append((operation, success, failure))  # type: ignore[method-assign]
        self.window._on_execute_clicked()
        self.window._on_execute_clicked()
        self.assertEqual(len(pending), 1)
        operation, success, _failure = pending[0]
        success(operation())
        self.assertEqual(calls, ["/api/execution/submissions/P-LOCK/cancel"])
        self.assertFalse(self.window.preparing_submission)
        self.assertEqual(self.window.execute_button.text(), "开始执行")
        self.assertFalse(any(path.endswith("/commit") for path in calls))

    def test_close_during_preparing_detaches_service_without_stopping_node(self) -> None:
        self.window.preparing_submission = {"prepare_id": "P-LIVE", "state": "preparing", "progress": {"percent": 45}}
        self.window.close()
        self.assertTrue(self.service.detached)
        self.assertFalse(self.service.stopped)

    def test_stale_scope_decision_response_never_overwrites_current_scope(self) -> None:
        self.window.auto_decision_token = 5
        self.window.auto_action = "update"
        self.window._auto_action_ready("cancel", 4)
        self.assertEqual(self.window.auto_action, "update")
        self.window._auto_action_ready("enroll", 5)
        self.assertEqual(self.window.auto_action, "enroll")

    def test_definite_group_validation_error_does_not_retry(self) -> None:
        self.window.pending_group_payload = {"client_submission_id": "BAD"}
        with patch.object(QMessageBox, "warning"):
            self.window._group_start_failed(ApiError("确认参数无效", 400, kind="http", retryable=False))
        self.assertIsNone(self.window.pending_group_payload)
        self.assertFalse(self.window.running_group)
        self.assertFalse(self.window.poll_timer.isActive())

    def test_submit_stays_disabled_until_scope_is_ready(self) -> None:
        self.window.scope_ready = False
        self.window._set_busy(False, "基础数据已加载")
        self.assertFalse(self.window.execute_button.isEnabled())
        self.window.scope_ready = True
        self.window.auto_action = "update"
        self.window._set_busy(False, "工作台已就绪")
        self.assertTrue(self.window.execute_button.isEnabled())

    def test_creation_targets_default_unchecked_and_dates_are_valid(self) -> None:
        dialog = SellerCampaignCreateDialog([{"accountId": "A1", "siteId": "MLM", "storeName": "测试店"}])
        self.assertEqual(dialog.selected_targets(), [])
        start = dialog.start_edit.date()
        self.assertEqual(
            dialog.finish_edit.date(),
            QDate(start.year(), start.month(), calendar.monthrange(start.year(), start.month())[1]),
        )
        dialog.start_edit.setDate(QDate(2026, 8, 8))
        self.assertEqual(dialog.finish_edit.date(), QDate(2026, 8, 31))
        dialog.start_edit.setDate(QDate(2024, 2, 8))
        self.assertEqual(dialog.finish_edit.date(), QDate(2024, 2, 29))
        dialog.start_edit.setDate(QDate(2026, 2, 8))
        self.assertEqual(dialog.finish_edit.date(), QDate(2026, 2, 28))
        item = dialog.scope_list.item(0)
        item.setCheckState(Qt.CheckState.Checked)
        self.assertEqual(len(dialog.selected_targets()), 1)
        self.assertGreaterEqual(dialog.finish_edit.date(), dialog.start_edit.date())

    def test_combo_keyboard_open_select_and_escape(self) -> None:
        combo = self.window.mode_combo
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Tab)
        self.assertIsNot(self.app.focusWidget(), combo)
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key.Key_Down, Qt.KeyboardModifier.AltModifier)
        self.app.processEvents()
        self.assertTrue(combo.view().isVisible())
        QTest.keyClick(combo.view(), Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(combo.view().isVisible())
        QTest.keyClick(combo, Qt.Key.Key_F4)
        self.app.processEvents()
        self.assertTrue(combo.view().isVisible())
        QTest.keyClick(combo.view(), Qt.Key.Key_End)
        QTest.keyClick(combo.view(), Qt.Key.Key_Up)
        QTest.keyClick(combo.view(), Qt.Key.Key_Home)
        QTest.keyClick(combo.view(), Qt.Key.Key_Down)
        QTest.keyClick(combo.view(), Qt.Key.Key_Enter)
        self.app.processEvents()
        self.assertFalse(combo.view().isVisible())
        self.assertEqual(combo.currentText(), "批量报活动")
        self.assertFalse(any(path == "/api/execution/jobs/start" for _method, path, _body in self.api.calls))

    def test_close_stops_owned_service_boundary(self) -> None:
        self.window.close()
        self.assertTrue(self.service.stopped)

    def test_group_poll_timeout_keeps_group_busy_and_retries(self) -> None:
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._set_execution_busy(True)
        self.window._poll_group_failed(ApiError("进度查询等待时间较长，任务仍在执行。", kind="timeout", retryable=True))
        self.assertEqual(self.window.running_group["status"], "running")
        self.assertTrue(self.window.poll_timer.isActive())
        self.assertEqual(self.window.execute_button.text(), "停止任务")
        self.window.poll_timer.stop()
        self.window.running_group.clear()

    def test_running_group_logs_keep_important_store_messages_in_timestamp_order(self) -> None:
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        response = {"group": {
            "id": "G1",
            "status": "running",
            "action": "cancel",
            "children": [
                {"job_id": "JOB-B", "userLogs": [
                    {"at": "2026-07-23T05:47:29.000Z", "message": "广州：平台限流，冷却后重试"},
                ]},
                {"job_id": "JOB-A", "userLogs": [
                    {"at": "2026-07-23T05:47:28.000Z", "message": "湖北：任务恢复，继续处理"},
                ]},
                {"job_id": "JOB-C", "userLogs": [
                    {"at": "2026-07-23T05:47:30.000Z", "message": "湖南：商品失败 1"},
                    {"at": "2026-07-23T05:47:31.000Z", "message": "详情 28/125，排队 97"},
                ]},
            ],
        }}
        self.window._group_polled(response)
        text = self.window.log_box.toPlainText()
        self.window.poll_timer.stop()
        self.window.running_group.clear()
        self.assertLess(text.index("湖北：任务恢复"), text.index("广州：平台限流"))
        self.assertLess(text.index("广州：平台限流"), text.index("湖南：商品失败"))
        self.assertNotIn("详情 28/125", text)
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._group_polled(response)
        self.window.poll_timer.stop()
        self.window.running_group.clear()
        self.assertEqual(self.window.log_box.toPlainText().count("湖北：任务恢复"), 1)

    def test_running_group_log_cursor_survives_fixed_length_backend_rollover(self) -> None:
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        first = [
            {"at": f"2026-07-23T05:47:{index:02d}.000Z", "message": f"恢复进度 {index}"}
            for index in range(3)
        ]
        self.window._group_polled({"group": {
            "id": "G1", "status": "running", "action": "update",
            "children": [{"job_id": "JOB-A", "userLogs": first}],
        }})
        rolled = first[1:] + [{"at": "2026-07-23T05:48:00.000Z", "message": "恢复进度 新消息"}]
        self.window._group_polled({"group": {
            "id": "G1", "status": "running", "action": "update",
            "children": [{"job_id": "JOB-A", "userLogs": rolled}],
        }})
        text = self.window.log_box.toPlainText()
        self.window.poll_timer.stop()
        self.window.running_group.clear()
        self.assertEqual(text.count("恢复进度 1"), 1)
        self.assertEqual(text.count("恢复进度 2"), 1)
        self.assertEqual(text.count("恢复进度 新消息"), 1)

    def test_ui_log_document_keeps_latest_thousand_lines(self) -> None:
        for index in range(1005):
            self.window.log(f"重要日志 {index}")
        text = self.window.log_box.toPlainText()
        self.assertLessEqual(self.window.log_box.document().blockCount(), 1000)
        self.assertNotIn("重要日志 0\n", text)
        self.assertIn("重要日志 1004", text)

    def test_startup_reuses_compatible_service_and_blocks_incompatible_port_once(self) -> None:
        callbacks: list[tuple[object, object, object]] = []
        self.window._run_worker = lambda function, result, error, **_kwargs: callbacks.append((function, result, error))  # type: ignore[method-assign]
        self.window.startup()
        function, result, error = callbacks.pop()
        result(function())
        self.assertEqual(self.service.ensure_calls, 1)
        self.assertIn("已连接现有程序组件", self.window.log_box.toPlainText())
        callbacks.clear()

        self.service.start_error = ServiceError("本软件组件协议不兼容，请先关闭占用该端口的旧版本。")
        self.window.startup()
        function, _result, error = callbacks.pop()
        with self.assertRaises(ServiceError) as raised:
            function()
        with patch.object(QMessageBox, "warning"):
            error(str(raised.exception))
        self.assertEqual(self.service.ensure_calls, 2)
        self.assertEqual(self.window.component_label.text(), "程序组件未连接")
        self.assertIn("协议不兼容", self.window.log_box.toPlainText())

    def test_real_worker_result_is_applied_on_gui_thread(self) -> None:
        main_thread = threading.get_ident()
        received: list[tuple[object, int]] = []
        failures: list[object] = []
        self.window._run_worker(
            lambda: {"ok": True},
            lambda value: received.append((value, threading.get_ident())),
            failures.append,
            phase="initial_bundle",
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not received and not failures:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertEqual(failures, [])
        self.assertEqual(received, [({"ok": True}, main_thread)])

    def test_callback_exception_releases_busy_but_keeps_readiness_blocked(self) -> None:
        self.window._set_busy(True, "正在读取店铺站点...")
        self.window.startup_ready = True
        failures: list[object] = []

        def broken(_value: object) -> None:
            raise RuntimeError("fixture callback failure")

        self.window._run_worker(
            lambda: {"ok": True},
            broken,
            failures.append,
            phase="scope_bundle",
        )
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not failures:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertEqual(len(failures), 1)
        self.assertFalse(self.window.ui_busy)
        self.assertFalse(self.window.startup_ready)
        self.assertIn("加载店铺与活动范围阶段失败", str(failures[0]))

    def test_stale_startup_callback_cannot_overwrite_new_attempt(self) -> None:
        pending: list[tuple[object, object, object]] = []
        self.window._run_worker = lambda operation, success, failure, **_kwargs: pending.append((operation, success, failure))  # type: ignore[method-assign]
        self.window.startup_attempt_token = 1
        self.window.refresh_scope()
        self.assertEqual(len(pending), 1)
        self.window.startup_attempt_token = 2
        self.window._service_ready(True, 1)
        operation, success, _failure = pending.pop()
        success(operation())
        self.app.processEvents()
        QTest.qWait(30)
        self.assertEqual(self.window.component_label.text(), "正在启动程序组件...")
        self.assertEqual(self.api.calls, [])

    def test_service_ready_starts_readiness_poll_before_initial_bundle_completes(self) -> None:
        self.window.refresh_poll_timer.stop()
        scheduled: list[tuple[object, object, object, dict]] = []

        def capture(function, result, error, **kwargs):
            scheduled.append((function, result, error, kwargs))

        self.window._run_worker = capture  # type: ignore[method-assign]
        self.window._service_ready(True, self.window.startup_attempt_token)
        self.assertTrue(self.window.refresh_poll_timer.isActive())
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][3].get("phase"), "initial_bundle")
        self.assertTrue(scheduled[0][3].get("soft_error"))
        self.window.refresh_poll_timer.stop()

    def test_initial_bundle_timeout_retries_without_logging_permanent_failure(self) -> None:
        self.window.startup_attempt_token = 7
        self.window.initial_bundle_retry_token = 7
        self.service.is_healthy = lambda timeout=1.0: True  # type: ignore[method-assign]
        self.window._initial_bundle_failed(ApiError("进度查询等待时间较长", kind="timeout"), 7)
        text = self.window.log_box.toPlainText()
        self.assertEqual(self.window.initial_bundle_retry_count, 1)
        self.assertTrue(self.window.initial_bundle_retry_timer.isActive())
        self.assertIn("基础数据读取较慢，程序组件仍正常；5 秒后自动重试（1/3）", text)
        self.assertNotIn("加载基础数据阶段失败", text)
        self.window.initial_bundle_retry_timer.stop()

    def test_startup_refresh_poll_is_worker_backed_and_non_reentrant(self) -> None:
        deadline = time.monotonic() + 1
        while self.window.workers and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        self.api.calls.clear()
        scheduled: list[tuple[object, object, object, dict]] = []

        def capture(function, result, error, **kwargs):
            scheduled.append((function, result, error, kwargs))

        self.window.refresh_poll_busy = False
        self.window._run_worker = capture  # type: ignore[method-assign]
        self.window._poll_startup_refresh()
        self.window._poll_startup_refresh()
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][3].get("phase"), "startup_readiness")
        self.assertTrue(scheduled[0][3].get("soft_error"))
        self.assertEqual(self.api.calls, [])

    def test_startup_refresh_poll_slow_response_does_not_overlap(self) -> None:
        deadline = time.monotonic() + 1
        while self.window.workers and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        active = 0
        maximum = 0
        calls = 0

        def slow_get(_path: str, **_kwargs):
            nonlocal active, maximum, calls
            calls += 1
            active += 1
            maximum = max(maximum, active)
            time.sleep(0.08)
            active -= 1
            return {"refresh": {"status": "running", "stage_label": "补偿", "percent": 92}}

        self.api.get = slow_get  # type: ignore[method-assign]
        self.window.refresh_poll_timer.setInterval(60000)
        self.window._poll_startup_refresh()
        self.window._poll_startup_refresh()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.window.refresh_poll_busy:
            self.app.processEvents()
            QTest.qWait(10)
        self.assertEqual(calls, 1)
        self.assertEqual(maximum, 1)
        self.window.refresh_poll_timer.stop()

    def test_startup_refresh_poll_runs_running_to_blocked_and_finalizes_busy(self) -> None:
        deadline = time.monotonic() + 1
        while self.window.workers and time.monotonic() < deadline:
            self.app.processEvents()
            QTest.qWait(10)
        responses = [
            {"refresh": {
                "status": "running", "stage_label": "补偿最近48小时商品通知",
                "replay_progress": {
                    "status": "running", "wave": 1, "page": 1, "attempted": 100,
                    "total": 242, "total_known": True, "unique_cbt": 90, "route_targets": 120,
                    "cache_updated": 110, "physical_get_used": 300, "physical_budget": 1000,
                    "remaining_events": 142,
                },
            }},
            {"refresh": {
                "status": "running", "stage_label": "补偿最近48小时商品通知",
                "replay_progress": {
                    "status": "running", "wave": 1, "page": 2, "attempted": 180,
                    "total": 242, "total_known": True, "unique_cbt": 160, "route_targets": 220,
                    "cache_updated": 210, "physical_get_used": 700, "physical_budget": 1000,
                    "remaining_events": 62,
                },
            }},
            {"refresh": {
                "status": "blocked", "stage_label": "补偿最近48小时商品通知",
                "readiness": {"ready": False, "reasons": [{"reason_cn": "最近48小时仍有 40 条通知待确认。"}]},
                "account_audits": [
                    {"store_name": "湖北", "status": "ok"},
                    {"store_name": "湖南", "status": "ok"},
                    {"store_name": "广州", "status": "ok"},
                ],
                "replay_progress": {
                    "status": "blocked", "wave": 1, "page": 3, "attempted": 220,
                    "total": 242, "total_known": True, "unique_cbt": 200, "route_targets": 260,
                    "cache_updated": 250, "physical_get_used": 1000, "physical_budget": 1000,
                    "remaining_events": 40,
                },
            }},
        ]
        calls: list[str] = []

        def get(path: str, **_kwargs):
            calls.append(path)
            return responses.pop(0)

        self.api.get = get  # type: ignore[method-assign]
        self.window.refresh_poll_timer.setInterval(60000)
        self.window._apply_startup_refresh_status(responses[0]["refresh"])
        self.window.refresh_poll_timer.stop()

        def wait_for_idle() -> None:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and self.window.refresh_poll_busy:
                self.app.processEvents()
                QTest.qWait(10)
            self.app.processEvents()
            self.assertFalse(self.window.refresh_poll_busy)

        for _ in range(3):
            self.window._poll_startup_refresh()
            wait_for_idle()
        text = self.window.log_box.toPlainText()
        self.assertEqual(len(calls), 3)
        self.assertFalse(self.window.refresh_poll_timer.isActive())
        self.assertFalse(self.window.refresh_busy)
        self.assertFalse(self.window.startup_ready)
        self.assertFalse(self.window.execute_button.isEnabled())
        self.assertIn("商品补偿第1波第1页：已处理100/242条通知", text)
        self.assertIn("商品补偿第1波第2页：已处理180/242条通知", text)
        self.assertIn("商品补偿第1波第3页：已处理220/242条通知", text)
        self.assertNotIn("92%", text)
        self.assertNotIn("99%", text)
        self.assertIn("最近48小时仍有 40 条通知待确认", text)
        self.assertIn("店铺结果：湖北：完成；湖南：完成；广州：完成", text)

    def test_startup_refresh_stale_result_does_not_clear_new_request_busy(self) -> None:
        self.window.refresh_poll_token = 2
        self.window.refresh_poll_inflight_token = 2
        self.window.refresh_poll_busy = True
        self.window.refresh_poll_timer.stop()
        self.window._finalize_startup_refresh_poll(
            1,
            time.perf_counter(),
            data={"refresh": {"status": "running", "percent": 92}},
        )
        self.assertTrue(self.window.refresh_poll_busy)
        self.assertEqual(self.window.refresh_poll_inflight_token, 2)
        self.window._finalize_startup_refresh_poll(
            2,
            time.perf_counter(),
            data={"refresh": {
                "status": "blocked", "percent": 99,
                "readiness": {"ready": False, "reasons": [{"reason_cn": "仍有待确认通知。"}]},
            }},
        )
        self.assertFalse(self.window.refresh_poll_busy)
        self.assertIsNone(self.window.refresh_poll_inflight_token)
        self.assertFalse(self.window.refresh_poll_timer.isActive())

    def test_startup_refresh_error_and_callback_exception_finalize_without_infinite_busy(self) -> None:
        self.window.refresh_poll_token = 1
        self.window.refresh_poll_inflight_token = 1
        self.window.refresh_poll_busy = True
        self.window.refresh_poll_timer.stop()
        self.window._finalize_startup_refresh_poll(1, time.perf_counter(), error=RuntimeError("slow read"))
        self.assertFalse(self.window.refresh_poll_busy)
        self.assertFalse(self.window.refresh_busy)
        self.assertTrue(self.window.refresh_poll_timer.isActive())
        self.window.refresh_poll_timer.stop()
        self.window.refresh_poll_token = 2
        self.window.refresh_poll_inflight_token = 2
        self.window.refresh_poll_busy = True
        with patch.object(self.window, "_apply_startup_refresh_status", side_effect=RuntimeError("apply failed")):
            self.window._finalize_startup_refresh_poll(
                2,
                time.perf_counter(),
                data={"refresh": {"status": "blocked", "percent": 99}},
            )
        self.assertFalse(self.window.refresh_poll_busy)
        self.assertFalse(self.window.refresh_busy)
        self.assertIn("加载启动缓存阶段失败", self.window.log_box.toPlainText())

    def test_poll_timeout_never_unlocks_or_starts_duplicate_group(self) -> None:
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._set_execution_busy(True)
        before = len([call for call in self.api.calls if call[1].endswith("/commit")])
        self.window._poll_group_failed(ApiError("进度查询等待时间较长，任务仍在执行。", kind="timeout", retryable=True))
        self.assertEqual(self.window.execute_button.text(), "停止任务")
        self.assertTrue(self.window.execute_button.isEnabled())
        after = len([call for call in self.api.calls if call[1].endswith("/commit")])
        self.assertEqual(before, after)
        self.window.poll_timer.stop()
        self.window.running_group.clear()

    def test_recovered_interrupted_group_is_terminal_without_claiming_success(self) -> None:
        self.window._refresh_records_after_group = lambda: None  # type: ignore[method-assign]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._set_execution_busy(True)
        self.window._group_polled({"group": {
            "id": "G1", "status": "interrupted", "action": "update", "children": [],
            "result": {"action": "update", "store_count": 0, "stores": [], "total": 0, "success": 0, "failed": 0, "skipped": 0},
        }})
        self.assertFalse(self.window.running_group)
        self.assertEqual(self.window.execute_button.text(), "开始执行")

    def test_final_completion_refreshes_recent_and_current_all_view(self) -> None:
        refreshed: list[list[str]] = []
        scope_refreshed: list[bool] = []
        self.window._request_record_views = lambda views: refreshed.append(list(views))  # type: ignore[method-assign]
        self.window.refresh_scope = lambda: scope_refreshed.append(True)  # type: ignore[method-assign]
        self.window.records_cache["all"] = [{"id": 1}]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._set_execution_busy(True)
        self.window._group_polled({"group": {"id": "G1", "status": "completed", "action": "update", "children": [], "result": {"action": "update", "stores": [], "store_count": 2}}})
        self.assertFalse(self.window.running_group)
        self.assertEqual(refreshed, [["recent"]])
        self.assertEqual(scope_refreshed, [True])
        self.assertNotIn("all", self.window.records_cache)
        self.assertEqual(self.window.execute_button.text(), "开始执行")

        self.window.records_view = "all"
        self.window.running_group = {"id": "G2", "status": "running", "children": []}
        self.window._group_polled({"group": {"id": "G2", "status": "completed", "action": "update", "children": [], "result": {"action": "update", "stores": [], "store_count": 1}}})
        self.assertEqual(refreshed, [["recent"], ["recent", "all"]])
        self.assertEqual(scope_refreshed, [True, True])

    def test_item_query_renders_price_cache_without_action_or_activity_rows(self) -> None:
        text = render_item_status_text(
            "MLB7250501430",
            [],
            [],
            [{"price": 0, "original_price": 171.84, "currency_id": "BRL", "updated_at": "2026-08-21T03:00:00Z"}],
        )
        self.assertIn("商品：MLB7250501430", text)
        self.assertIn("最新价格快照：0（原价 171.84，BRL）", text)

    def test_item_query_lists_every_relation_and_candidate_zero_is_not_a_listing_price(self) -> None:
        text = render_item_status_text(
            "MLB7317545856",
            [],
            [
                {
                    "promotion_name": "9.9", "promotion_type": "DEAL", "cached_status": "candidate",
                    "price": 0, "original_price": 24.96, "currency_id": "USD",
                    "raw_json": '{"status":"candidate","price":0,"original_price":24.96}',
                },
                {
                    "promotion_name": "95", "promotion_type": "SELLER_CAMPAIGN", "cached_status": "candidate",
                    "price": 24.96, "original_price": 24.96, "currency_id": "USD",
                    "raw_json": '{"status":"candidate","price":24.96,"original_price":24.96}',
                },
            ],
            [{"price": 24.96, "original_price": None, "currency_id": "USD"}],
        )
        self.assertIn("当前活动关系：2 个", text)
        self.assertIn("9.9（官方活动）", text)
        self.assertIn("95（自建活动）", text)
        self.assertEqual(text.count("活动价：未报名"), 2)
        self.assertIn("折扣基准：24.96 USD", text)
        self.assertNotIn("价格：0", text)

    def test_item_query_translates_technical_action_reason(self) -> None:
        text = render_item_status_text(
            "MLB7250501430",
            [{
                "action": "cancel",
                "status": "pending_verification",
                "promotion_id": "P1",
                "error_cn": "PROMOTION_ITEMS_UNREADABLE",
                "created_at": "2026-08-21T03:00:00Z",
            }],
            [],
            [],
        )
        self.assertIn("平台未返回可读取的商品清单", text)
        self.assertNotIn("PROMOTION_ITEMS_UNREADABLE", text)

    def test_item_query_dialog_does_not_hide_price_cache_only_result(self) -> None:
        dialog = ItemQueryDialog(self.window)
        dialog.show()
        self.app.processEvents()
        dialog.show_result({
            "item_id": "MLB7250501430",
            "actions": [],
            "items": [],
            "price_cache": [{"price": 154.62, "original_price": 171.84, "currency_id": "BRL"}],
        })
        self.assertIn("最新价格快照", dialog.status_box.toPlainText())
        dialog.close()

    def test_settings_only_persists_explicit_store_aliases(self) -> None:
        accounts = [
            Account("2651442567", "", "CBT", "湖北自定义"),
            Account("3332096437", "", "CBT", "广州"),
            Account("3408885754", "", "CBT", "湖南"),
        ]
        dialog = SettingsDialog({"storeAliases": {"2651442567": "湖北自定义"}}, accounts, [], "")
        hubei_item = next(
            dialog.store_table.item(row, 1)
            for row in range(dialog.store_table.rowCount())
            if dialog.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole) == "2651442567"
        )
        self.assertEqual(hubei_item.text(), "湖北自定义")
        self.assertEqual(dialog.values()["storeAliases"], {"2651442567": "湖北自定义"})
        hunan_item = next(
            dialog.store_table.item(row, 1)
            for row in range(dialog.store_table.rowCount())
            if dialog.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole) == "3408885754"
        )
        hunan_item.setText("湖南自定义")
        self.assertEqual(dialog.values()["storeAliases"], {
            "2651442567": "湖北自定义",
            "3408885754": "湖南自定义",
        })

    def test_settings_oauth_fields_mask_secret_and_preserve_webhook_url(self) -> None:
        dialog = SettingsDialog({
            "oauthClientId": "client-123",
            "oauthClientSecretConfigured": True,
            "oauthRedirectUri": "https://example.test/callback",
            "webhookCallbackUrl": "https://callback.example.test/webhook",
        }, [], [], "")
        self.assertEqual(dialog.oauth_client_id.text(), "client-123")
        self.assertEqual(dialog.oauth_client_secret.text(), "")
        self.assertEqual(dialog.oauth_client_secret.echoMode(), QLineEdit.EchoMode.Password)
        self.assertIn("已保存", dialog.oauth_client_secret.placeholderText())
        self.assertEqual(dialog.oauth_redirect_uri.text(), "https://example.test/callback")
        self.assertEqual(dialog.webhook_callback_url.text(), "https://callback.example.test/webhook")
        labels = {label.text() for label in dialog.findChildren(QLabel)}
        self.assertTrue({"美客多应用 Client ID", "美客多应用 Client Secret", "OAuth 回调地址", "Webhook 通知地址"}.issubset(labels))
        self.assertNotIn("Mercado App Client ID", labels)
        self.assertNotIn("Webhook Callback URL", labels)
        self.assertIn("独立回调服务", dialog.webhook_callback_url.placeholderText())
        values = dialog.values()
        self.assertEqual(values["oauthClientSecret"], "")
        self.assertEqual(values["webhookCallbackUrl"], "https://callback.example.test/webhook")

    def test_settings_callback_defaults_use_dedicated_webhook_subdomain(self) -> None:
        dialog = SettingsDialog({}, [], [], "")
        self.assertEqual(
            dialog.webhook_callback_url.text(),
            "https://webhook.xingtupro1020.com/webhook/mercado-libre/",
        )
        self.assertEqual(
            dialog.activity_callback_claim_url.text(),
            "https://webhook.xingtupro1020.com/meli-callback/consumer/claim",
        )
        self.assertEqual(
            dialog.activity_callback_ack_url.text(),
            "https://webhook.xingtupro1020.com/meli-callback/consumer/ack",
        )
        values = dialog.values()
        self.assertNotIn("https://xingtupro1020.com/meli-callback/consumer/", str(values))

    def test_settings_concurrency_controls_show_effective_scheduler_limits(self) -> None:
        dialog = SettingsDialog({
            "readConcurrency": 125,
            "previewConcurrency": 42,
            "writeConcurrency": 24,
        }, [], [], "")
        self.assertEqual(dialog.read_concurrency.maximum(), 125)
        self.assertEqual(dialog.activity_concurrency.maximum(), 192)
        self.assertEqual(dialog.write_concurrency.maximum(), 160)
        self.assertEqual(dialog.values()["readConcurrency"], 125)
        self.assertEqual(dialog.values()["previewConcurrency"], 42)
        self.assertEqual(dialog.values()["writeConcurrency"], 24)
        labels = {label.text() for label in dialog.findChildren(QLabel)}
        self.assertTrue({"读取全局上限", "活动目录并发上限", "商品写入全局上限"}.issubset(labels))
        self.assertNotIn("读取并发（当前使用值）", labels)

    def test_settings_dialog_uses_safe_effective_defaults_and_server_refresh(self) -> None:
        dialog = SettingsDialog({}, [], [], "旧并发说明")
        self.assertEqual(
            (dialog.read_concurrency.value(), dialog.activity_concurrency.value(), dialog.write_concurrency.value()),
            (125, 192, 160),
        )
        dialog.apply_settings_context({
            "authDir": "C:/authorized",
            "readConcurrency": 20,
            "previewConcurrency": 20,
            "writeConcurrency": 350,
            "oauthClientId": "saved-client",
            "oauthClientSecretConfigured": True,
        })
        self.assertEqual(dialog.auth_dir.text(), "C:/authorized")
        self.assertEqual(
            (dialog.read_concurrency.value(), dialog.activity_concurrency.value(), dialog.write_concurrency.value()),
            (20, 20, 160),
        )
        self.assertEqual(dialog.oauth_client_id.text(), "saved-client")
        self.assertIn("已保存", dialog.oauth_client_secret.placeholderText())

    def test_concurrency_explanation_matches_effective_limits(self) -> None:
        text = benchmark_text({})
        self.assertIn("商品读取 125", text)
        self.assertIn("活动目录 192", text)
        self.assertIn("批量取消 160", text)
        self.assertIn("批量报名 160", text)
        self.assertIn("报名 192", text)
        self.assertIn("批量更新 128", text)
        self.assertIn("自动降档", text)
        self.assertNotIn("350", text)
        self.assertNotIn("300-320", text)

    def test_settings_dialog_opens_from_snapshot_without_remote_wait(self) -> None:
        accounts = [Account("2651442567", "PLATFORM_NICK", "CBT", "湖北")]
        started = time.perf_counter()
        dialog = SettingsDialog(
            {"operatingSites": {"2651442567": ["MLB", "MLM"]}},
            accounts,
            [],
            "最近并发状态",
        )
        self.assertLess((time.perf_counter() - started) * 1000, 100)
        self.assertEqual(dialog.values()["operatingSites"], {"2651442567": ["MLB", "MLM"]})

    def test_open_settings_schedules_refresh_after_immediate_dialog_creation(self) -> None:
        self.window.settings = {"operatingSites": {"2651442567": ["MLB", "MLM"]}}
        self.window.accounts = [Account("2651442567", "PLATFORM_NICK", "CBT", "湖北")]
        scheduled: list[object] = []

        def capture(function, on_result, on_error) -> None:
            scheduled.append((function, on_result, on_error))

        started = time.perf_counter()
        with patch.object(self.window, "_run_worker", side_effect=capture), patch.object(
            SettingsDialog, "exec", return_value=QDialog.DialogCode.Rejected
        ):
            self.window._open_settings()
        self.assertLess((time.perf_counter() - started) * 1000, 200)
        self.assertEqual(len(scheduled), 1)

    def test_settings_context_loads_latest_normalized_settings(self) -> None:
        self.window.accounts = [Account("A1", "RAW", "CBT", "测试店")]
        calls: list[str] = []

        def get(path: str, **_kwargs):
            calls.append(path)
            if path == "/api/settings":
                return {"settings": {"readConcurrency": 20, "previewConcurrency": 20, "writeConcurrency": 24}}
            if path.startswith("/api/accounts/profiles/refresh"):
                return {"accounts": [{"account_id": "A1", "raw_display_name": "RAW", "store_name": "测试店", "site_id": "CBT"}]}
            if path.startswith("/api/accounts/A1/sites"):
                return {"sites": []}
            if path == "/api/concurrency-benchmark/results":
                return {"results": {}}
            raise AssertionError(path)

        self.api.get = get  # type: ignore[method-assign]
        context = self.window._load_settings_context()
        self.assertIn("/api/settings", calls)
        self.assertIn("/api/accounts/A1/sites?includeAll=1&probeBusiness=1&refresh=1", calls)
        self.assertEqual(context["settings"], {"readConcurrency": 20, "previewConcurrency": 20, "writeConcurrency": 24})

    def test_settings_background_merge_is_keyed_and_preserves_current_edits(self) -> None:
        accounts = [
            Account("2651442567", "OLD_265", "CBT", "湖北"),
            Account("3332096437", "OLD_333", "CBT", "广州"),
        ]
        dialog = SettingsDialog(
            {"operatingSites": {"2651442567": ["MLB"], "3332096437": ["MLM"]}},
            accounts,
            [
                {"account_id": "2651442567", "site_id": "MLB", "store_name": "湖北"},
                {"account_id": "3332096437", "site_id": "MLM", "store_name": "广州"},
            ],
            "旧状态",
        )
        rows = {
            dialog.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole): row
            for row in range(dialog.store_table.rowCount())
        }
        dialog.store_table.item(rows["2651442567"], 1).setText("湖北自定义")
        dialog.apply_background_context(
            [
                Account("3332096437", "NEW_333", "CBT", "广州"),
                Account("2651442567", "NEW_265", "CBT", "湖北"),
            ],
            [
                {"account_id": "3332096437", "site_id": "MLA", "store_name": "广州"},
                {"account_id": "2651442567", "site_id": "MLC", "store_name": "湖北"},
            ],
            "新状态",
        )
        rows = {
            dialog.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole): row
            for row in range(dialog.store_table.rowCount())
        }
        self.assertEqual(dialog.store_table.item(rows["2651442567"], 0).text(), "NEW_265")
        self.assertEqual(dialog.store_table.item(rows["3332096437"], 0).text(), "NEW_333")
        self.assertEqual(dialog.store_table.item(rows["2651442567"], 1).text(), "湖北自定义")
        site_keys = {
            dialog.site_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(dialog.site_list.count())
        }
        self.assertEqual(site_keys, {
            ("2651442567", "MLB"), ("2651442567", "MLC"),
            ("3332096437", "MLM"), ("3332096437", "MLA"),
        })

    def test_settings_offline_save_does_not_clear_operating_sites(self) -> None:
        dialog = SettingsDialog(
            {"operatingSites": {"2651442567": ["MLB", "MLM"]}},
            [Account("2651442567", "PLATFORM_NICK", "CBT", "湖北")],
            [],
            "",
        )
        self.assertEqual(dialog.values()["operatingSites"], {"2651442567": ["MLB", "MLM"]})

    def test_settings_persists_separate_seller_and_official_cycle_maximums(self) -> None:
        dialog = SettingsDialog(
            {"sellerMaxDiscount": 15, "officialMaxDiscount": 16},
            [Account("A1", "RAW_A1", "CBT", "测试店")],
            [],
            "",
        )
        self.assertEqual(dialog.seller_max_discount.value(), 15)
        self.assertEqual(dialog.official_max_discount.value(), 16)
        values = dialog.values()
        self.assertEqual(values["sellerMaxDiscount"], 15)
        self.assertEqual(values["officialMaxDiscount"], 16)

    def test_settings_cycle_maximums_are_unconfigured_by_default(self) -> None:
        dialog = SettingsDialog({}, [Account("A1", "RAW_A1", "CBT", "测试店")], [], "")
        self.assertEqual(dialog.seller_max_discount.value(), 0)
        self.assertEqual(dialog.official_max_discount.value(), 0)
        self.assertEqual(dialog.seller_max_discount.specialValueText(), "未设置")
        self.assertIsNone(dialog.values()["sellerMaxDiscount"])
        self.assertIsNone(dialog.values()["officialMaxDiscount"])

    def test_settings_store_names_keep_raw_identity_and_account_binding(self) -> None:
        accounts = [
            Account("2651442567", "CNHUBEISHENGRUIHESHANGM", "CBT", "湖北"),
            Account("3332096437", "CNGUANGZHOULINGTANGMINB", "CBT", "广州"),
            Account("3408885754", "CNLIUYANGSHIZHEPINGDIAN", "CBT", "湖南"),
        ]
        dialog = SettingsDialog({}, accounts, [], "")
        self.assertEqual(dialog.store_table.columnCount(), 2)
        self.assertEqual(
            [dialog.store_table.horizontalHeaderItem(index).text() for index in range(2)],
            ["原始店铺名称", "店铺名称"],
        )
        rows_by_account = {
            dialog.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole): row
            for row in range(dialog.store_table.rowCount())
        }
        self.assertEqual(dialog.store_table.item(rows_by_account["2651442567"], 0).text(), "CNHUBEISHENGRUIHESHANGM")
        self.assertEqual(dialog.store_table.item(rows_by_account["3332096437"], 0).text(), "CNGUANGZHOULINGTANGMINB")
        self.assertEqual(dialog.store_table.item(rows_by_account["3408885754"], 0).text(), "CNLIUYANGSHIZHEPINGDIAN")
        self.assertFalse(dialog.store_table.item(rows_by_account["2651442567"], 0).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertEqual(
            {account_id: dialog.store_table.item(row, 1).text() for account_id, row in rows_by_account.items()},
            {"2651442567": "湖北", "3332096437": "广州", "3408885754": "湖南"},
        )
        self.assertEqual(dialog.values()["storeAliases"], {})
        self.assertGreaterEqual(dialog.store_table.columnWidth(0), 340)
        self.assertEqual(
            dialog.store_table.editTriggers(),
            QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed,
        )
        raw_item = dialog.store_table.item(rows_by_account["2651442567"], 0)
        self.assertEqual(raw_item.toolTip(), "CNHUBEISHENGRUIHESHANGM")
        editable_item = dialog.store_table.item(rows_by_account["2651442567"], 1)
        dialog.store_table.editItem(editable_item)
        self.app.processEvents()
        editor = next(
            (child for child in dialog.store_table.findChildren(QLineEdit) if child.parent() is dialog.store_table.viewport()),
            None,
        )
        self.assertIsNotNone(editor)
        self.assertEqual(editor.font().pointSizeF(), dialog.store_table.font().pointSizeF())
        self.assertIn("padding: 0 4px", editor.styleSheet())
        self.assertFalse(editor.hasFrame())

        dialog.store_table.setSortingEnabled(True)
        dialog.store_table.sortItems(0, Qt.SortOrder.DescendingOrder)
        target = next(
            dialog.store_table.item(row, 1)
            for row in range(dialog.store_table.rowCount())
            if dialog.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole) == "3332096437"
        )
        target.setText("广州新店")
        saved = dialog.values()
        self.assertEqual(saved["storeAliases"], {"3332096437": "广州新店"})

        reopened_accounts = [
            account_from_json(
                {
                    "account_id": account.account_id,
                    "raw_display_name": account.raw_display_name,
                    "store_name": saved["storeAliases"].get(account.account_id, account.store_name),
                    "site_id": account.site_id,
                },
            )
            for account in accounts
        ]
        reopened = SettingsDialog(saved, reopened_accounts, [], "")
        reopened_names = {
            reopened.store_table.item(row, 1).data(Qt.ItemDataRole.UserRole): reopened.store_table.item(row, 1).text()
            for row in range(reopened.store_table.rowCount())
        }
        self.assertEqual(
            reopened_names,
            {"2651442567": "湖北", "3332096437": "广州新店", "3408885754": "湖南"},
        )

        self.window.accounts = reopened_accounts
        self.window._fill_store_combo()
        visible_store_names = [self.window.store_combo.itemText(index) for index in range(self.window.store_combo.count())]
        self.assertEqual(visible_store_names, ["全部店铺", "广州新店", "湖北", "湖南"])
        daily_text = " ".join(visible_store_names + [target_label({"store_name": account.store_name}) for account in reopened_accounts])
        self.assertNotIn("CNLIUYANGSHIZHEPINGDIAN", daily_text)
        self.assertNotIn("CNGUANGZHOULINGTANGMINB", daily_text)
        for account in reopened_accounts:
            self.assertNotIn(account.account_id, daily_text)

    def test_operating_sites_are_stably_grouped_after_initial_and_background_merge(self) -> None:
        accounts = [
            Account("3408885754", "RAW-340", "CBT", "湖南"),
            Account("2651442567", "RAW-265", "CBT", "湖北"),
            Account("3332096437", "RAW-333", "CBT", "广州"),
        ]
        dialog = SettingsDialog(
            {"operatingSites": {"3408885754": ["MLM"], "2651442567": ["MLB"], "3332096437": ["MLC"]}},
            accounts,
            [
                {"account_id": "3408885754", "site_id": "MLM", "store_name": "湖南", "operating": True},
                {"account_id": "2651442567", "site_id": "MLB", "store_name": "湖北", "operating": True},
                {"account_id": "3332096437", "site_id": "MLC", "store_name": "广州", "operating": True},
            ],
            "",
        )
        self.assertEqual(
            [dialog.site_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(dialog.site_list.count())],
            [("3332096437", "MLC"), ("2651442567", "MLB"), ("3408885754", "MLM")],
        )
        dialog.apply_background_context(accounts, [
            {"account_id": "3408885754", "site_id": "MLB", "store_name": "湖南"},
            {"account_id": "3332096437", "site_id": "MLA", "store_name": "广州"},
            {"account_id": "2651442567", "site_id": "MLM", "store_name": "湖北"},
        ], "")
        keys = [dialog.site_list.item(index).data(Qt.ItemDataRole.UserRole) for index in range(dialog.site_list.count())]
        self.assertEqual(keys, [
            ("3332096437", "MLA"), ("3332096437", "MLC"),
            ("2651442567", "MLB"), ("2651442567", "MLM"),
            ("3408885754", "MLB"), ("3408885754", "MLM"),
        ])
        checked = {
            dialog.site_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(dialog.site_list.count())
            if dialog.site_list.item(index).checkState() == Qt.CheckState.Checked
        }
        self.assertEqual(checked, {("3332096437", "MLC"), ("2651442567", "MLB"), ("3408885754", "MLM")})

    def test_settings_store_name_validation_blocks_empty_and_duplicates(self) -> None:
        accounts = [
            Account("2651442567", "RAW-A", "CBT", "湖北"),
            Account("3332096437", "RAW-B", "CBT", "广州"),
        ]
        for names, expected in ((["", "广州"], "不能为空"), (["同名店", "同名店"], "不能重复")):
            dialog = SettingsDialog({}, accounts, [], "")
            for row, name in enumerate(names):
                dialog.store_table.item(row, 1).setText(name)
            with patch.object(QMessageBox, "warning") as warning:
                dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            self.assertIn(expected, warning.call_args.args[2])

    def test_execution_log_message_accepts_dict_string_and_json_without_raw_leaks(self) -> None:
        self.assertEqual(execution_log_message({"at": "x", "message": "中文完成"}), "中文完成")
        self.assertEqual(execution_log_message("普通日志"), "普通日志")
        self.assertEqual(execution_log_message('{"at":"x","message":"JSON日志"}'), "JSON日志")
        self.assertEqual(execution_log_message("{not-json"), "{not-json")
        self.assertEqual(execution_log_message({"at": "x", "internal": "secret"}), "")

    def test_terminal_jobs_log_each_authoritative_store_and_one_global_summary(self) -> None:
        self.window.accounts = [
            Account("2651442567", "", "CBT", "湖北"),
            Account("3332096437", "", "CBT", "广州"),
            Account("3408885754", "", "CBT", "湖南"),
        ]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        stores = []
        for job_id, account_id, unique, relations, success, failed, activity_failed, skipped in (
            ("J1", "2651442567", 15, 17, 0, 15, 1, 0),
            ("J2", "3332096437", 9319, 9400, 73, 283, 0, 8963),
            ("J3", "3408885754", 4486, 4583, 19, 21, 1, 4446),
        ):
            stores.append({
                "job_id": job_id,
                "account_id": account_id,
                "status": "completed",
                "total": unique,
                "success": success,
                "failed": failed,
                "skipped": skipped,
                "unique_item_count": unique,
                "relation_count": relations,
                "activity_failure_count": activity_failed,
            })
        self.window._refresh_records_after_group = lambda: None  # type: ignore[method-assign]
        self.window._group_polled({"group": {
            "id": "G1", "status": "completed", "action": "update", "children": [],
            "result": {"action": "update", "store_count": 3, "stores": stores,
                       "total": 13820, "success": 92, "failed": 319, "skipped": 13409,
                       "unique_item_count": 13820, "relation_count": 14000, "activity_failure_count": 2},
        }})
        text = self.window.log_box.toPlainText()
        self.assertIn("湖北 / 全部站点：批量更新完成，处理 17 项，涉及 15 件商品，更新成功 0，商品失败 15，活动失败 1，跳过 0", text)
        self.assertIn("广州 / 全部站点：批量更新完成，处理 9400 项，涉及 9319 件商品，更新成功 73，商品失败 283，活动失败 0，跳过 8963", text)
        self.assertIn("湖南 / 全部站点：批量更新完成，处理 4583 项，涉及 4486 件商品，更新成功 19，商品失败 21，活动失败 1，跳过 4446", text)
        self.assertEqual(text.count("本次批量更新总汇总"), 1)
        self.assertIn("店铺 3 个，处理 14000 项，涉及 13820 件商品，更新成功 92，商品失败 319，活动失败 2，跳过 13409", text)
        self.assertNotIn("{'at':", text)

    def test_cancel_terminal_log_uses_request_verified_and_pending_counts(self) -> None:
        self.window.accounts = [Account("A1", "", "CBT", "测试店")]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._refresh_records_after_group = lambda: None  # type: ignore[method-assign]
        counts = {
            "unique_item_count": 100, "relation_count": 120, "activity_failure_count": 2,
            "request_success_count": 100, "live_verified_removed_count": 80, "pending_verification_count": 20,
            "success": 80, "failed": 3, "skipped": 20,
        }
        self.window._group_polled({"group": {
            "id": "G1", "status": "completed", "action": "cancel", "children": [],
            "result": {**counts, "action": "cancel", "store_count": 1,
                       "stores": [{**counts, "account_id": "A1", "status": "completed"}]},
        }})
        text = self.window.log_box.toPlainText()
        self.assertIn("取消请求成功 100，成功取消 80，取消请求已提交，待平台回查确认 20", text)
        self.assertIn("商品失败 3，活动失败 2", text)
        self.assertNotIn("，成功 100，", text)
        self.assertNotIn("A1", text)

    def test_enroll_terminal_log_separates_platform_pending_from_failures(self) -> None:
        self.window.accounts = [Account("A1", "", "CBT", "测试店")]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._refresh_records_after_group = lambda: None  # type: ignore[method-assign]
        counts = {
            "unique_item_count": 120, "relation_count": 130, "activity_failure_count": 0,
            "success": 100, "failed": 10, "skipped": 0,
            "pending_verification_count": 20, "platform_pending_count": 20,
            "retryable_pending_count": 0,
        }
        self.window._group_polled({"group": {
            "id": "G1", "status": "completed", "action": "enroll", "children": [],
            "result": {**counts, "action": "enroll", "store_count": 1,
                       "stores": [{**counts, "account_id": "A1", "status": "completed"}]},
        }})
        text = self.window.log_box.toPlainText()
        self.assertIn("报名成功 100，平台已接受待生效 20，商品失败 10", text)
        self.assertNotIn("待平台回查确认 20", text)

    def test_terminal_null_result_is_safe(self) -> None:
        self.window.accounts = [Account("3408885754", "", "CBT", "湖南")]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._refresh_records_after_group = lambda: None  # type: ignore[method-assign]
        self.window._group_polled({"group": {
            "id": "G1", "status": "failed", "action": "update", "children": [],
            "result": {"action": "update", "store_count": 1, "total": 0, "success": 0, "failed": 0, "skipped": 0,
                       "stores": [{"account_id": "3408885754", "status": "failed", "total": 0, "success": 0, "failed": 0, "skipped": 0}]},
        }})
        self.assertIn("湖南 / 全部站点：批量更新未完整完成，处理 - 项，涉及 旧记录未区分 件商品，更新成功 0，商品失败 0，活动失败 -，跳过 0", self.window.log_box.toPlainText())

    def test_theme_uses_project_icons_and_complete_dark_scrollbars(self) -> None:
        self.assertIn("@CHEVRON_DOWN@", APP_QSS)
        self.assertIn("@CHEVRON_UP@", APP_QSS)
        self.assertIn("QScrollBar::add-page:vertical", APP_QSS)
        self.assertIn("gridline-color", APP_QSS)
        self.assertNotIn("font-size: 14px", APP_QSS)
        self.assertNotIn("font-size: 22px", APP_QSS)
        self.assertNotIn("font-size: 15px", APP_QSS)
        self.assertIn("font-size: 10pt", APP_QSS)

    def test_version_label_is_in_status_bar_permanent_right_side(self) -> None:
        self.assertEqual(self.window.version_label.text(), f"版本 {product_version()}")
        self.assertEqual(product_version(), "0.1.58")
        self.assertNotIn("0.1.12", self.window.version_label.text())
        self.assertIs(self.window.version_label.parentWidget(), self.window.statusBar())

    def test_status_bar_left_side_stays_empty_during_runtime_updates_and_version_survives_resize(self) -> None:
        self.window._show_status("缓存同步被阻断，详情见运行日志", "长原因不应进入底部边框")
        self.window.resize(520, 360)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.statusBar().currentMessage(), "")
        self.assertEqual(self.window.statusBar().toolTip(), "")
        self.assertTrue(self.window.version_label.isVisible())
        self.assertEqual(self.window.version_label.text(), "版本 0.1.58")

    def test_control_groups_are_three_closed_gold_sections(self) -> None:
        sections = self.window.findChildren(QFrame, "controlSection")
        self.assertEqual(len(sections), 3)
        heading_widgets = [
            section.findChild(QLabel, "sectionTitle")
            for section in sections
        ]
        self.assertEqual([heading.text() for heading in heading_widgets], ["执行范围", "活动参数", "今日判断"])
        for heading in heading_widgets:
            self.assertGreaterEqual(heading.minimumHeight(), heading.fontMetrics().lineSpacing() + 4)
            self.assertTrue(heading.alignment() & Qt.AlignmentFlag.AlignVCenter)
        self.assertIn("QFrame#controlSection", APP_QSS)
        self.assertIn(f"border: 1px solid {COLORS['gold']}", APP_QSS)
        source = (ROOT / "main_window.py").read_text(encoding="utf-8")
        self.assertIn('content.setObjectName("controlContent")', source)
        self.assertNotIn('content.setStyleSheet("background: transparent; border: 0;")', source)
        self.assertIn("QWidget#controlContent", APP_QSS)

    def test_discount_spin_text_and_buttons_fit_across_values_states_and_dpr(self) -> None:
        for spin in (self.window.seller_discount, self.window.official_discount):
            self.assertEqual(spin.suffix(), "%")
            self.assertLess(spin.width(), 108)
            for enabled in (True, False):
                spin.setEnabled(enabled)
                for value in (1, 9, 10, 90):
                    spin.setValue(value)
                    option = QStyleOptionSpinBox()
                    spin.initStyleOption(option)
                    edit_rect = spin.style().subControlRect(
                        QStyle.ComplexControl.CC_SpinBox,
                        option,
                        QStyle.SubControl.SC_SpinBoxEditField,
                        spin,
                    )
                    up_rect = spin.style().subControlRect(
                        QStyle.ComplexControl.CC_SpinBox,
                        option,
                        QStyle.SubControl.SC_SpinBoxUp,
                        spin,
                    )
                    down_rect = spin.style().subControlRect(
                        QStyle.ComplexControl.CC_SpinBox,
                        option,
                        QStyle.SubControl.SC_SpinBoxDown,
                        spin,
                    )
                    text_width = spin.fontMetrics().horizontalAdvance(spin.textFromValue(value) + spin.suffix())
                    for dpr in (1.0, 1.25, 1.5):
                        self.assertGreaterEqual(edit_rect.width() * dpr, (text_width + 8) * dpr)
                        self.assertGreaterEqual(up_rect.width() * dpr, 24 * dpr)
                        self.assertGreaterEqual(down_rect.width() * dpr, 24 * dpr)
                    self.assertTrue(up_rect.isValid())
                    self.assertTrue(down_rect.isValid())

    def test_startup_refresh_ok_automatically_recovers_scope_when_scope_not_ready(self) -> None:
        self.window.scope_inputs_ready = True
        self.window.scope_ready = False
        refreshed: list[bool] = []
        self.window.refresh_scope = lambda *args, **kwargs: refreshed.append(True)  # type: ignore[method-assign]
        self.window._apply_startup_refresh_status({"status": "ok", "readiness": {"ready": True}})
        self.assertEqual(len(refreshed), 1)
        self.assertIn("启动缓存已就绪，正在自动同步店铺与活动范围...", self.window.log_box.toPlainText())

    def test_startup_refresh_ok_does_not_retrigger_scope_if_already_ready(self) -> None:
        self.window.scope_inputs_ready = True
        self.window.scope_ready = True
        refreshed: list[bool] = []
        self.window.refresh_scope = lambda *args, **kwargs: refreshed.append(True)  # type: ignore[method-assign]
        self.window._apply_startup_refresh_status({"status": "ok", "readiness": {"ready": True}})
        self.assertEqual(len(refreshed), 0)

    def test_scope_load_failed_auto_retries_when_startup_ready(self) -> None:
        self.window.startup_ready = True
        self.window.scope_inputs_ready = True
        self.window.scope_retry_count = 0
        self.window._scope_load_failed(self.window.scope_refresh_token, "网络超时")
        self.assertEqual(self.window.scope_retry_count, 1)
        self.assertIn("将在 1 秒后自动重试读取活动范围（1/2）", self.window.log_box.toPlainText())


if __name__ == "__main__":
    unittest.main()
