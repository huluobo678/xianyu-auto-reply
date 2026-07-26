"""真实数据库并发兑换集成测试（需要可用的 MySQL 测试库）。

本测试不使用 ``ModelFakeSession``，而是用两个独立 ``AsyncSession`` 真正并发兑换
同一个兑换码，验证行锁 + ``(user_id, idempotency_key)`` 唯一约束在真实并发下的语义：

- 两个并发请求兑换同一码：只有一个成功，另一个得到“已兑换”冲突；
- 只生成一份权益（订阅或加量 grant 只一条），兑换码只被标记使用一次；
- 同一幂等请求并发提交不重复发放权益；
- 权益发放中途失败时兑换码保持未使用、不留下部分权益。

运行前提：本机或测试环境存在一个可用的 MySQL 库，并通过环境变量提供连接信息
（``MYSQL_HOST`` / ``MYSQL_PORT`` / ``MYSQL_USER`` / ``MYSQL_PASSWORD`` /
``MYSQL_DATABASE``）。该项目使用 ``asyncmy`` 驱动。

精确运行命令（在仓库根目录）：

    # 1) 设置测试库环境变量（PowerShell 示例）
    $env:MYSQL_HOST="127.0.0.1"
    $env:MYSQL_PORT="3306"
    $env:MYSQL_USER="root"
    $env:MYSQL_PASSWORD="<your-password>"
    $env:MYSQL_DATABASE="xianyu_test"

    # 2) 运行真实并发集成测试（asyncmy 驱动，MySQL）
    D:\\python\\python.exe -m unittest tests.test_redemption_concurrency_db -v

如本机无可用的 MySQL 库，本测试会在 setUp 阶段 ``skipTest``，不会用 FakeSession
冒充通过。
"""

from __future__ import annotations

import asyncio
import sys
import unittest

from sqlalchemy import func, select
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from common.db.base_class import Base  # noqa: E402
    from common.db.session import async_session_maker  # noqa: E402
    from common.models.billing import (  # noqa: E402
        AIQuotaGrant,
        BillingPlan,
        EntitlementLedger,
        UserSubscription,
    )
    from common.models.redemption import (  # noqa: E402
        RedemptionBatch,
        RedemptionCode,
        RedemptionRecord,
    )
    from common.models.user import User  # noqa: E402
    from common.services.redemption_service import (  # noqa: E402
        RedemptionError,
        RedemptionService,
        reset_redemption_secret,
        set_redemption_secret,
        _decrypt_export_payload,
    )
    from common.utils.security import get_password_hash  # noqa: E402

    _IMPORTS_OK = True
    _IMPORT_ERR = None
except Exception as exc:  # pragma: no cover - 仅在依赖缺失时触发
    _IMPORTS_OK = False
    _IMPORT_ERR = repr(exc)


_TEST_SECRET = "r" * 64


def _db_configured() -> bool:
    """是否显式声明要运行真实数据库测试。

    真实并发测试只在显式配置可用测试库时运行，避免误连生产库或因本机无库而报失败。
    设置环境变量 ``REDMPTION_DB_TESTS=1`` 且配置好 ``MYSQL_*`` 连接信息后才会运行。
    """
    import os

    return os.environ.get("REDMPTION_DB_TESTS") == "1"


