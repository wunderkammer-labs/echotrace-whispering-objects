"""MQTT listener coordinating node messages for the EchoTrace hub."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
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


def _build_mqtt_client() -> mqtt_types.Client:
    if mqtt is None:
        raise RuntimeError("paho-mqtt must be installed to run the hub listener.")
    callback_api = getattr(mqtt, "CallbackAPIVersion", None)
    if callback_api is None:
        return mqtt.Client()
    return mqtt.Client(callback_api_version=callback_api.VERSION2)


def _reason_code_value(reason_code: object) -> int:
    if isinstance(reason_code, int):
        return reason_code
    return int(str(reason_code))


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


@dataclass
class PendingAck:
    """Track an in-flight config push awaiting a specific acknowledgement."""

    request_id: str
    event: threading.Event
    version: int
    status: str = "pending"
    detail: str = ""


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
        self._client = mqtt_client or _build_mqtt_client()
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message

        self._runtime = HubRuntimeState()
        self._narrative = NarrativeState(
            required_fragments=self._config.narrative.required_fragments_to_unlock,
        )
        self._event_logger = CsvEventLogger(self._config.logs_dir)

        self._state_lock = threading.RLock()
        self._ack_events: dict[str, PendingAck] = {}
        self._ack_lock = threading.Lock()
        self._desired_configs: dict[str, dict[str, Any]] = {}
        self._desired_versions: dict[str, int] = {}
        self._reported_versions: dict[str, int] = {}
        self._version_counter = 0
        self._reconcile_interval_s = 5.0
        self._reconcile_stop = threading.Event()
        self._reconcile_thread: threading.Thread | None = None

    def _set_reported_version(self, node_id: str, version: int) -> None:
        """Record the latest version a node reports as applied."""
        self._reported_versions[node_id] = max(0, int(version))

    def _config_sync_state(self, desired_version: int, reported_version: int) -> str:
        """Return a human-readable sync status for dashboard display."""
        if desired_version <= 0:
            return "unknown"
        if reported_version >= desired_version:
            return "in_sync"
        return "stale"

    def start(self) -> None:
        """Connect to the MQTT broker and begin processing messages."""
        LOGGER.info(
            "Connecting to MQTT broker at %s:%s",
            self._config.broker_host,
            self._config.broker_port,
        )
        self._client.connect(self._config.broker_host, self._config.broker_port, keepalive=60)
        self._client.loop_start()
        self._reconcile_stop.clear()
        self._reconcile_thread = threading.Thread(
            target=self._reconcile_loop,
            name="hub-config-reconcile",
            daemon=True,
        )
        self._reconcile_thread.start()

    def stop(self) -> None:
        """Stop the MQTT listener and close resources."""
        self._client.loop_stop()
        self._client.disconnect()
        self._reconcile_stop.set()
        if self._reconcile_thread is not None:
            self._reconcile_thread.join(timeout=1.0)
            self._reconcile_thread = None
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

        results = self.push_node_configs({node_id: payload}, timeout=timeout)
        success = results.get(node_id, False)
        if not success:
            raise ConfigPushError(
                f"Configuration push to {node_id} failed to apply.", status_code=502
            )
        return True

    def push_node_configs(
        self,
        payloads: dict[str, dict[str, object]],
        timeout: float = 5.0,
    ) -> dict[str, bool]:
        """Push a versioned desired configuration set to multiple nodes."""
        if not payloads:
            return {}
        with self._state_lock:
            self._version_counter += 1
            config_version = self._version_counter
        results: dict[str, bool] = {}
        for node_id, payload in payloads.items():
            if not isinstance(payload, dict):
                raise ValueError("Node configuration payload must be a dictionary.")
            typed_payload: dict[str, Any] = dict(payload)
            typed_payload["config_version"] = config_version
            with self._state_lock:
                self._desired_configs[node_id] = dict(typed_payload)
                self._desired_versions[node_id] = config_version
            try:
                results[node_id] = self._publish_and_wait_ack(
                    node_id=node_id,
                    payload=typed_payload,
                    timeout=timeout,
                )
            except ConfigPushError:
                results[node_id] = False
        return results

    def _publish_and_wait_ack(
        self,
        *,
        node_id: str,
        payload: dict[str, object],
        timeout: float,
    ) -> bool:
        request_id = uuid.uuid4().hex
        version_raw = payload.get("config_version", 0)
        version = int(version_raw) if isinstance(version_raw, int) else 0
        message_payload = dict(payload)
        message_payload["_request_id"] = request_id
        message = json.dumps(message_payload)
        ack_event = PendingAck(request_id=request_id, event=threading.Event(), version=version)
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
        if ack_event.event.wait(timeout):
            if ack_event.status != "ok":
                detail = ack_event.detail or ack_event.status
                raise ConfigPushError(
                    f"Configuration push to {node_id} failed: {detail}.",
                    status_code=502,
                )
            self._event_logger.record_event("config_push_ok", node_id, message)
            with self._state_lock:
                self._set_reported_version(node_id, version)
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
        with self._state_lock:
            self._narrative.reset()
        self.publish_state()
        self._event_logger.record_event("admin_action", "hub", "Narrative state reset")

    def get_state_snapshot(self) -> dict[str, object]:
        """Return the current narrative state snapshot."""
        with self._state_lock:
            return self._narrative.snapshot()

    def get_health_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return ages of the last heartbeat received per node."""
        with self._state_lock:
            runtime = self._runtime.snapshot()
            all_nodes = set(runtime) | set(self._desired_versions)
            snapshot: dict[str, dict[str, Any]] = {}
            for node_id in sorted(all_nodes):
                node_runtime = dict(runtime.get(node_id, {}))
                desired_version = self._desired_versions.get(node_id, 0)
                reported_version = self._reported_versions.get(node_id, 0)
                node_runtime["desired_config_version"] = desired_version
                node_runtime["reported_config_version"] = reported_version
                node_runtime["config_sync"] = self._config_sync_state(
                    desired_version,
                    reported_version,
                )
                snapshot[node_id] = node_runtime
            return snapshot

    def publish_state(self) -> None:
        """Publish the narrative state to the MQTT broker."""
        with self._state_lock:
            state_snapshot = self._narrative.snapshot()
        payload = json.dumps(state_snapshot)
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
        reason_code: object,
        _properties: object | None = None,
    ) -> None:
        code = _reason_code_value(reason_code)
        if code != 0:
            LOGGER.error("Failed to connect to MQTT broker (rc=%s).", reason_code)
            return
        LOGGER.info("Connected to MQTT broker.")
        client.subscribe(health_wildcard())
        client.subscribe(trigger_wildcard())
        client.subscribe(ack_wildcard())

    def _on_disconnect(
        self,
        client: mqtt_types.Client,
        _userdata: object,
        _disconnect_flags: object,
        reason_code: object,
        _properties: object | None = None,
    ) -> None:
        del client
        code = _reason_code_value(reason_code)
        if code != 0:
            LOGGER.warning("Unexpected disconnection from MQTT broker (rc=%s).", reason_code)
        else:
            LOGGER.info("Disconnected from MQTT broker.")

    def _reconcile_loop(self) -> None:
        """Retry stale node configs until nodes report desired versions."""
        while not self._reconcile_stop.wait(self._reconcile_interval_s):
            with self._state_lock:
                expected = {
                    node_id: dict(payload)
                    for node_id, payload in self._desired_configs.items()
                }
                desired_versions = dict(self._desired_versions)
                reported_versions = dict(self._reported_versions)
            for node_id, payload in expected.items():
                desired_version = desired_versions.get(node_id, 0)
                reported_version = reported_versions.get(node_id, 0)
                if desired_version <= 0 or reported_version >= desired_version:
                    continue
                try:
                    self._publish_and_wait_ack(
                        node_id=node_id,
                        payload=payload,
                        timeout=1.5,
                    )
                except ConfigPushError:
                    LOGGER.debug("Reconcile push still pending for %s", node_id)

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
            "fragment_file": data.get("fragment_file", ""),
        }
        config_version = data.get("config_version")
        if isinstance(config_version, int) and config_version >= 0:
            extra["config_version"] = config_version
        with self._state_lock:
            self._runtime.update_health(node_id, timestamp, extra)
            if isinstance(config_version, int) and config_version >= 0:
                self._set_reported_version(node_id, config_version)
        self._event_logger.record_event("heartbeat_received", node_id, payload or "{}")

    def _handle_trigger(self, node_id: str, payload: str) -> None:
        try:
            data = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            LOGGER.warning("Invalid trigger payload from %s: %s", node_id, payload)
            self._event_logger.record_event("fragment_triggered", node_id, "invalid_json")
            return

        if not isinstance(data, dict):
            LOGGER.warning("Trigger payload from %s must be an object.", node_id)
            self._event_logger.record_event("fragment_triggered", node_id, "invalid_shape")
            return

        role = str(data.get("role", "")).strip().lower()
        if role == "mystery":
            self._event_logger.record_event("mystery_triggered", node_id, json.dumps(data))
            return

        self._event_logger.record_event("fragment_triggered", node_id, json.dumps(data))

        with self._state_lock:
            unlocked_before = self._narrative.unlocked
            is_new = self._narrative.register_trigger(node_id)
            unlocked_after = self._narrative.unlocked
        if not is_new:
            LOGGER.debug("Duplicate trigger received from %s; ignoring.", node_id)

        self.publish_state()
        if unlocked_after and not unlocked_before:
            self._event_logger.record_event(
                "narrative_unlocked",
                node_id,
                "Unlock threshold reached",
            )
            LOGGER.info("Narrative unlocked after trigger from %s.", node_id)

    def _handle_ack(self, node_id: str, payload: str) -> None:
        self._event_logger.record_event("config_ack", node_id, payload or "{}")
        request_id = ""
        ack_version = -1
        ack_status = "ok"
        ack_detail = "ok"
        if payload:
            try:
                data = json.loads(payload)
                if isinstance(data, dict):
                    request_id = str(data.get("request_id", "")).strip()
                    ack_status = str(data.get("status", "ok")).strip().lower() or "ok"
                    ack_detail = str(data.get("detail", data.get("error", ack_status)))
                    version_raw = data.get("config_version")
                    if isinstance(version_raw, int):
                        ack_version = version_raw
            except json.JSONDecodeError:
                LOGGER.warning("Invalid ACK payload from %s: %s", node_id, payload)
                return
        with self._ack_lock:
            pending = self._ack_events.get(node_id)
            if pending is None:
                LOGGER.warning("Received unexpected ACK from %s.", node_id)
                return
            if request_id != pending.request_id:
                LOGGER.warning(
                    "Ignoring ACK for %s with mismatched request_id (expected=%s, got=%s).",
                    node_id,
                    pending.request_id,
                    request_id or "<missing>",
                )
                return
            self._ack_events.pop(node_id, None)
            pending.status = ack_status
            pending.detail = ack_detail
        if pending:
            with self._state_lock:
                if ack_version >= 0:
                    self._set_reported_version(node_id, ack_version)
            pending.event.set()
        else:
            LOGGER.warning("Received unexpected ACK from %s.", node_id)


def run_forever() -> None:
    """Run the listener from a module entry point."""
    listener = HubListener()
    listener.run_forever()


__all__ = ["HubListener", "run_forever"]
