"""支付宝充值通知事务、幂等和真实数据库并发测试。"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))

from sqlalchemy import delete, func, select  # noqa: E402

from app.services.recharge_service import BALANCE_KEY, RechargeService  # noqa: E402
from common.db.session import async_session_maker  # noqa: E402
from common.models.fund_flow import FundFlow  # noqa: E402
from common.models.recharge_order import RechargeOrder  # noqa: E402
from common.models.user import User  # noqa: E402
from common.models.user_setting import UserSetting  # noqa: E402
from common.utils.security import get_password_hash  # noqa: E402


_VALID_CONFIG = {
    "app_id": "test-app",
    "private_key": "test-private",
    "alipay_public_key": "test-public",
    "seller_id": "seller-1",
}


def _notify(**overrides):
    data = {
        "out_trade_no": "ORDER-1",
        "trade_no": "TRADE-1",
        "trade_status": "TRADE_SUCCESS",
        "seller_id": "seller-1",
        "total_amount": "12.34",
    }
    data.update(overrides)
    return data


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class NotifyFakeSession:
    def __init__(self, order=None, balance=None, *, commit_error=None):
        self.responses = [order, balance]
        self.added = []
        self.commit_error = commit_error
        self.commit_calls = 0
        self.rollback_calls = 0
        self.flush_calls = 0

    async def execute(self, _statement):
        return _Result(self.responses.pop(0) if self.responses else None)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_calls += 1

    async def commit(self):
        self.commit_calls += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rollback_calls += 1


def _pending_order():
    return SimpleNamespace(
        id=1,
        order_no="ORDER-1",
        user_id=7,
        amount="12.34",
        status="pending",
        trade_no=None,
        paid_at=None,
    )


class RechargeNotifyUnitTests(unittest.IsolatedAsyncioTestCase):
    async def _handle(self, session, notify=None, *, verified=True, trade_success=True):
        with (
            patch(
                "app.services.recharge_service.AlipayService.load_config",
                new=AsyncMock(return_value=dict(_VALID_CONFIG)),
            ),
            patch(
                "app.services.recharge_service.AlipayService.verify_notify",
                return_value=verified,
            ),
            patch(
                "app.services.recharge_service.AlipayService.is_trade_success",
                return_value=trade_success,
            ),
        ):
            return await RechargeService(session).handle_alipay_notify(
                notify or _notify()
            )

    async def test_valid_notify_credits_once_and_commits_atomically(self):
        order = _pending_order()
        balance = SimpleNamespace(value="1.00")
        session = NotifyFakeSession(order, balance)
        self.assertTrue(await self._handle(session))
        self.assertEqual(balance.value, "13.34")
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.trade_no, "TRADE-1")
        self.assertIsNotNone(order.paid_at)
        flows = [item for item in session.added if isinstance(item, FundFlow)]
        self.assertEqual(len(flows), 1)
        self.assertEqual(flows[0].balance_before, "1.00")
        self.assertEqual(flows[0].balance_after, "13.34")
        self.assertEqual(session.flush_calls, 1)
        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(session.rollback_calls, 0)

    async def test_duplicate_paid_notify_is_idempotent(self):
        order = _pending_order()
        order.status = "paid"
        session = NotifyFakeSession(order)
        self.assertTrue(await self._handle(session))
        self.assertEqual(session.added, [])
        self.assertEqual(session.commit_calls, 0)

    async def test_seller_mismatch_rejected_without_credit(self):
        session = NotifyFakeSession(_pending_order())
        self.assertFalse(await self._handle(session, _notify(seller_id="other")))
        self.assertEqual(session.added, [])
        self.assertEqual(session.commit_calls, 0)

    async def test_amount_mismatch_rejected_without_credit(self):
        session = NotifyFakeSession(_pending_order())
        self.assertFalse(await self._handle(session, _notify(total_amount="99.00")))
        self.assertEqual(session.added, [])

    async def test_invalid_amount_rejected_safely(self):
        session = NotifyFakeSession(_pending_order())
        self.assertFalse(await self._handle(session, _notify(total_amount="bad")))
        self.assertEqual(session.added, [])

    async def test_invalid_signature_rejected(self):
        session = NotifyFakeSession(_pending_order())
        self.assertFalse(await self._handle(session, verified=False))
        self.assertEqual(len(session.responses), 2)
        self.assertEqual(session.added, [])

    async def test_non_success_status_does_not_credit(self):
        session = NotifyFakeSession(_pending_order())
        self.assertTrue(
            await self._handle(
                session,
                _notify(trade_status="WAIT_BUYER_PAY"),
                trade_success=False,
            )
        )
        self.assertEqual(session.added, [])
        self.assertEqual(session.commit_calls, 0)

    async def test_mid_transaction_failure_rolls_back(self):
        order = _pending_order()
        balance = SimpleNamespace(value="1.00")
        session = NotifyFakeSession(
            order, balance, commit_error=RuntimeError("commit failed")
        )
        self.assertFalse(await self._handle(session))
        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(session.rollback_calls, 1)


@unittest.skipUnless(
    os.environ.get("RECHARGE_DB_TESTS") == "1",
    "未设置 RECHARGE_DB_TESTS=1；真实充值并发测试未运行",
)
class RechargeNotifyRealDbTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.suffix = uuid.uuid4().hex[:12]
        self.order_no = f"RECHARGE-{self.suffix}"
        self.username = f"recharge_{self.suffix}"
        self.email = f"{self.username}@example.test"
        try:
            async with async_session_maker() as session:
                async with session.begin():
                    user = User(
                        username=self.username,
                        email=self.email,
                        password_hash=get_password_hash("TestPass123!"),
                    )
                    session.add(user)
                    await session.flush()
                    self.user_id = user.id
                    session.add_all(
                        [
                            UserSetting(
                                user_id=user.id,
                                key=BALANCE_KEY,
                                value="0.00",
                                description="测试余额",
                            ),
                            RechargeOrder(
                                order_no=self.order_no,
                                user_id=user.id,
                                amount="12.34",
                                status="pending",
                            ),
                        ]
                    )
        except Exception as exc:
            self.skipTest(f"真实 MySQL 测试库不可用: {exc}")

    async def asyncTearDown(self):
        if not hasattr(self, "user_id"):
            return
        async with async_session_maker() as session:
            async with session.begin():
                await session.execute(
                    delete(FundFlow).where(FundFlow.user_id == self.user_id)
                )
                await session.execute(
                    delete(RechargeOrder).where(RechargeOrder.order_no == self.order_no)
                )
                await session.execute(
                    delete(UserSetting).where(UserSetting.user_id == self.user_id)
                )
                await session.execute(delete(User).where(User.id == self.user_id))

    async def test_two_sessions_credit_only_once(self):
        notify = _notify(out_trade_no=self.order_no)

        async def handle():
            async with async_session_maker() as session:
                return await RechargeService(session).handle_alipay_notify(notify)

        with (
            patch(
                "app.services.recharge_service.AlipayService.load_config",
                new=AsyncMock(return_value=dict(_VALID_CONFIG)),
            ),
            patch(
                "app.services.recharge_service.AlipayService.verify_notify",
                return_value=True,
            ),
            patch(
                "app.services.recharge_service.AlipayService.is_trade_success",
                return_value=True,
            ),
        ):
            results = await asyncio.gather(handle(), handle())

        self.assertEqual(results, [True, True])
        async with async_session_maker() as session:
            order = await session.scalar(
                select(RechargeOrder).where(RechargeOrder.order_no == self.order_no)
            )
            balance = await session.scalar(
                select(UserSetting).where(
                    UserSetting.user_id == self.user_id,
                    UserSetting.key == BALANCE_KEY,
                )
            )
            flow_count = await session.scalar(
                select(func.count(FundFlow.id)).where(
                    FundFlow.user_id == self.user_id,
                    FundFlow.description.contains(self.order_no),
                )
            )
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.trade_no, "TRADE-1")
        self.assertIsNotNone(order.paid_at)
        self.assertEqual(Decimal(balance.value), Decimal("12.34"))
        self.assertEqual(flow_count, 1)


if __name__ == "__main__":
    unittest.main()
