"""MQTT listener coordinating node messages for the EchoTrace hub."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import paho.mqtt.client as mqtt_types

try:  # pragma: no cover - executed when paho-mqtt is installed
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - executed in environments without paho-mqtt
    mqtt = None  # type: ignore[assignment]

from .config_loader import HubConfig, load_config
from .event_logging import CsvEventLogger
from .mqtt_topics import (
    ack_wildcard,
    health_topic,
    health_wildcard,
    hub_state_topic,
    node_ack_topic,
    node_config_topic,
    trigger_wildcard,
)
from .narrative_state import NarrativeState

LOGGER = logging.getLogger(__name__)

_HEALTH_PREFIX = health_topic("")
_TRIGGER_PREFIX = f"{trigger_wildcard().rsplit('/', 1)[0]}/"
_ACK_PREFIX = node_ack_topic("")


class ConfigPushError(RuntimeError):
    """Raised when a configuration push cannot complete."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class HubRuntimeState:
    """In-memory snapshot of hub observability data."""

    last_seen: dict[str, datetime] = field(default_factory=dict)
    telemetry: dict[str, dict[str, Any]] = field(default_factory=dict)

    def update_health(self, node_id: str, timestamp: datetime, extra: dict[str, Any]) -> None:
        """Record the last time a heartbeat was observed for a node."""
        self.last_seen[node_id] = timestamp
        self.telemetry[node_id] = extra

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return status snapshot per node."""
        now = datetime.now(tz=timezone.utc)
        result = {}
        for node_id, seen in self.last_seen.items():
            result[node_id] = {
                "age": (now - seen).total_seconds(),
                **self.telemetry.get(node_id, {}),
            }
        return result


class HubListener:
    """Coordinate MQTT communication between the hub and distributed nodes."""

    def __init__(
        self,
        config: Optional[HubConfig] = None,
        mqtt_client: Optional[mqtt_types.Client] = None,
    ) -> None:
        if mqtt is None:
            raise RuntimeError("paho-mqtt must be installed to run the hub listener.")

        self._config = config or load_config()
        self._client = mqtt_client or mqtt.Client()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._runtime = HubRuntimeState()
        self._narrative = NarrativeState(
            required_fragments=self._config.narrative.required_fragments_to_unlock,
        )
        self._event_logger = CsvEventLogger(self._config.logs_dir)

        self._ack_events: dict[str, threading.Event] = {}
        self._ack_lock = threading.Lock()

    def start(self) -> None:
        """Connect to the MQTT broker and begin processing messages."""
        LOGGER.info(
            "Connecting to MQTT broker at %s:%s",
            self._config.broker_host,
            self._config.broker_port,
        )
        self._client.connect(self._config.broker_host, self._config.broker_port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        """Stop the MQTT listener and close resources."""
        self._client.loop_stop()
        self._client.disconnect()
        self._event_logger.close()

    def run_forever(self) -> None:
        """Run the listener until interrupted."""
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:  # pragma: no cover - manual stop
            LOGGER.info("Hub listener interrupted by user.")
            raise
        finally:
            self.stop()

    def push_node_config(
        self,
        node_id: str,
        payload: dict[str, object],
        timeout: float = 5.0,
    ) -> bool:
        """Publish configuration updates to a node and await acknowledgement."""
        if not isinstance(payload, dict):
            raise ValueError("Node configuration payload must be a dictionary.")

        message = json.dumps(payload)
        ack_event = threading.Event()
        with self._ack_lock:
            if node_id in self._ack_events:
                raise ConfigPushError(
                    f"Configuration push already in progress for {node_id}.", status_code=409
                )
            self._ack_events[node_id] = ack_event

        info = self._client.publish(node_config_topic(node_id), message, qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:  # type: ignore[attr-defined]
            LOGGER.error("Failed to publish configuration to %s: rc=%s", node_id, info.rc)
            with self._ack_lock:
                self._ack_events.pop(node_id, None)
            raise ConfigPushError(
                f"Unable to publish configuration to {node_id} (rc={info.rc}).",
                status_code=502,
            )

        LOGGER.info("Pushed configuration to %s, awaiting acknowledgement.", node_id)
        if ack_event.wait(timeout):
            self._event_logger.record_event("config_push_ok", node_id, message)
            return True

        LOGGER.warning("Configuration push to %s timed out after %.1fs.", node_id, timeout)
        self._event_logger.record_event("config_push_timeout", node_id, message)
        with self._ack_lock:
            self._ack_events.pop(node_id, None)
        raise ConfigPushError(
            f"Configuration push to {node_id} timed out after {timeout:.1f}s.",
            status_code=504,
        )

    def reset_state(self) -> None:
        """Clear the narrative state and retain heartbeat history."""
        self._narrative.reset()
        self.publish_state()
        self._event_logger.record_event("admin_action", "hub", "Narrative state reset")

    def get_state_snapshot(self) -> dict[str, object]:
        """Return the current narrative state snapshot."""
        return self._narrative.snapshot()

    def get_health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return ages of the last heartbeat received per node."""
        return self._runtime.snapshot()

    def publish_state(self) -> None:
        """Publish the narrative state to the MQTT broker."""
        payload = json.dumps(self._narrative.snapshot())
        info = self._client.publish(hub_state_topic(), payload, qos=1, retain=True)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:  # type: ignore[attr-defined]
            LOGGER.error("Failed to publish hub state: rc=%s", info.rc)
        else:
            LOGGER.debug("Published hub state: %s", payload)

    # MQTT callbacks -----------------------------------------------------

    def _on_connect(
        self,
        client: mqtt_types.Client,
        _userdata: object,
        _flags: dict[str, int],
        rc: int,
    ) -> None:
        if rc != 0:
            LOGGER.error("Failed to connect to MQTT broker (rc=%s).", rc)
            return
        LOGGER.info("Connected to MQTT broker.")
        client.subscribe(health_wildcard())
        client.subscribe(trigger_wildcard())
        client.subscribe(ack_wildcard())

    def _on_disconnect(
        self,
        client: mqtt_types.Client,
        _userdata: object,
        rc: int,
    ) -> None:
        if rc != 0:
            LOGGER.warning("Unexpected disconnection from MQTT broker (rc=%s).", rc)
        else:
            LOGGER.info("Disconnected from MQTT broker.")

    def _on_message(
        self,
        _client: mqtt_types.Client,
        _userdata: object,
        message: mqtt_types.MQTTMessage,
    ) -> None:
        topic = message.topic or ""
        payload = message.payload.decode("utf-8") if message.payload else ""
        if topic.startswith(_HEALTH_PREFIX):
            node_id = topic[len(_HEALTH_PREFIX) :]
            self._handle_health(node_id, payload)
        elif topic.startswith(_TRIGGER_PREFIX):
            node_id = topic[len(_TRIGGER_PREFIX) :]
            self._handle_trigger(node_id, payload)
        elif topic.startswith(_ACK_PREFIX):
            node_id = topic[len(_ACK_PREFIX) :]
            self._handle_ack(node_id, payload)
        else:
            LOGGER.debug("Ignoring message on unhandled topic: %s", topic)

    def _handle_health(self, node_id: str, payload: str) -> None:
        timestamp = datetime.now(tz=timezone.utc)
        try:
            data = json.loads(payload) if payload else {}
            epoch = data.get("ts")
            if isinstance(epoch, (int, float)):
                timestamp = datetime.fromtimestamp(epoch, tz=timezone.utc)
        except json.JSONDecodeError:
            LOGGER.warning("Invalid health payload from %s: %s", node_id, payload)
            self._event_logger.record_event("heartbeat_received", node_id, "invalid_json")
            return

        extra = {
            "rssi": data.get("rssi", 0),
            "sensor_status": data.get("sensor_status", "unknown"),
        }
        self._runtime.update_health(node_id, timestamp, extra)
        self._event_logger.record_event("heartbeat_received", node_id, payload or "{}")

    def _handle_trigger(self, node_id: str, payload: str) -> None:
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            LOGGER.warning("Invalid trigger payload from %s: %s", node_id, payload)
            self._event_logger.record_event("fragment_triggered", node_id, "invalid_json")
            return

        self._event_logger.record_event("fragment_triggered", node_id, json.dumps(data))

        unlocked_before = self._narrative.unlocked
        is_new = self._narrative.register_trigger(node_id)
        if not is_new:
            LOGGER.debug("Duplicate trigger received from %s; ignoring.", node_id)

        self.publish_state()
        unlocked_after = self._narrative.unlocked
        if unlocked_after and not unlocked_before:
            self._event_logger.record_event(
                "narrative_unlocked",
                node_id,
                "Unlock threshold reached",
            )
            LOGGER.info("Narrative unlocked after trigger from %s.", node_id)

    def _handle_ack(self, node_id: str, payload: str) -> None:
        self._event_logger.record_event("config_ack", node_id, payload or "{}")
        with self._ack_lock:
            event = self._ack_events.pop(node_id, None)
        if event:
            event.set()
        else:
            LOGGER.warning("Received unexpected ACK from %s.", node_id)


def run_forever() -> None:
    """Run the listener from a module entry point."""
    listener = HubListener()
    listener.run_forever()


__all__ = ["HubListener", "run_forever"]
