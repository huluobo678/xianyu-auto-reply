"""兑换码相关测试共用的轻量 FakeSession。

按 ORM 实体分发 scalar 查询（FIFO 队列），支持 begin 事务上下文、add、flush，
用于在不连真实库的前提下驱动 EntitlementGrantService / RedemptionService /
SubscriptionLifecycleService / AIUsageService 的核心发放与核销流程。

注意：真实并发由数据库行锁（``with_for_update``）与唯一约束保证；此处仅模拟
单线程内的查询顺序，并发语义由对应测试用队列顺序 + 代码结构共同体现。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


class ModelFakeSession:
    def __init__(self) -> None:
        self.queues: dict[Any, list] = {}
        self.scalars_lists: list[list] = []
        self.column_scalars: list = []
        self.added: list = []
        self._next_id = 1000

    def begin(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, _exc_type, _exc, _tb):
        return False

    def queue(self, entity, *values):
        self.queues.setdefault(entity, []).extend(values)
        return self

    def queue_scalars(self, *rows):
        self.scalars_lists.extend(rows)
        return self

    @staticmethod
    def _entity(statement):
        try:
            return statement.column_descriptions[0]["entity"]
        except Exception:
            return None

    async def scalar(self, statement):
        entity = self._entity(statement)
        if entity is None:
            return self.column_scalars.pop(0) if self.column_scalars else None
        queue = self.queues.setdefault(entity, [])
        return queue.pop(0) if queue else None

    async def scalars(self, statement):
        if self.scalars_lists:
            return self.scalars_lists.pop(0)
        entity = self._entity(statement)
        return list(self.queues.get(entity, []))

    def add(self, value):
        if getattr(value, "id", None) is None:
            value.id = self._next_id
            self._next_id += 1
        self.added.append(value)

    async def flush(self):
        return None

    async def execute(self, _statement, _params=None):
        return SimpleNamespace(scalar_one=lambda: None, scalar=lambda: None)
