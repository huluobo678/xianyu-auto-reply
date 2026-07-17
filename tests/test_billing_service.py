import unittest
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from common.services.billing_service import BillingCatalogError, BillingService


class FakeSession:
    def __init__(self, scalars=None, entities=None, commit_error=None):
        self.scalar_values = list(scalars or [])
        self.entities = entities or {}
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.commit_error = commit_error

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def get(self, _model, entity_id):
        return self.entities.get(entity_id)

    def add(self, value):
        value.id = 99
        value.created_at = None
        self.added.append(value)

    async def commit(self):
        self.commits += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, _value):
        return None


class BillingServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_request_returns_existing_order(self):
        existing = SimpleNamespace(order_no='B1')
        session = FakeSession(scalars=[existing])
        order, duplicate = await BillingService(session).create_order(
            7, 'plan', 10, 'request-0001'
        )
        self.assertIs(order, existing)
        self.assertTrue(duplicate)
        self.assertEqual(session.added, [])
        self.assertEqual(session.commits, 0)

    async def test_plan_order_uses_database_amount_and_snapshot(self):
        price = SimpleNamespace(
            id=10, plan_id=2, enabled=True, billing_cycle='quarterly',
            duration_months=3, amount=Decimal('99.00'),
        )
        plan = SimpleNamespace(
            id=2, code='standard', name='Standard', enabled=True,
            is_free=False, account_limit=2, monthly_ai_quota=1000,
            feature_flags=['ai_reply'],
        )
        session = FakeSession(scalars=[None], entities={10: price, 2: plan})
        order, duplicate = await BillingService(session).create_order(
            7, 'plan', 10, 'request-0002'
        )
        self.assertFalse(duplicate)
        self.assertEqual(order.amount, Decimal('99.00'))
        self.assertEqual(order.product_snapshot['duration_months'], 3)
        self.assertEqual(order.product_snapshot['monthly_ai_quota'], 1000)
        self.assertEqual(session.commits, 1)

    async def test_free_plan_cannot_be_purchased(self):
        price = SimpleNamespace(
            id=10, plan_id=1, enabled=True, billing_cycle='monthly',
            duration_months=1, amount=Decimal('0.00'),
        )
        free = SimpleNamespace(id=1, enabled=True, is_free=True)
        session = FakeSession(scalars=[None], entities={10: price, 1: free})
        with self.assertRaises(BillingCatalogError):
            await BillingService(session).create_order(
                7, 'plan', 10, 'request-0003'
            )

    async def test_concurrent_duplicate_returns_winning_order(self):
        price = SimpleNamespace(
            id=10, plan_id=2, enabled=True, billing_cycle='monthly',
            duration_months=1, amount=Decimal('39.00'),
        )
        plan = SimpleNamespace(
            id=2, code='standard', name='Standard', enabled=True,
            is_free=False, account_limit=2, monthly_ai_quota=1000,
            feature_flags=['ai_reply'],
        )
        winner = SimpleNamespace(order_no='B-WINNER')
        error = IntegrityError('insert', {}, Exception('duplicate'))
        session = FakeSession(
            scalars=[None, winner], entities={10: price, 2: plan},
            commit_error=error,
        )
        order, duplicate = await BillingService(session).create_order(
            7, 'plan', 10, 'request-0004'
        )
        self.assertIs(order, winner)
        self.assertTrue(duplicate)
        self.assertEqual(session.rollbacks, 1)


if __name__ == '__main__':
    unittest.main()
