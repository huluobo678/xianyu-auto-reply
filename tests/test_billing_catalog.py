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
from common.services.billing_catalog import (
    AI_QUOTA_PACKAGES,
    PLAN_CATALOG,
    PLAN_PRICES,
)


class BillingCatalogTests(unittest.TestCase):
    def test_plan_catalog_matches_approved_limits(self):
        plans = {plan['code']: plan for plan in PLAN_CATALOG}
        self.assertEqual(plans['free']['account_limit'], 1)
        self.assertEqual(plans['standard']['monthly_ai_quota'], 1000)
        self.assertEqual(plans['merchant']['monthly_ai_quota'], 5000)
        self.assertEqual(plans['enterprise']['monthly_ai_quota'], 15000)

    def test_monthly_quarterly_and_yearly_prices(self):
        prices = {(code, cycle): (months, amount) for code, cycle, months, amount in PLAN_PRICES}
        self.assertEqual(prices['standard', 'monthly'], (1, Decimal('39.00')))
        self.assertEqual(prices['standard', 'quarterly'], (3, Decimal('99.00')))
        self.assertEqual(prices['merchant', 'quarterly'], (3, Decimal('269.00')))
        self.assertEqual(prices['enterprise', 'yearly'], (12, Decimal('1999.00')))

    def test_quota_packages_include_bonus_replies(self):
        packages = {code: (base + bonus, amount, days) for code, _name, base, bonus, amount, days, _sort in AI_QUOTA_PACKAGES}
        self.assertEqual(packages['ai_light'], (600, Decimal('9.90'), 90))
        self.assertEqual(packages['ai_regular'], (1200, Decimal('16.90'), 90))
        self.assertEqual(packages['ai_merchant'], (6000, Decimal('59.00'), 180))
        self.assertEqual(packages['ai_large'], (12000, Decimal('99.00'), 180))

    def test_billing_order_keeps_product_snapshot(self):
        columns = BillingOrder.__table__.columns
        self.assertIn('product_snapshot', columns)
        self.assertIn('channel_trade_no', columns)
        self.assertNotIn('payment_payload', columns)

    def test_subscription_and_idempotency_constraints(self):
        subscription_unique = {column.name for constraint in UserSubscription.__table__.constraints if constraint.__class__.__name__ == 'UniqueConstraint' for column in constraint.columns}
        self.assertIn('user_id', subscription_unique)
        grant_constraints = {constraint.name for constraint in AIQuotaGrant.__table__.constraints}
        ledger_constraints = {constraint.name for constraint in EntitlementLedger.__table__.constraints}
        self.assertIn('uk_ai_quota_grant_idempotency', grant_constraints)
        self.assertIn('uk_entitlement_ledger_idempotency', ledger_constraints)

    def test_all_billing_tables_are_registered(self):
        expected = {
            BillingPlan.__tablename__, BillingPlanPrice.__tablename__,
            BillingOrder.__tablename__, UserSubscription.__tablename__,
            AIQuotaGrant.__tablename__, EntitlementLedger.__tablename__,
        }
        self.assertTrue(expected.issubset(BillingPlan.metadata.tables))


if __name__ == '__main__':
    unittest.main()
