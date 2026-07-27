from connector.protocol import DeliveryState, MessageEnvelope, can_transition
from common.models.connector import ConnectorDevice, ConnectorReleaseVersion


def test_connector_models_use_isolated_tables():
    assert ConnectorDevice.__tablename__ == "xy_connector_devices"
    assert ConnectorReleaseVersion.__tablename__ == "xy_connector_release_versions"


def test_delivery_state_rejects_duplicate_send():
    assert can_transition(DeliveryState.PENDING_SEND, DeliveryState.SENT)
    assert not can_transition(DeliveryState.SENT, DeliveryState.PENDING_SEND)


def test_message_envelope_defaults_to_protocol_v1():
    envelope = MessageEnvelope("r", "d", "a", "m", "i", 1)
    assert envelope.protocol_version == 1
