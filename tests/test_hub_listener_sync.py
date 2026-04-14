"""Tests for hub-side config sync and acknowledgement behavior."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import hub.hub_listener as hub_listener_module

from hub.config_loader import AnalyticsConfig, HubConfig, NarrativeConfig, SecurityConfig
from hub.hub_listener import HubListener, PendingAck, _build_mqtt_client


class FakeMQTT:
    """Minimal MQTT client used for unit tests without a broker."""

    def __init__(self) -> None:
        self.on_connect: Any = None
        self.on_disconnect: Any = None
        self.on_message: Any = None
        self.published: list[tuple[str, str, int, bool]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> object:
        self.published.append((topic, payload, qos, retain))

        class Info:
            rc = 0

        return Info()

    def connect(self, _host: str, _port: int, keepalive: int = 60) -> None:
        del keepalive

    def loop_start(self) -> None:
        return

    def loop_stop(self) -> None:
        return

    def disconnect(self) -> None:
        return

    def subscribe(self, _topic: str) -> None:
        return


def _config(tmp_path: Path) -> HubConfig:
    return HubConfig(
        broker_host="localhost",
        broker_port=1883,
        dashboard_host="127.0.0.1",
        dashboard_port=8080,
        default_language="en",
        logs_dir=tmp_path / "logs",
        analytics=AnalyticsConfig(),
        narrative=NarrativeConfig(required_fragments_to_unlock=4),
        security=SecurityConfig(require_basic_auth=False),
    )


def test_health_downgrade_marks_node_stale(tmp_path: Path) -> None:
    """If node-reported version drops, sync should reflect stale state."""
    listener = HubListener(config=_config(tmp_path))
    listener._client = cast(Any, FakeMQTT())
    with listener._state_lock:
        listener._desired_versions["node1"] = 5

    listener._handle_health(
        "node1",
        json.dumps({"config_version": 5, "rssi": -44, "sensor_status": "ok"}),
    )
    assert listener.get_health_snapshot()["node1"]["config_sync"] == "in_sync"

    listener._handle_health(
        "node1",
        json.dumps({"config_version": 1, "rssi": -44, "sensor_status": "ok"}),
    )
    snapshot = listener.get_health_snapshot()["node1"]
    assert snapshot["reported_config_version"] == 1
    assert snapshot["config_sync"] == "stale"

    listener.stop()


def test_non_success_ack_completes_as_failure(tmp_path: Path) -> None:
    """Error ACKs should wake waiters but preserve failure status."""
    listener = HubListener(config=_config(tmp_path))
    listener._client = cast(Any, FakeMQTT())
    event = threading.Event()
    with listener._ack_lock:
        listener._ack_events["node1"] = PendingAck(
            request_id="req-1",
            event=event,
            version=4,
        )

    listener._handle_ack(
        "node1",
        json.dumps({"request_id": "req-1", "status": "error", "config_version": 4}),
    )
    assert event.is_set() is True
    with listener._ack_lock:
        assert "node1" not in listener._ack_events
    assert listener._reported_versions["node1"] == 4

    listener.stop()


def test_build_mqtt_client_uses_callback_api_v2_when_available() -> None:
    """Hub listener should opt into the supported paho callback API when available."""
    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def _fake_client(*args: object, **kwargs: object) -> FakeClient:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeClient()

    original_mqtt = hub_listener_module.mqtt
    hub_listener_module.mqtt = cast(
        Any,
        SimpleNamespace(
            CallbackAPIVersion=SimpleNamespace(VERSION2="v2"),
            Client=_fake_client,
        ),
    )
    try:
        client = _build_mqtt_client()
    finally:
        hub_listener_module.mqtt = original_mqtt

    assert isinstance(client, FakeClient)
    assert captured["kwargs"] == {"callback_api_version": "v2"}
