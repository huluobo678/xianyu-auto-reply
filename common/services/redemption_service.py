"""兑换码服务：生成、一次性导出、核销与管理。

安全设计：
- 兑换码使用 ``secrets`` 生成密码学安全随机串，禁止连续编号/时间戳/可预测编码。
- 数据库只保存 ``HMAC-SHA256(code, redemption.hmac_secret)`` 摘要与尾 4 位，不保存完整码。
- 完整明文仅在 ``create_batch`` 返回一次；``export_batch_once`` 只能消费同一生成事务内
  的临时安全载荷（进程内短时缓存），绝不从摘要反推，也绝不为支持二次导出而把明文写库。
- 导出文件名使用 ``secrets`` 随机串，输出到专用临时目录（已加入 .gitignore），
  下载或超时后删除，不允许进入 Git。
- 核销在同一数据库事务内完成：HMAC→行锁兑换码→按 (user_id, idempotency_key)
  幂等校验→校验码状态→按批次冻结快照发放→标记 used→写记录→提交；失败整体回滚，
  兑换码不被消耗，不形成部分权益。
- 并发与幂等：行锁 + ``code_digest`` 唯一约束 + ``RedemptionRecord`` 的
  ``(user_id, idempotency_key)`` 唯一约束共同保证；幂等键按用户隔离，不依赖应用内锁。
- HMAC 密钥失败关闭：库不可用或未初始化时绝不回退进程内临时随机密钥，生成/核销直接失败。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.models.billing import AIQuotaPackage, BillingPlan
from common.models.redemption import (
    RedemptionBatch,
    RedemptionCode,
    RedemptionRecord,
)
from common.models.system_setting import SystemSetting
from common.services.billing_catalog import (
    AI_QUOTA_PACKAGES,
    ALLOWED_QUOTA_PACKAGE_CODES,
    PLAN_CATALOG,
)
from common.services.entitlement_grant_service import (
    EntitlementGrantError,
    EntitlementGrantService,
)
from common.utils.time_utils import get_beijing_now_naive

# 数据库中存储兑换码 HMAC 密钥的设置项 key（与 backend-web
# redemption_secret_service 共用，单一权威来源写在此处）。
REDEMPTION_HMAC_SETTING_KEY = "redemption.hmac_secret"

# 兑换码字符集与长度：大小写字母+数字，24 位；熵足够且便于人工抄录。
_CODE_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
_CODE_LENGTH = 24

# 导出专用临时目录（相对仓库根，已加入 .gitignore）。
_EXPORT_DIR_NAME = "redemption_exports"

# 进程内短时安全载荷：create_batch 生成后暂存明文，export_batch_once 一次性消费。
# 仅存在于内存，进程重启即丢失——明文永远不落库。
_pending_exports: dict[int, list[str]] = {}

# 进程内密钥缓存（由 backend-web bootstrap 持久化成功后注入；缺失时查库，库无则失败关闭）。
_injected_secret: str | None = None


class RedemptionError(RuntimeError):
    """兑换码业务错误（含可对外展示的提示）。"""


@dataclass(frozen=True)
class BatchCreateResult:
    batch_id: int
    batch_no: str
    product_type: str
    product_code: str
    codes: list[str]  # 一次性明文，仅此返回


@dataclass(frozen=True)
class RedeemResult:
    record_id: int
    code_id: int
    batch_id: int
    product_type: str
    product_code: str
    grant_id: int | None
    ledger_id: int | None
    action: str
    duplicate: bool = False


# ---------------- 密钥 / 摘要 ----------------


def set_redemption_secret(secret: str) -> None:
    """注入进程内 HMAC 密钥（由 backend-web bootstrap 调用，或测试注入）。"""
    global _injected_secret
    _injected_secret = secret


def reset_redemption_secret() -> None:
    """清除进程内密钥缓存（仅供测试）。"""
    global _injected_secret
    _injected_secret = None


async def _resolve_secret(session: AsyncSession) -> str:
    """获取 HMAC 密钥：失败关闭，绝不回退进程内临时随机密钥。

    - 进程内已注入（由 backend-web bootstrap 持久化成功后注入）→ 直接采用；
    - 否则查库：库中存在有效密钥 → 注入缓存并采用；
    - 库中无密钥或读取异常 → 抛 ``RedemptionError``，兑换码生成/核销失败关闭，
      不允许用临时随机密钥继续运行（否则进程重启后将无法验证历史兑换码）。

    密钥不得出现在日志、异常文本或接口响应中。
    """
    global _injected_secret
    if _injected_secret:
        return _injected_secret
    try:
        row = await session.scalar(
            select(SystemSetting.value).where(
                SystemSetting.key == REDEMPTION_HMAC_SETTING_KEY
            )
        )
    except Exception as exc:
        # 数据库读取失败：失败关闭，不回退临时密钥
        raise RedemptionError(
            "Redemption HMAC secret is unavailable; database read failed"
        ) from exc
    if row and len(row) >= 32:
        _injected_secret = row
        return _injected_secret
    # 数据库未初始化密钥：失败关闭
    raise RedemptionError(
        "Redemption HMAC secret is not initialized; redeem/generate rejected"
    )


def compute_code_digest(code: str, secret: str) -> str:
    """计算兑换码的 HMAC-SHA256 摘要（64 位 hex）。"""
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def _generate_code() -> str:
    """生成密码学安全随机兑换码（无连续编号/时间戳/可预测编码）。"""
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


# 权益快照必须覆盖的字段：兑换时只依赖快照发放权益，缺少任一字段即失败关闭。
_PLAN_SNAPSHOT_REQUIRED_FIELDS = (
    "plan_id",
    "plan_code",
    "billing_cycle",
    "duration_months",
    "account_limit",
    "monthly_ai_quota",
    "ai_unlimited",
    "feature_flags",
)
_PACKAGE_SNAPSHOT_REQUIRED_FIELDS = (
    "package_code",
    "base_quota",
    "bonus_quota",
    "total_quota",
    "validity_days",
    "ai_unlimited",
)


def _validate_plan_snapshot(snapshot: object) -> None:
    """校验套餐权益快照结构，缺字段时失败关闭。"""
    if not isinstance(snapshot, dict):
        raise RedemptionError("Redemption batch plan snapshot is corrupted")
    missing = [f for f in _PLAN_SNAPSHOT_REQUIRED_FIELDS if f not in snapshot]
    if missing:
        raise RedemptionError(
            "Redemption batch plan snapshot is incomplete; redeem rejected"
        )


def _validate_package_snapshot(snapshot: object) -> None:
    """校验加量包权益快照结构，缺字段时失败关闭。"""
    if not isinstance(snapshot, dict):
        raise RedemptionError("Redemption batch package snapshot is corrupted")
    missing = [f for f in _PACKAGE_SNAPSHOT_REQUIRED_FIELDS if f not in snapshot]
    if missing:
        raise RedemptionError(
            "Redemption batch package snapshot is incomplete; redeem rejected"
        )


def _export_dir() -> Path:
    base = Path(tempfile.gettempdir()) / "xianyu_redemption_exports"
    base.mkdir(parents=True, exist_ok=True)
    return base


class RedemptionService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------- 生成 ----------------

    async def create_batch(
        self,
        admin_id: int,
        product_type: str,
        product_code: str,
        cycle_or_validity: str | int,
        count: int,
        expires_at: datetime | None = None,
        now: datetime | None = None,
    ) -> BatchCreateResult:
        """生成一批兑换码，返回一次性明文列表。"""
        now = now or get_beijing_now_naive()
        if product_type not in ("plan", "ai_quota_package"):
            raise RedemptionError(f"Unsupported product type: {product_type}")
        if count <= 0 or count > 10000:
            raise RedemptionError("Batch count must be between 1 and 10000")

        secret = await _resolve_secret(self.session)

        if product_type == "plan":
            billing_cycle, duration_months, product_name, ai_unlimited = (
                self._resolve_plan_params(product_code, cycle_or_validity)
            )
            validity_days = None
            entitlement_snapshot = await self._build_plan_snapshot(
                product_code, billing_cycle, duration_months
            )
        else:
            validity_days, product_name, ai_unlimited = self._resolve_package_params(
                product_code, cycle_or_validity
            )
            billing_cycle = None
            duration_months = None
            entitlement_snapshot = await self._build_package_snapshot(product_code)

        batch_no = f"RB{now.strftime('%Y%m%d%H%M%S')}{secrets.token_hex(4)}"
        batch = RedemptionBatch(
            batch_no=batch_no,
            product_type=product_type,
            product_code=product_code,
            product_name=product_name,
            billing_cycle=billing_cycle,
            duration_months=duration_months,
            validity_days=validity_days,
            ai_unlimited=ai_unlimited,
            entitlement_snapshot=entitlement_snapshot,
            quantity=count,
            generated_count=count,
            used_count=0,
            expires_at=expires_at,
            disabled=False,
            created_by=admin_id,
        )
        self.session.add(batch)
        await self.session.flush()

        plain_codes: list[str] = []
        for _ in range(count):
            code = _generate_code()
            digest = compute_code_digest(code, secret)
            self.session.add(
                RedemptionCode(
                    batch_id=batch.id,
                    code_digest=digest,
                    code_last4=code[-4:],
                    status="unused",
                    expires_at=expires_at,
                    disabled=False,
                )
            )
            plain_codes.append(code)
        await self.session.flush()

        # 暂存一次性明文载荷，供 export_batch_once 在同进程内消费一次
        _pending_exports[batch.id] = list(plain_codes)

        return BatchCreateResult(
            batch_id=batch.id,
            batch_no=batch_no,
            product_type=product_type,
            product_code=product_code,
            codes=plain_codes,
        )

    def _resolve_plan_params(
        self, plan_code: str, cycle_or_validity: str | int
    ) -> tuple[str, int, str, bool]:
        plan = next((p for p in PLAN_CATALOG if p["code"] == plan_code), None)
        if not plan or plan.get("is_free"):
            raise RedemptionError(f"Unsupported plan code for redemption: {plan_code}")
        billing_cycle = str(cycle_or_validity)
        if billing_cycle not in ("monthly", "quarterly"):
            # 第一版只支持月卡/季卡，禁止年卡兑换码
            raise RedemptionError("Only monthly/quarterly plan codes are allowed")
        duration = 1 if billing_cycle == "monthly" else 3
        return billing_cycle, duration, plan["name"], bool(plan.get("ai_unlimited"))

    def _resolve_package_params(
        self, package_code: str, cycle_or_validity: str | int
    ) -> tuple[int, str, bool]:
        if package_code not in ALLOWED_QUOTA_PACKAGE_CODES:
            raise RedemptionError(f"Unsupported quota package code: {package_code}")
        pkg = next(
            (
                p
                for p in AI_QUOTA_PACKAGES
                if p[0] == package_code  # type: ignore[index]
            ),
            None,
        )
        if not pkg:
            raise RedemptionError(f"Quota package not found: {package_code}")
        # 元组：(code, name, base, bonus, ai_unlimited, amount, validity_days, sort_order)
        validity_days = int(pkg[6])  # type: ignore[index]
        return validity_days, str(pkg[1]), bool(pkg[4])  # type: ignore[index]

    # ---------------- 一次性导出 ----------------

    async def export_batch_once(self, batch_id: int) -> str:
        """一次性导出某批次的明文兑换码到临时文件，返回文件路径。

        - 仅 ``exported_at`` 为空的批次可导出一次；成功后设置 ``exported_at``。
        - 明文只来自同进程生成时暂存的临时载荷；进程重启后不可再导出
          （此时只能依赖 create_batch 当次返回的明文）。
        - 不得从摘要反推；明文永不写库。
        - 文件名使用 ``secrets`` 随机串，输出到专用临时目录（已 gitignore）。
        """
        batch = await self.session.scalar(
            select(RedemptionBatch)
            .where(RedemptionBatch.id == batch_id)
            .with_for_update()
        )
        if not batch:
            raise RedemptionError("Redemption batch not found")
        if batch.exported_at is not None:
            raise RedemptionError(
                "Batch already exported; codes can be viewed once only"
            )

        plain_codes = _pending_exports.get(batch.id)
        if plain_codes is None:
            raise RedemptionError(
                "Plaintext codes no longer available; export is allowed only once "
                "immediately after generation"
            )

        file_name = f"redemption_{batch.batch_no}_{secrets.token_hex(8)}.txt"
        file_path = _export_dir() / file_name
        # 仅写入尾4位无关的完整明文到临时文件（一次性），不含摘要/密钥
        file_path.write_text("\n".join(plain_codes) + "\n", encoding="utf-8")

        batch.exported_at = get_beijing_now_naive()
        await self.session.flush()

        # 消费临时载荷：导出后立即从内存移除，明文不再驻留进程
        _pending_exports.pop(batch.id, None)
        logger.info(
            f"[redemption] batch {batch.batch_no} exported once to temp file "
            f"(codes={len(plain_codes)})"
        )
        return str(file_path)

    @staticmethod
    def delete_export_file(file_path: str) -> None:
        """删除导出的临时文件（下载完成或超时后调用）。"""
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning(f"[redemption] failed to delete export file: {exc}")

    # ---------------- 核销 ----------------

    async def redeem(
        self,
        user_id: int,
        code_plain: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> RedeemResult:
        """核销兑换码并发放权益。整个流程在单事务内完成，失败整体回滚。

        幂等键按用户隔离：
        - 同一用户同一幂等键重放同一兑换码 → 返回已有结果，不重复发放；
        - 同一用户同一幂等键提交不同兑换码 → 明确拒绝，不返回旧请求的成功结果；
        - 不同用户复用相同幂等键互不影响，且互不可读对方兑换结果。
        """
        now = now or get_beijing_now_naive()
        async with self.session.begin():
            secret = await _resolve_secret(self.session)
            digest = compute_code_digest(code_plain, secret)

            # 行锁锁定兑换码：并发兑换同一码只允许一个成功
            code = await self.session.scalar(
                select(RedemptionCode)
                .where(RedemptionCode.code_digest == digest)
                .with_for_update()
            )
            if not code:
                raise RedemptionError("Invalid redemption code")

            # 幂等：按 (user_id, idempotency_key) 查询，禁止跨用户读取
            existing = await self.session.scalar(
                select(RedemptionRecord).where(
                    RedemptionRecord.user_id == user_id,
                    RedemptionRecord.idempotency_key == idempotency_key,
                )
            )
            if existing:
                if existing.code_id != code.id:
                    # 同一用户同一幂等键提交了不同兑换码：明确拒绝
                    raise RedemptionError(
                        "Idempotency key already used for a different redemption code"
                    )
                return RedeemResult(
                    record_id=existing.id,
                    code_id=existing.code_id,
                    batch_id=existing.batch_id,
                    product_type=existing.product_type,
                    product_code=existing.product_code,
                    grant_id=existing.grant_id,
                    ledger_id=existing.ledger_id,
                    action="replay",
                    duplicate=True,
                )

            # 校验兑换码状态（replay 已提前返回，此处遇到 used 即并发冲突）
            if code.disabled:
                raise RedemptionError("Redemption code is disabled")
            if code.status != "unused":
                raise RedemptionError("Redemption code already used")
            if code.expires_at and code.expires_at <= now:
                raise RedemptionError("Redemption code expired")

            batch = await self.session.scalar(
                select(RedemptionBatch)
                .where(RedemptionBatch.id == code.batch_id)
                .with_for_update()
            )
            if not batch or batch.disabled:
                raise RedemptionError("Redemption batch is disabled")

            try:
                grant_result = await self._grant_for_batch(
                    batch=batch,
                    code_id=code.id,
                    user_id=user_id,
                    idempotency_key=idempotency_key,
                    now=now,
                )
            except EntitlementGrantError as exc:
                # 权益发放失败：整体回滚，兑换码不被消耗
                raise RedemptionError(str(exc)) from exc

            record = RedemptionRecord(
                code_id=code.id,
                batch_id=batch.id,
                user_id=user_id,
                product_type=batch.product_type,
                product_code=batch.product_code,
                grant_id=grant_result.grant_id,
                ledger_id=grant_result.ledger_id,
                idempotency_key=idempotency_key,
                details={
                    "action": grant_result.action,
                    "ai_unlimited": grant_result.ai_unlimited,
                },
            )
            self.session.add(record)
            try:
                await self.session.flush()
            except IntegrityError as exc:
                # 并发场景下同一 (user_id, idempotency_key) 被另一请求抢先写入：
                # 失败关闭，整体回滚，不重复发放、不消耗本兑换码。
                raise RedemptionError(
                    "Redemption idempotency conflict; retry the request"
                ) from exc

            code.status = "used"
            code.used_by = user_id
            code.used_at = now
            code.redemption_record_id = record.id
            batch.used_count = int(batch.used_count or 0) + 1
            await self.session.flush()

            return RedeemResult(
                record_id=record.id,
                code_id=code.id,
                batch_id=batch.id,
                product_type=batch.product_type,
                product_code=batch.product_code,
                grant_id=grant_result.grant_id,
                ledger_id=grant_result.ledger_id,
                action=grant_result.action,
                duplicate=False,
            )

    async def _grant_for_batch(
        self,
        *,
        batch: RedemptionBatch,
        code_id: int,
        user_id: int,
        idempotency_key: str,
        now: datetime,
    ):
        """根据批次冻结的权益快照发放权益，不重新依赖当前套餐目录。

        ``code_id`` / ``batch_id`` 写入 source，由通用发放服务落入权益流水
        ``details`` 与加量包 grant 的 ``source_id``，保证可从权益追溯到具体兑换码。
        """
        snapshot = batch.entitlement_snapshot
        if not snapshot:
            # 快照缺失：失败关闭，不消耗兑换码、不发放部分权益
            raise RedemptionError(
                "Redemption batch entitlement snapshot is missing; redeem rejected"
            )
        grant_service = EntitlementGrantService(self.session)
        source = {
            "kind": "redemption",
            "event_type": "redemption_grant",
            "code_id": code_id,
            "batch_id": batch.id,
            "order_id": None,
        }
        if batch.product_type == "plan":
            _validate_plan_snapshot(snapshot)
            return await grant_service.grant_plan(
                user_id=user_id,
                plan_snapshot=snapshot,
                billing_cycle=str(batch.billing_cycle),
                duration_months=int(batch.duration_months or 1),
                idempotency_key=idempotency_key,
                source=source,
                now=now,
            )
        _validate_package_snapshot(snapshot)
        return await grant_service.grant_quota_package(
            user_id=user_id,
            package_snapshot=snapshot,
            idempotency_key=idempotency_key,
            source=source,
            now=now,
        )

    async def _build_plan_snapshot(
        self, product_code: str, billing_cycle: str | None, duration_months: int | None
    ) -> dict:
        """生成批次时从当前套餐目录冻结完整权益快照。"""
        plan = await self.session.scalar(
            select(BillingPlan).where(BillingPlan.code == product_code)
        )
        if not plan:
            raise RedemptionError("Plan not found for redemption batch")
        return {
            "plan_id": plan.id,
            "plan_code": plan.code,
            "plan_name": plan.name,
            "billing_cycle": billing_cycle,
            "duration_months": duration_months or 1,
            "account_limit": plan.account_limit,
            "monthly_ai_quota": plan.monthly_ai_quota,
            "ai_unlimited": bool(plan.ai_unlimited),
            "feature_flags": plan.feature_flags,
            "sort_order": plan.sort_order,
        }

    async def _build_package_snapshot(self, product_code: str) -> dict:
        """生成批次时从当前加量包目录冻结完整权益快照。"""
        pkg = await self.session.scalar(
            select(AIQuotaPackage).where(AIQuotaPackage.code == product_code)
        )
        if not pkg:
            raise RedemptionError("Quota package not found for redemption batch")
        total = int(pkg.base_quota) + int(pkg.bonus_quota)
        return {
            "package_code": pkg.code,
            "package_name": pkg.name,
            "base_quota": int(pkg.base_quota),
            "bonus_quota": int(pkg.bonus_quota),
            "total_quota": total,
            "validity_days": int(pkg.validity_days),
            "ai_unlimited": bool(pkg.ai_unlimited),
        }

    # ---------------- 管理服务（只返回尾4位/状态/时间，不含完整码或摘要） ----------------

    async def list_batches(self, limit: int = 100) -> list[dict]:
        rows = list(
            await self.session.scalars(
                select(RedemptionBatch).order_by(RedemptionBatch.id.desc()).limit(limit)
            )
        )
        return [self._batch_view(b) for b in rows]

    async def list_codes(self, batch_id: int, limit: int = 500) -> list[dict]:
        rows = list(
            await self.session.scalars(
                select(RedemptionCode)
                .where(RedemptionCode.batch_id == batch_id)
                .order_by(RedemptionCode.id.asc())
                .limit(limit)
            )
        )
        return [self._code_view(c) for c in rows]

    async def disable_batch(self, batch_id: int, reason: str | None) -> None:
        batch = await self.session.scalar(
            select(RedemptionBatch)
            .where(RedemptionBatch.id == batch_id)
            .with_for_update()
        )
        if not batch:
            raise RedemptionError("Redemption batch not found")
        batch.disabled = True
        batch.disable_reason = reason
        await self.session.flush()

    async def disable_code(self, code_id: int, reason: str | None) -> None:
        code = await self.session.scalar(
            select(RedemptionCode).where(RedemptionCode.id == code_id).with_for_update()
        )
        if not code:
            raise RedemptionError("Redemption code not found")
        code.disabled = True
        code.disable_reason = reason
        code.status = "disabled" if code.status == "unused" else code.status
        await self.session.flush()

    async def audit(self, batch_id: int | None = None, limit: int = 100) -> list[dict]:
        stmt = (
            select(RedemptionRecord).order_by(RedemptionRecord.id.desc()).limit(limit)
        )
        if batch_id is not None:
            stmt = (
                select(RedemptionRecord)
                .where(RedemptionRecord.batch_id == batch_id)
                .order_by(RedemptionRecord.id.desc())
                .limit(limit)
            )
        rows = list(await self.session.scalars(stmt))
        return [
            {
                "record_id": r.id,
                "batch_id": r.batch_id,
                "code_id": r.code_id,
                "user_id": r.user_id,
                "product_type": r.product_type,
                "product_code": r.product_code,
                "created_at": r.created_at,
            }
            for r in rows
        ]

    @staticmethod
    def _batch_view(b: RedemptionBatch) -> dict:
        return {
            "batch_id": b.id,
            "batch_no": b.batch_no,
            "product_type": b.product_type,
            "product_code": b.product_code,
            "product_name": b.product_name,
            "billing_cycle": b.billing_cycle,
            "duration_months": b.duration_months,
            "validity_days": b.validity_days,
            "ai_unlimited": b.ai_unlimited,
            "quantity": b.quantity,
            "generated_count": b.generated_count,
            "used_count": b.used_count,
            "disabled": b.disabled,
            "disable_reason": b.disable_reason,
            "expires_at": b.expires_at,
            "exported_at": b.exported_at,
            "created_at": b.created_at,
        }

    @staticmethod
    def _code_view(c: RedemptionCode) -> dict:
        return {
            "code_id": c.id,
            "batch_id": c.batch_id,
            "code_last4": c.code_last4,
            "status": c.status,
            "disabled": c.disabled,
            "disable_reason": c.disable_reason,
            "used_by": c.used_by,
            "used_at": c.used_at,
            "expires_at": c.expires_at,
            "created_at": c.created_at,
        }
