import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.client import clean_error_message, format_clean_api_error
from reason_text import business_reason_text


class TestCleanErrorMessage(unittest.TestCase):
    def test_500_internal_error(self):
        err = "Oops! Something went wrong..."
        self.assertEqual(clean_error_message(err), "平台服务繁忙，请稍后重试 (500)")
        self.assertEqual(format_clean_api_error(500, "internal_error"), "平台服务繁忙，请稍后重试 (500)")

    def test_502_503_504_gateway(self):
        self.assertEqual(format_clean_api_error(502, "Bad Gateway"), "平台网关超时/维护中 (502)")
        self.assertEqual(format_clean_api_error(503, "Service Unavailable"), "平台网关超时/维护中 (503)")
        self.assertEqual(format_clean_api_error(504, "Gateway Timeout"), "平台网关超时/维护中 (504)")

    def test_activity_locked(self):
        err = "Errors: LockedEntityException: Offer Locked [MLB4897041997]"
        self.assertEqual(clean_error_message(err), "活动已锁定，平台禁止退出")

    def test_credibility_discount(self):
        err = "ERROR_CREDIBILITY_DISCOUNTED_PRICE - The discounted price is not credible."
        self.assertEqual(clean_error_message(err), "折后价未达近期成交价门槛")

    def test_item_not_eligible(self):
        err = "Errors: ITEM_NOT_ELIGIBLE - Item cannot be enrolled"
        self.assertEqual(clean_error_message(err), "未达活动受邀门槛")

    def test_already_in_promotion(self):
        err = "Item is ALREADY in promotion"
        self.assertEqual(clean_error_message(err), "已在活动中(自动跳过)")

    def test_promotion_type_required(self):
        err = "The promotion_type is required"
        self.assertEqual(clean_error_message(err), "缺少活动类型参数")

    def test_deleted_not_modifiable(self):
        err = "Cannot update item [status:active, has_bids:false] (deleted is not modifiable.)"
        self.assertEqual(clean_error_message(err), "商品活跃/出单中，禁止删除")

    def test_not_cbt_item(self):
        err = "item MCO4200590930 is not a cbt item"
        self.assertEqual(clean_error_message(err), "非全球CBT商品 (404)")

    def test_item_not_found_404(self):
        err = "Item with id MLB123 not found"
        self.assertEqual(format_clean_api_error(404, err), "商品或活动不存在 (404)")

    def test_can_not_identify_user_403(self):
        err = "Can not identify the user."
        self.assertEqual(clean_error_message(err), "站点无权限或账号不匹配 (403)")

    def test_forbidden_403(self):
        self.assertEqual(format_clean_api_error(403, "forbidden"), "站点访问受限 (403)")

    def test_invalid_grant_401(self):
        err = "invalid_grant: The provided authorization grant is invalid, expired, or revoked"
        self.assertEqual(clean_error_message(err), "店铺授权已过期 (401)")

    def test_rate_limit_429(self):
        err = "Too Many Requests: rate_limit_exceeded"
        self.assertEqual(clean_error_message(err), "平台限流，降速排队中 (429)")

    def test_network_errors(self):
        err = "RemoteDisconnected: Remote end closed connection without response"
        self.assertEqual(clean_error_message(err), "网络超时或中断")

    def test_reason_text_integration(self):
        err = "Oops! Something went wrong..."
        self.assertEqual(business_reason_text(err), "平台服务繁忙，请稍后重试 (500)")


if __name__ == "__main__":
    unittest.main()
