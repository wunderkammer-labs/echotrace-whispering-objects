"""MQTT topic helpers used across EchoTrace components.

This module re-exports the shared MQTT topic definitions for backward
compatibility. New code should import directly from shared.mqtt_topics.
"""

from shared.mqtt_topics import (
    ack_wildcard,
    health_topic,
    health_wildcard,
    hub_state_topic,
    node_ack_topic,
    node_config_topic,
    trigger_topic,
    trigger_wildcard,
)

__all__ = [
    "ack_wildcard",
    "health_topic",
    "health_wildcard",
    "hub_state_topic",
    "node_ack_topic",
    "node_config_topic",
    "trigger_topic",
    "trigger_wildcard",
]
