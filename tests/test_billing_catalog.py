from decimal import Decimal
import unittest

from common.models.billing import (
    AIQuotaGrant,
    BillingOrder,
    BillingPlan,
    BillingPlanPrice,
    EntitlementLedger,
    UserSubscription,
)
from common.models.redemption import (
    RedemptionBatch,
    RedemptionCode,
    RedemptionRecord,
)
from common.services.billing_catalog import (
    AI_QUOTA_PACKAGES,
    DEPRECATED_QUOTA_PACKAGE_CODES,
    PLAN_CATALOG,
    PLAN_PRICES,
)


class BillingCatalogTests(unittest.TestCase):
    def test_plan_catalog_matches_approved_limits(self):
        plans = {plan["code"]: plan for plan in PLAN_CATALOG}
        # 账号上限 1 / 3 / 8 / 30
        self.assertEqual(plans["free"]["account_limit"], 1)
        self.assertEqual(plans["standard"]["account_limit"], 3)
        self.assertEqual(plans["merchant"]["account_limit"], 8)
        self.assertEqual(plans["enterprise"]["account_limit"], 30)
        # 月度额度
        self.assertEqual(plans["free"]["monthly_ai_quota"], 0)
        self.assertEqual(plans["standard"]["monthly_ai_quota"], 1000)
        self.assertEqual(plans["merchant"]["monthly_ai_quota"], 5000)
        # 企业版无限：monthly_ai_quota=0 且 ai_unlimited=True
        self.assertEqual(plans["enterprise"]["monthly_ai_quota"], 0)
        self.assertTrue(plans["enterprise"]["ai_unlimited"])
        # 非 enterprise 的 ai_unlimited 必须为 False
        for code in ("free", "standard", "merchant"):
            self.assertFalse(plans[code]["ai_unlimited"])

    def test_only_monthly_and_quarterly_prices_no_yearly(self):
        # 第一版不开放年卡
        cycles = {cycle for _code, cycle, _months, _amount in PLAN_PRICES}
        self.assertIn("monthly", cycles)
        self.assertIn("quarterly", cycles)
        self.assertNotIn("yearly", cycles)
        prices = {
            (code, cycle): (months, amount)
            for code, cycle, months, amount in PLAN_PRICES
        }
        self.assertEqual(prices["standard", "monthly"], (1, Decimal("39.00")))
        self.assertEqual(prices["standard", "quarterly"], (3, Decimal("99.00")))
        self.assertEqual(prices["merchant", "quarterly"], (3, Decimal("269.00")))
        self.assertEqual(prices["enterprise", "quarterly"], (3, Decimal("539.00")))

    def test_quota_packages_only_three_active(self):
        # 加量包仅常用包 / 商家包 / 无限包
        codes = {pkg[0] for pkg in AI_QUOTA_PACKAGES}
        self.assertEqual(codes, {"ai_regular", "ai_merchant", "ai_unlimited"})
        # 废弃包不在启用集合
        self.assertTrue(set(DEPRECATED_QUOTA_PACKAGE_CODES).isdisjoint(codes))
        packages = {
            code: (base + bonus, ai_unlimited, amount, days)
            for code, _name, base, bonus, ai_unlimited, amount, days, _sort in AI_QUOTA_PACKAGES
        }
        # 常用包 1200 次 / 12.9 元 / 90 天
        self.assertEqual(packages["ai_regular"], (1200, False, Decimal("12.90"), 90))
        # 商家包 6000 次 / 39 元 / 180 天
        self.assertEqual(packages["ai_merchant"], (6000, False, Decimal("39.00"), 180))
        # 无限包 79 元 / 30 天 / ai_unlimited
        self.assertEqual(packages["ai_unlimited"][1:], (True, Decimal("79.00"), 30))

    def test_billing_order_keeps_product_snapshot(self):
        columns = BillingOrder.__table__.columns
        self.assertIn("product_snapshot", columns)
        self.assertIn("channel_trade_no", columns)
        self.assertNotIn("payment_payload", columns)

    def test_subscription_and_idempotency_constraints(self):
        subscription_unique = {
            column.name
            for constraint in UserSubscription.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
            for column in constraint.columns
        }
        self.assertIn("user_id", subscription_unique)
        grant_constraints = {
            constraint.name for constraint in AIQuotaGrant.__table__.constraints
        }
        ledger_constraints = {
            constraint.name for constraint in EntitlementLedger.__table__.constraints
        }
        self.assertIn("uk_ai_quota_grant_idempotency", grant_constraints)
        self.assertIn("uk_entitlement_ledger_idempotency", ledger_constraints)

    def test_all_billing_tables_are_registered(self):
        expected = {
            BillingPlan.__tablename__,
            BillingPlanPrice.__tablename__,
            BillingOrder.__tablename__,
            UserSubscription.__tablename__,
            AIQuotaGrant.__tablename__,
            EntitlementLedger.__tablename__,
            RedemptionBatch.__tablename__,
            RedemptionCode.__tablename__,
            RedemptionRecord.__tablename__,
        }
        self.assertTrue(expected.issubset(BillingPlan.metadata.tables))


if __name__ == "__main__":
    unittest.main()
