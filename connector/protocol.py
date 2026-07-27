from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeliveryState(str, Enum):
    RECEIVED = "received"
    DECISION_PENDING = "decision_pending"
    PENDING_SEND = "pending_send"
    SENT = "sent"
    FAILED = "failed"
    RECONCILE = "reconcile"


_TRANSITIONS = {
    DeliveryState.RECEIVED: {DeliveryState.DECISION_PENDING},
    DeliveryState.DECISION_PENDING: {DeliveryState.PENDING_SEND, DeliveryState.FAILED},
    DeliveryState.PENDING_SEND: {DeliveryState.SENT, DeliveryState.FAILED, DeliveryState.RECONCILE},
    DeliveryState.RECONCILE: {DeliveryState.SENT, DeliveryState.FAILED},
}


@dataclass(frozen=True)
class MessageEnvelope:
    request_id: str
    device_id: str
    account_id: str
    source_message_id: str
    idempotency_key: str
    timestamp: int
    protocol_version: int = 1


def can_transition(current: DeliveryState, target: DeliveryState) -> bool:
    return target in _TRANSITIONS.get(current, set())
