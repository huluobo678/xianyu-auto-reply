"""兑换码管理员接口测试（阶段三）。

覆盖 ``/api/v1/admin/redemption/*`` 管理员接口：

1. 普通用户访问返回 403（路由级 ``get_current_admin_user`` 守卫）；
2. 管理员可创建并提交批次（响应不含完整兑换码）；
3. 管理员可查询批次（列表不含完整兑换码）；
4. 管理员可一次性导出完整兑换码；
5. 导出后不得再次导出（服务层 ``exported_at`` 已标记 → 再次导出拒绝）；
6. 管理员可禁用批次；
7. 管理员可禁用单码；
8. 审计接口不返回完整兑换码；
9. 年卡兑换码在创建批次时即被拒绝（第一版仅月卡/季卡）。

完整兑换码不写入日志/审计/异常；导出文件为临时文件，不提交 Git。
密码学安全随机生成由阶段一 ``test_redemption_secret_service`` 等覆盖，此处聚焦管理 HTTP 接口。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend-web"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.routes import redemption as redemption_routes  # noqa: E402
from common.services.redemption_service import (  # noqa: E402
    BatchCreateResult,
    RedemptionError,
)

_ADMIN = SimpleNamespace(id=1, role="admin", is_admin=True)
_PLAIN_CODE = "FULLPLAINCODE1234567890AB"


class AdminFakeSession:
    """记录管理接口事务调用的最小会话替身。"""

    def __init__(self, *, commit_error: Exception | None = None):
        self.commit_error = commit_error
        self.commit_calls = 0
        self.rollback_calls = 0

    async def scalar(self, _statement):
        return None

    async def execute(self, _statement):
        return SimpleNamespace(
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
        )

    async def commit(self):
        self.commit_calls += 1
        if self.commit_error:
            raise self.commit_error

    async def rollback(self):
        self.rollback_calls += 1

    async def flush(self):
        return None


def _build_app(*, admin_user=_ADMIN, session=None) -> FastAPI:
    app = FastAPI()
    app.include_router(redemption_routes.router, prefix="/api/v1")
    app.include_router(redemption_routes.admin_router, prefix="/api/v1/admin")
    app.dependency_overrides[deps.get_current_active_user] = lambda: SimpleNamespace(
        id=7, role="user", is_admin=False
    )
    app.dependency_overrides[deps.get_current_admin_user] = lambda: admin_user
    app.dependency_overrides[deps.get_db_session] = lambda: (
        session or AdminFakeSession()
    )
    return app


class AdminAccessTests(unittest.TestCase):
    """普通用户访问管理接口返回 403。"""

    def test_non_admin_create_batch_returns_403(self):
        def _deny():
            raise HTTPException(status_code=403, detail="需要管理员权限")

        app = FastAPI()
        app.include_router(redemption_routes.admin_router, prefix="/api/v1/admin")
        app.dependency_overrides[deps.get_current_admin_user] = _deny
        app.dependency_overrides[deps.get_db_session] = lambda: AdminFakeSession()
        client = TestClient(app)
        resp = client.post(
            "/api/v1/admin/redemption/batches",
            json={
                "product_type": "plan",
                "product_code": "standard",
                "billing_cycle": "monthly",
                "count": 5,
            },
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_admin_audit_returns_403(self):
        def _deny():
            raise HTTPException(status_code=403, detail="需要管理员权限")

        app = FastAPI()
        app.include_router(redemption_routes.admin_router, prefix="/api/v1/admin")
        app.dependency_overrides[deps.get_current_admin_user] = _deny
        client = TestClient(app)
        resp = client.get("/api/v1/admin/redemption/audit")
        self.assertEqual(resp.status_code, 403)


class AdminBatchTests(unittest.TestCase):
    """创建 / 查询 / 年卡拒绝 / 导出一次性 / 禁用 / 审计。"""

    def test_admin_create_batch_commits_and_returns_no_codes(self):
        session = AdminFakeSession()
        app = _build_app(session=session)
        client = TestClient(app)
        canned = BatchCreateResult(
            batch_id=1,
            batch_no="RB1",
            product_type="plan",
            product_code="standard",
        )
        with patch(
            "app.api.routes.redemption.RedemptionService.create_batch",
            new=AsyncMock(return_value=canned),
        ):
            resp = client.post(
                "/api/v1/admin/redemption/batches",
                json={
                    "product_type": "plan",
                    "product_code": "standard",
                    "billing_cycle": "monthly",
                    "count": 1,
                },
            )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["batch_id"], 1)
        self.assertNotIn("codes", body["data"])
        self.assertNotIn(_PLAIN_CODE, resp.text)
        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(session.rollback_calls, 0)

    def test_create_batch_commit_failure_rolls_back_without_plaintext(self):
        session = AdminFakeSession(commit_error=RuntimeError("commit failed"))
        app = _build_app(session=session)
        client = TestClient(app, raise_server_exceptions=False)
        canned = BatchCreateResult(
            batch_id=1,
            batch_no="RB1",
            product_type="plan",
            product_code="standard",
        )
        with patch(
            "app.api.routes.redemption.RedemptionService.create_batch",
            new=AsyncMock(return_value=canned),
        ):
            resp = client.post(
                "/api/v1/admin/redemption/batches",
                json={
                    "product_type": "plan",
                    "product_code": "standard",
                    "billing_cycle": "monthly",
                    "count": 1,
                },
            )
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn(_PLAIN_CODE, resp.text)
        self.assertEqual(session.commit_calls, 1)
        self.assertEqual(session.rollback_calls, 1)

    def test_yearly_plan_batch_rejected(self):
        app = _build_app()
        client = TestClient(app)
        # 不 patch 服务：年卡在校验阶段即被拒，不会触达服务
        resp = client.post(
            "/api/v1/admin/redemption/batches",
            json={
                "product_type": "plan",
                "product_code": "standard",
                "billing_cycle": "yearly",
                "count": 1,
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_list_batches_has_no_full_codes(self):
        app = _build_app()
        client = TestClient(app)
        batches_view = [
            {
                "batch_id": 1,
                "batch_no": "RB1",
                "product_type": "plan",
                "product_code": "standard",
                "quantity": 5,
                "generated_count": 5,
                "used_count": 0,
                "disabled": False,
                "exported_at": None,
            }
        ]
        with patch(
            "app.api.routes.redemption.RedemptionService.list_batches",
            new=AsyncMock(return_value=batches_view),
        ):
            resp = client.get("/api/v1/admin/redemption/batches")
        self.assertEqual(resp.status_code, 200)
        text = resp.text
        # 列表不含完整明文兑换码
        self.assertNotIn(_PLAIN_CODE, text)
        self.assertNotIn("codes", resp.json()["data"][0])

    def test_export_once_then_second_rejected(self):
        app = _build_app()
        client = TestClient(app)
        # 临时导出文件（不提交 Git）
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tmp.write(_PLAIN_CODE + "\n")
        tmp.flush()
        tmp.close()
        # 第一次导出成功返回文件路径；第二次服务层标记 exported_at 后抛 RedemptionError
        with patch(
            "app.api.routes.redemption.RedemptionService.export_batch_once",
            new=AsyncMock(
                side_effect=[tmp.name, RedemptionError("已导出，仅可查看一次")]
            ),
        ):
            first = client.post("/api/v1/admin/redemption/batches/1/export")
            second = client.post("/api/v1/admin/redemption/batches/1/export")
        self.assertEqual(first.status_code, 200)
        self.assertIn(_PLAIN_CODE, first.text)
        # 第二次不得再次导出完整兑换码
        self.assertEqual(second.status_code, 400)

    def test_export_commit_failure_rolls_back_and_deletes_file(self):
        session = AdminFakeSession(commit_error=RuntimeError("commit failed"))
        app = _build_app(session=session)
        client = TestClient(app, raise_server_exceptions=False)
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tmp.write(_PLAIN_CODE + "\n")
        tmp.flush()
        tmp.close()
        with patch(
            "app.api.routes.redemption.RedemptionService.export_batch_once",
            new=AsyncMock(return_value=tmp.name),
        ):
            resp = client.post("/api/v1/admin/redemption/batches/1/export")
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(session.rollback_calls, 1)
        self.assertFalse(Path(tmp.name).exists())

    def test_admin_can_disable_batch(self):
        app = _build_app()
        client = TestClient(app)
        with patch(
            "app.api.routes.redemption.RedemptionService.disable_batch",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/v1/admin/redemption/batches/1/disable")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_admin_can_disable_code(self):
        app = _build_app()
        client = TestClient(app)
        with patch(
            "app.api.routes.redemption.RedemptionService.disable_code",
            new=AsyncMock(return_value=None),
        ):
            resp = client.post("/api/v1/admin/redemption/codes/5/disable")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

    def test_audit_has_no_full_code(self):
        app = _build_app()
        client = TestClient(app)
        audit_view = [
            {
                "record_id": 1,
                "batch_id": 1,
                "code_id": 2,
                "user_id": 7,
                "product_type": "plan",
                "product_code": "standard",
                "created_at": None,
            }
        ]
        with patch(
            "app.api.routes.redemption.RedemptionService.audit",
            new=AsyncMock(return_value=audit_view),
        ):
            resp = client.get("/api/v1/admin/redemption/audit")
        self.assertEqual(resp.status_code, 200)
        text = resp.text
        # 审计不返回完整兑换码 / 明文 / 摘要
        self.assertNotIn(_PLAIN_CODE, text)
        self.assertNotIn("code_digest", text)
        self.assertNotIn("codes", text)
        record = resp.json()["data"][0]
        self.assertNotIn("code", record)
        self.assertNotIn("codes", record)


if __name__ == "__main__":
    unittest.main()