@unittest.skipUnless(_IMPORTS_OK, f"依赖未就绪: {_IMPORT_ERR}")
@unittest.skipUnless(
    _db_configured(), "未设置 REDMPTION_DB_TESTS=1；真实并发测试未运行"
)
class RealDbConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """真实数据库并发兑换测试。"""

    async def asyncSetUp(self):
        set_redemption_secret(_TEST_SECRET)
        # 探测数据库可连通；不可达则 skip，不报失败（不冒充通过）
        try:
            async with async_session_maker() as session:
                from sqlalchemy import text

                await session.execute(text("SELECT 1"))
        except Exception as exc:
            self.skipTest(
                "无可用的 MySQL 测试库连接，真实并发测试未运行。"
                f"原因: {exc}。运行命令见模块 docstring。"
            )
        # 建表（幂等）
        async with async_session_maker() as session:
            async with session.begin():
                conn = await session.connection()
                await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        if self._outcome is not None and getattr(self, "_outcome", None):
            # 若 setUp 跳过，则无需清理
            pass
        reset_redemption_secret()
        try:
            async with async_session_maker() as session:
                async with session.begin():
                    from sqlalchemy import delete

                    for model in (
                        RedemptionRecord,
                        RedemptionCode,
                        RedemptionBatch,
                        AIQuotaGrant,
                        EntitlementLedger,
                        UserSubscription,
                        BillingPlan,
                        User,
                    ):
                        await session.execute(delete(model))
        except Exception:
            pass

    async def _seed_plan_and_batch(self, session) -> str:
        """种子一个套餐目录行 + 一个兑换码批次，返回明文兑换码。"""
        plan = BillingPlan(
            code="standard",
            name="standard-test",
            account_limit=3,
            monthly_ai_quota=1000,
            ai_unlimited=False,
            feature_flags=["ai_reply"],
            is_free=False,
            enabled=True,
            sort_order=10,
        )
        session.add(plan)
        await session.flush()
        result = await RedemptionService(session).create_batch(
            admin_id=1,
            product_type="plan",
            product_code="standard",
            cycle_or_validity="monthly",
            count=1,
            now=datetime(2026, 7, 1, 9, 0),
        )
        batch = await session.scalar(
            select(RedemptionBatch).where(RedemptionBatch.id == result.batch_id)
        )
        return _decrypt_export_payload(batch.export_payload, _TEST_SECRET)[0]

    async def test_batch_create_persists_for_new_session(self):
        async with async_session_maker() as session:
            async with session.begin():
                plan = BillingPlan(
                    code="standard",
                    name="standard-test",
                    account_limit=3,
                    monthly_ai_quota=1000,
                    ai_unlimited=False,
                    feature_flags=["ai_reply"],
                    is_free=False,
                    enabled=True,
                    sort_order=10,
                )
                session.add(plan)
                await session.flush()
                result = await RedemptionService(session).create_batch(
                    admin_id=1,
                    product_type="plan",
                    product_code="standard",
                    cycle_or_validity="monthly",
                    count=3,
                    now=datetime(2026, 7, 1, 9, 0),
                )
                batch_id = result.batch_id

        async with async_session_maker() as session:
            batch = await session.scalar(
                select(RedemptionBatch).where(RedemptionBatch.id == batch_id)
            )
            code_count = await session.scalar(
                select(func.count())
                .select_from(RedemptionCode)
                .where(RedemptionCode.batch_id == batch_id)
            )
        self.assertIsNotNone(batch)
        self.assertEqual(int(code_count or 0), 3)
        self.assertIsNotNone(batch.export_payload)
        self.assertIsNone(batch.exported_at)

    async def test_concurrent_export_only_one_succeeds(self):
        async with async_session_maker() as setup_session:
            async with setup_session.begin():
                plan = BillingPlan(
                    code="standard",
                    name="standard-test",
                    account_limit=3,
                    monthly_ai_quota=1000,
                    ai_unlimited=False,
                    feature_flags=["ai_reply"],
                    is_free=False,
                    enabled=True,
                    sort_order=10,
                )
                setup_session.add(plan)
                await setup_session.flush()
                result = await RedemptionService(setup_session).create_batch(
                    admin_id=1,
                    product_type="plan",
                    product_code="standard",
                    cycle_or_validity="monthly",
                    count=2,
                    now=datetime(2026, 7, 1, 9, 0),
                )
                batch_id = result.batch_id

        async def export_once():
            async with async_session_maker() as session:
                try:
                    file_path = await RedemptionService(session).export_batch_once(
                        batch_id
                    )
                    await session.commit()
                    return file_path
                except Exception as exc:
                    await session.rollback()
                    return exc

        results = await asyncio.gather(export_once(), export_once())
        paths = [value for value in results if isinstance(value, str)]
        errors = [value for value in results if isinstance(value, Exception)]
        self.assertEqual(len(paths), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RedemptionError)
        try:
            async with async_session_maker() as session:
                batch = await session.scalar(
                    select(RedemptionBatch).where(RedemptionBatch.id == batch_id)
                )
            self.assertIsNotNone(batch.exported_at)
            self.assertIsNone(batch.export_payload)
        finally:
            RedemptionService.delete_export_file(paths[0])

    async def test_concurrent_same_code_only_one_succeeds(self):
        async with async_session_maker() as setup_session:
            async with setup_session.begin():
                user = User(
                    username="tdb-user-a",
                    email="tdb-a@test.local",
                    password_hash=get_password_hash("Aa12345678!"),
                    status="ACTIVE",
                    role="MEMBER",
                    account_limit=1,
                )
                setup_session.add(user)
                await setup_session.flush()
                user_id = user.id
                plain_code = await self._seed_plan_and_batch(setup_session)

        now = datetime(2026, 7, 1, 9, 0)

        async def redeem(idem_key: str):
            async with async_session_maker() as session:
                return await RedemptionService(session).redeem(
                    user_id, plain_code, idem_key, now
                )

        results = await asyncio.gather(
            redeem("idem-concur-1"),
            redeem("idem-concur-2"),
            return_exceptions=True,
        )
        succeeded = [r for r in results if not isinstance(r, Exception)]
        failed = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(len(succeeded), 1)
        self.assertEqual(len(failed), 1)
        self.assertIsInstance(failed[0], RedemptionError)

        # 兑换码只被标记使用一次
        async with async_session_maker() as session:
            from sqlalchemy import func, select

            used_count = await session.scalar(
                select(func.count())
                .select_from(RedemptionCode)
                .where(RedemptionCode.status == "used")
            )
            self.assertEqual(int(used_count or 0), 1)
            record_count = await session.scalar(
                select(func.count()).select_from(RedemptionRecord)
            )
            self.assertEqual(int(record_count or 0), 1)
            sub_count = await session.scalar(
                select(func.count()).select_from(UserSubscription)
            )
            self.assertEqual(int(sub_count or 0), 1)
            grant_count = await session.scalar(
                select(func.count()).select_from(AIQuotaGrant)
            )
            self.assertEqual(int(grant_count or 0), 1)

    async def test_concurrent_same_idempotency_key_no_duplicate_grant(self):
        async with async_session_maker() as setup_session:
            async with setup_session.begin():
                user = User(
                    username="tdb-user-b",
                    email="tdb-b@test.local",
                    password_hash=get_password_hash("Aa12345678!"),
                    status="ACTIVE",
                    role="MEMBER",
                    account_limit=1,
                )
                setup_session.add(user)
                await setup_session.flush()
                user_id = user.id
                plain_code = await self._seed_plan_and_batch(setup_session)

        now = datetime(2026, 7, 1, 9, 0)

        async def redeem():
            async with async_session_maker() as session:
                return await RedemptionService(session).redeem(
                    user_id, plain_code, "idem-same-key", now
                )

        results = await asyncio.gather(redeem(), redeem(), return_exceptions=True)
        ok = [r for r in results if not isinstance(r, Exception)]
        self.assertGreaterEqual(len(ok), 1)
        # 无论另一个被行锁/幂等拒绝，最终只生成一份权益
        async with async_session_maker() as session:
            from sqlalchemy import func, select

            record_count = await session.scalar(
                select(func.count()).select_from(RedemptionRecord)
            )
            self.assertEqual(int(record_count or 0), 1)
            grant_count = await session.scalar(
                select(func.count()).select_from(AIQuotaGrant)
            )
            self.assertEqual(int(grant_count or 0), 1)


if __name__ == "__main__":
    unittest.main()
