import unittest

from sqlalchemy import inspect

from common.models._exports import (
    RedemptionBatch,
    RedemptionCode,
    RedemptionRecord,
)
from common.models.billing import (
    AIQuotaGrant,
    AIQuotaPackage,
    BillingPlan,
    UserSubscription,
)


class RedemptionModelMigrationTests(unittest.TestCase):
    """阶段一：模型与迁移字段注册校验（不连真实库，仅校验 ORM 元数据）。"""

    def test_ai_unlimited_field_present_on_three_models(self):
        for model in (BillingPlan, AIQuotaPackage, AIQuotaGrant):
            self.assertIn("ai_unlimited", inspect(model).columns.keys())
            self.assertFalse(
                model.__table__.columns["ai_unlimited"].nullable is False
                and model.__table__.columns["ai_unlimited"].server_default is None
            )

    def test_ai_unlimited_defaults_to_zero(self):
        # server_default 必须为 '0'，避免旧数据被误判为无限
        for model in (BillingPlan, AIQuotaPackage, AIQuotaGrant):
            col = model.__table__.columns["ai_unlimited"]
            self.assertEqual(str(col.server_default.arg), "0")

    def test_user_subscription_pending_downgrade_fields(self):
        cols = set(inspect(UserSubscription).columns.keys())
        expected = {
            "pending_plan_id",
            "pending_plan_code",
            "pending_billing_cycle",
            "pending_duration_months",
            "pending_account_limit",
            "pending_monthly_ai_quota",
            "pending_ai_unlimited",
            "pending_feature_snapshot",
            "pending_source",
        }
        self.assertTrue(expected.issubset(cols))

    def test_redemption_tables_registered_in_metadata(self):
        tables = BillingPlan.metadata.tables
        self.assertIn(RedemptionBatch.__tablename__, tables)
        self.assertIn(RedemptionCode.__tablename__, tables)
        self.assertIn(RedemptionRecord.__tablename__, tables)

    def test_redemption_code_digest_unique_constraint(self):
        constraints = {
            c.name
            for c in RedemptionCode.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn("uk_redemption_code_digest", constraints)

    def test_redemption_record_idempotency_unique(self):
        constraints = {
            c.name
            for c in RedemptionRecord.__table__.constraints
            if c.__class__.__name__ == "UniqueConstraint"
        }
        self.assertIn("uk_redemption_record_idempotency", constraints)

    def test_redemption_models_exported(self):
        from common.models import _exports

        for name in ("RedemptionBatch", "RedemptionCode", "RedemptionRecord"):
            self.assertIn(name, _exports.__all__)
            self.assertTrue(hasattr(_exports, name))


if __name__ == "__main__":
    unittest.main()
