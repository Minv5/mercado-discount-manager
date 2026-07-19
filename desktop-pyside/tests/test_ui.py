from __future__ import annotations

import calendar
import os
import sys
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
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QMessageBox, QStyle, QStyleOptionSpinBox  # noqa: E402

from app import create_application  # noqa: E402
from core import Account, account_from_json, completed_execution_for_scope, execution_completion_text  # noqa: E402
from core import execution_group_payload  # noqa: E402
from dialogs import ConfirmDialog, SellerCampaignCreateDialog, SettingsDialog, target_label  # noqa: E402
from main_window import MainWindow, business_details_text, business_task_text, execution_log_message  # noqa: E402
from api_client import ApiError  # noqa: E402
from theme import APP_QSS, COLORS  # noqa: E402


class FakeService:
    def __init__(self) -> None:
        self.stopped = False
        self.detached = False

    def ensure_started(self) -> bool:
        return False

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

    def test_final_confirmation_cancel_never_starts_execution_job(self) -> None:
        with patch("main_window.ConfirmDialog.exec", return_value=QDialog.DialogCode.Rejected):
            self.window._confirm_submission({
                "prepare_id": "P1", "resolved_action": "update",
                "confirmation_summary": "批量更新最终范围", "seller_input": {"selected_targets": []},
            })
        self.assertFalse(any(path.endswith("/commit") for _method, path, _body in self.api.calls))

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

    def test_manual_action_change_warning_cancel_creates_zero_prepare(self) -> None:
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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

    def test_manual_same_action_can_explicitly_continue_after_warning(self) -> None:
        self.window._poll_prepare = lambda: None  # type: ignore[method-assign]
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
        self.api.post = lambda path, body=None, **_kwargs: self.api.calls.append(("POST", path, body or {})) or {
            "prepare": {"prepare_id": "P-OTHER-SCOPE", "state": "preparing"}
        }  # type: ignore[method-assign]
        with patch("main_window.ConfirmDialog") as dialog_class:
            self.window._on_execute_clicked()
        dialog_class.assert_not_called()
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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]

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
        self.window._run_worker = lambda operation, success, _failure: pending.append((operation, success))  # type: ignore[method-assign]

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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
        self.window.show()
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
                "id": 1, "action": "cancel", "mode": "real",
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
        self.assertEqual(headers, ["时间", "动作", "活动", "类型", "商品（唯一/活动关系）", "结果", "失败（商品/活动）", "失败原因"])
        self.assertIn("跨多个活动", self.window.records_table.horizontalHeaderItem(4).toolTip())
        self.assertIn("每个活动", self.window.records_table.horizontalHeaderItem(4).toolTip())
        self.assertIn("活动失败不计入商品失败", self.window.records_table.horizontalHeaderItem(6).toolTip())
        self.assertEqual(self.window.records_table.item(0, 4).text(), "100 / 140")
        self.assertEqual(self.window.records_table.item(0, 5).text(), "取消请求 100\n确认移除 80\n待平台确认 20")
        self.assertEqual(self.window.records_table.item(0, 6).text(), "商品 5 / 活动 2")
        self.assertEqual(self.window.records_table.item(1, 5).text(), "成功 84 / 跳过 5")
        self.assertNotIn("确认移除", self.window.records_table.item(1, 5).text())
        self.assertEqual(self.window.records_table.item(2, 4).text(), "旧记录未区分 / -")
        self.assertEqual(self.window.records_table.item(2, 5).text(), "旧记录未区分")

    def test_execution_record_columns_fit_minimum_window_without_horizontal_scroll(self) -> None:
        self.window.resize(1180, 720)
        self.window.show()
        self.app.processEvents()
        self.assertEqual(self.window.records_table.horizontalScrollBar().maximum(), 0)
        visible_width = self.window.records_table.viewport().width()
        used_width = sum(self.window.records_table.columnWidth(index) for index in range(self.window.records_table.columnCount()))
        self.assertLessEqual(used_width, visible_width + 2)
        self.window.thread_pool.waitForDone(5000)

    def test_cancel_details_distinguish_request_verified_pending_and_legacy(self) -> None:
        cancel = {
            "action": "cancel", "relation_count": 100, "unique_item_count": 100, "activity_failure_count": 2,
            "request_success_count": 100, "live_verified_removed_count": 0, "pending_verification_count": 100,
            "success_count": 0, "failed_count": 4, "skipped_count": 100,
            "failure_reason": "商品失败4，活动失败2",
        }
        text = business_task_text(cancel)
        self.assertIn("取消请求成功：100", text)
        self.assertIn("平台确认移除：0", text)
        self.assertIn("取消请求已提交，待平台回查确认：100", text)
        self.assertIn("商品失败：4", text)
        self.assertIn("活动失败：2", text)
        self.assertNotIn("成功：100", text.splitlines())

        old = {"action": "cancel", "total_count": 12, "success_count": 12, "failed_count": 3}
        old_text = business_task_text(old)
        self.assertIn("唯一商品：旧记录未区分", old_text)
        self.assertIn("平台确认移除：旧记录未区分", old_text)
        self.assertNotIn("平台确认移除：12", old_text)

        details = business_details_text(cancel, [{**cancel, "store_name": "测试店", "site_name": "墨西哥站", "promotion_name": "活动A"}])
        self.assertIn("活动商品关系 100", details)
        self.assertIn("商品失败 4，活动失败 2", details)

    def test_backend_legacy_terminal_summary_is_not_shown_before_authoritative_ui_summary(self) -> None:
        self.assertEqual(execution_log_message("结束：总商品 100，成功 100，失败 0，跳过 0，用时 3 秒。"), "")
        self.assertEqual(execution_log_message("正在处理活动。"), "正在处理活动。")

    def test_initial_bundle_does_not_load_history(self) -> None:
        self.api.get = lambda path, **_kwargs: {  # type: ignore[method-assign]
            "/api/settings": {"settings": {}},
            "/api/accounts": {"accounts": []},
            "/api/today/global-discount": {"discount": {}},
            "/api/execution/groups/active": {"active": False, "group": None},
            "/api/execution/submissions/active": {"active": False, "prepare": None},
        }[path]
        self.window._load_initial_bundle()
        self.assertNotIn(("GET", "/api/tasks?limit=20", None), self.api.calls)

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
        self.assertNotIn("accountId", payload)

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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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

        confirmations: list[dict[str, object]] = []
        errors: list[str] = []
        self.window._confirm_submission = lambda prepare: confirmations.append(prepare)  # type: ignore[method-assign]
        self.window._operation_error = lambda _title, message, **_kwargs: errors.append(message)  # type: ignore[method-assign]
        self.window._commit_submission_polled({
            "prepare_id": "P-ASYNC", "state": "reconfirm_required",
            "confirmation_summary": "候选商品数量已变化，请再次确认。",
            "reconfirm_changes": ["候选商品数量已变化"],
        })
        self.assertEqual(confirmations, [])
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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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
        self.assertEqual(self.window.execute_button.text(), "开始核对范围")

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
        self.window._run_worker = lambda operation, success, _failure: success(operation())  # type: ignore[method-assign]
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
        self.window._run_worker = lambda operation, success, failure: pending.append((operation, success, failure))  # type: ignore[method-assign]
        self.window._on_execute_clicked()
        self.window._on_execute_clicked()
        self.assertEqual(len(pending), 1)
        operation, success, _failure = pending[0]
        success(operation())
        self.assertEqual(calls, ["/api/execution/submissions/P-LOCK/cancel"])
        self.assertFalse(self.window.preparing_submission)
        self.assertEqual(self.window.execute_button.text(), "开始核对范围")
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
        self.assertEqual(self.window.execute_button.text(), "开始核对范围")

    def test_final_completion_refreshes_recent_and_current_all_view(self) -> None:
        refreshed: list[list[str]] = []
        self.window._request_record_views = lambda views: refreshed.append(list(views))  # type: ignore[method-assign]
        self.window.records_cache["all"] = [{"id": 1}]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._set_execution_busy(True)
        self.window._group_polled({"group": {"id": "G1", "status": "completed", "action": "update", "children": [], "result": {"action": "update", "stores": [], "store_count": 2}}})
        self.assertFalse(self.window.running_group)
        self.assertEqual(refreshed, [["recent"]])
        self.assertNotIn("all", self.window.records_cache)
        self.assertEqual(self.window.execute_button.text(), "开始核对范围")

        self.window.records_view = "all"
        self.window.running_group = {"id": "G2", "status": "running", "children": []}
        self.window._group_polled({"group": {"id": "G2", "status": "completed", "action": "update", "children": [], "result": {"action": "update", "stores": [], "store_count": 1}}})
        self.assertEqual(refreshed, [["recent"], ["recent", "all"]])

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
        self.assertIn("湖北 / 全部站点：批量更新完成，唯一商品 15，活动商品关系 17，成功 0，商品失败 15，活动失败 1，跳过 0", text)
        self.assertIn("广州 / 全部站点：批量更新完成，唯一商品 9319，活动商品关系 9400，成功 73，商品失败 283，活动失败 0，跳过 8963", text)
        self.assertIn("湖南 / 全部站点：批量更新完成，唯一商品 4486，活动商品关系 4583，成功 19，商品失败 21，活动失败 1，跳过 4446", text)
        self.assertEqual(text.count("本次批量更新总汇总"), 1)
        self.assertIn("店铺 3 个，唯一商品 13820，活动商品关系 14000，成功 92，商品失败 319，活动失败 2，跳过 13409", text)
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
        self.assertIn("取消请求成功 100，平台确认移除 80，取消请求已提交，待平台回查确认 20", text)
        self.assertIn("商品失败 3，活动失败 2", text)
        self.assertNotIn("，成功 100，", text)
        self.assertNotIn("A1", text)

    def test_terminal_null_result_is_safe(self) -> None:
        self.window.accounts = [Account("3408885754", "", "CBT", "湖南")]
        self.window.running_group = {"id": "G1", "status": "running", "children": []}
        self.window._refresh_records_after_group = lambda: None  # type: ignore[method-assign]
        self.window._group_polled({"group": {
            "id": "G1", "status": "failed", "action": "update", "children": [],
            "result": {"action": "update", "store_count": 1, "total": 0, "success": 0, "failed": 0, "skipped": 0,
                       "stores": [{"account_id": "3408885754", "status": "failed", "total": 0, "success": 0, "failed": 0, "skipped": 0}]},
        }})
        self.assertIn("湖南 / 全部站点：批量更新未完整完成，唯一商品 旧记录未区分，活动商品关系 -，成功 0，商品失败 0，活动失败 -，跳过 0", self.window.log_box.toPlainText())

    def test_theme_uses_project_icons_and_complete_dark_scrollbars(self) -> None:
        self.assertIn("@CHEVRON_DOWN@", APP_QSS)
        self.assertIn("@CHEVRON_UP@", APP_QSS)
        self.assertIn("QScrollBar::add-page:vertical", APP_QSS)
        self.assertIn("gridline-color", APP_QSS)
        self.assertNotIn("font-size: 14px", APP_QSS)
        self.assertNotIn("font-size: 22px", APP_QSS)
        self.assertNotIn("font-size: 15px", APP_QSS)
        self.assertIn("font-size: 10pt", APP_QSS)

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


if __name__ == "__main__":
    unittest.main()
