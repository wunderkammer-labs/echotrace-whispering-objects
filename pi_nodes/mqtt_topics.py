"""Node-side MQTT topic helpers aligned with the hub definitions.

This module re-exports the shared MQTT topic definitions for backward
compatibility. New code should import directly from shared.mqtt_topics.
"""

from shared.mqtt_topics import (
    health_topic,
    hub_state_topic,
    node_ack_topic,
    node_config_topic,
    trigger_topic,
)

__all__ = [
    "health_topic",
    "hub_state_topic",
    "node_ack_topic",
    "node_config_topic",
    "trigger_topic",
]
