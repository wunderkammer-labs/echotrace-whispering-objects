"""Behavioural tests for the node service."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Tuple, cast

import pytest
import yaml  # type: ignore[import]

from pi_nodes.mqtt_topics import (
    health_topic,
    hub_state_topic,
    node_ack_topic,
    node_config_topic,
    trigger_topic,
)
import pi_nodes.node_service as node_service_module
from pi_nodes.node_service import HEARTBEAT_INTERVAL_SECONDS, NodeService, _build_mqtt_client
from pi_nodes.proximity_sensor import MockProximitySensor


class FakeMQTT:
    """Minimal MQTT client stub capturing published messages."""

    def __init__(self) -> None:
        self.published: List[Tuple[str, Any]] = []
        self.subscriptions: List[str] = []

    def publish(
        self,
        topic: str,
        payload: str,
        qos: int = 0,
        retain: bool = False,
    ) -> object:
        """Record a published message and return a success result."""
        del qos, retain  # Unused parameters
        self.published.append((topic, json.loads(payload)))

        class Info:
            rc = 0

        return Info()

    def subscribe(self, topic: str) -> None:
        self.subscriptions.append(topic)


class DummyAudio:
    """Audio player stub capturing configuration changes."""

    def __init__(self) -> None:
        self.loaded: Optional[Path] = None
        self.safety_limit: float = 1.0
        self.volume: float = 0.0
        self.play_calls: List[Tuple[bool, float, int]] = []

    def load(self, path: Path) -> None:
        self.loaded = path

    def set_safety_limit(self, limit: float) -> None:
        self.safety_limit = limit

    def set_volume(self, value: float) -> None:
        self.volume = value

    def play(self, loop: bool = False, pace: float = 1.0, repeat: int = 0) -> None:
        self.play_calls.append((loop, pace, repeat))

    def stop(self) -> None:  # pragma: no cover - not used yet
        pass


class DummyLED:
    """LED stub recording the last command."""

    def __init__(self) -> None:
        self.history: List[Tuple[str, Tuple[Any, ...]]] = []

    def glow(self, level: float) -> None:
        self.history.append(("glow", (level,)))

    def blink(self, on_s: float, off_s: float) -> None:
        self.history.append(("blink", (on_s, off_s)))

    def off(self) -> None:
        self.history.append(("off", ()))


class DummyHaptics:
    """Haptics stub capturing pulse durations."""

    def __init__(self) -> None:
        self.pulses: List[int] = []

    def pulse(self, ms: int) -> None:
        self.pulses.append(ms)

    def off(self) -> None:  # pragma: no cover - not used yet
        pass


def _write_config(tmp_path: Path, overrides: Optional[dict[str, Any]] = None) -> Path:
    base = {
        "node_id": "test-node",
        "role": "whisper",
        "language_default": "en",
        "gpio": {"led_pin": 18, "haptic_pin": 23},
        "proximity": {
            "min_mm": 100,
            "max_mm": 1200,
            "story_threshold_mm": 700,
            "hysteresis_mm": 50,
        },
        "audio": {
            "fragment_file": "clip.mp3",
            "volume": 0.6,
        },
        "accessibility": {
            "captions": False,
            "visual_pulse": False,
            "proximity_glow": True,
            "mobility_buffer_ms": 0,
            "repeat": 0,
            "pace": 1.0,
            "safety_limiter": True,
        },
    }
    if overrides:
        base.update(overrides)
    config_path = tmp_path / "node_config.yaml"
    config_path.write_text(json.dumps(base), encoding="utf-8")
    return config_path


def test_node_service_emits_heartbeat(tmp_path: Path) -> None:
    """Heartbeat should publish to the expected MQTT topic."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)

    mqtt_client = FakeMQTT()
    audio = DummyAudio()
    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, audio),
        led_feedback=cast(Any, DummyLED()),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    service._last_heartbeat_ts = -HEARTBEAT_INTERVAL_SECONDS  # force immediate heartbeat
    service.run_once(now=0.0)

    heartbeat = next(
        body
        for topic, body in mqtt_client.published
        if topic == health_topic("test-node")
    )
    assert heartbeat["config_version"] == 0
    assert "fragment_file" in heartbeat


def test_whisper_node_triggers_story(tmp_path: Path) -> None:
    """Crossing the story threshold should publish trigger and play audio."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)

    mqtt_client = FakeMQTT()
    audio = DummyAudio()
    led = DummyLED()
    sensor = MockProximitySensor([900, 650])

    service = NodeService(
        config_path=config_path,
        sensor=sensor,
        audio_player=cast(Any, audio),
        led_feedback=cast(Any, led),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    service.run_once(now=0.0)  # ambient pass
    service.run_once(now=1.0)  # should trigger

    assert audio.play_calls, "Audio play should be invoked."
    assert any(topic == trigger_topic("test-node") for topic, _ in mqtt_client.published)


def test_config_update_applies_and_acknowledges(tmp_path: Path) -> None:
    """Node should honour runtime configuration updates."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)

    mqtt_client = FakeMQTT()
    audio = DummyAudio()

    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, audio),
        led_feedback=cast(Any, DummyLED()),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    payload = json.dumps(
        {
            "audio": {"volume": 0.4},
            "config_version": 7,
            "_request_id": "req-123",
        }
    )
    service.handle_mqtt_message(node_config_topic("test-node"), payload)

    assert pytest.approx(service.config.audio.volume, rel=1e-3) == 0.4
    assert service.config.config_version == 7
    ack = next(
        body
        for topic, body in mqtt_client.published
        if topic == node_ack_topic("test-node")
    )
    assert ack["request_id"] == "req-123"
    assert ack["config_version"] == 7
    persisted = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert persisted["config_version"] == 7


def test_mystery_node_plays_on_unlock(tmp_path: Path) -> None:
    """Mystery nodes play finale audio when the hub publishes an unlocked state."""
    audio_file = tmp_path / "finale.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        {
            "role": "mystery",
            "audio": {
                "fragment_file": str(audio_file),
                "volume": 0.6,
            },
        },
    )

    mqtt_client = FakeMQTT()
    audio = DummyAudio()

    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, audio),
        led_feedback=cast(Any, DummyLED()),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    service.handle_mqtt_message(hub_state_topic(), json.dumps({"unlocked": True}))
    assert audio.play_calls, "Mystery node should play audio when unlocked."
    assert not any(topic == trigger_topic("test-node") for topic, _ in mqtt_client.published)


def test_stale_config_version_is_rejected(tmp_path: Path) -> None:
    """Out-of-order config versions should be ignored to prevent rollback."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)
    mqtt_client = FakeMQTT()

    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, DummyAudio()),
        led_feedback=cast(Any, DummyLED()),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    service.handle_mqtt_message(
        node_config_topic("test-node"),
        json.dumps(
            {
                "audio": {"volume": 0.2},
                "config_version": 8,
                "_request_id": "req-new",
            }
        ),
    )
    service.handle_mqtt_message(
        node_config_topic("test-node"),
        json.dumps(
            {
                "audio": {"volume": 0.9},
                "config_version": 2,
                "_request_id": "req-old",
            }
        ),
    )

    assert service.config.config_version == 8
    assert service.config.audio.volume == pytest.approx(0.2, rel=1e-3)
    ack = next(
        body
        for topic, body in reversed(mqtt_client.published)
        if topic == node_ack_topic("test-node")
    )
    assert ack["status"] == "stale"
    assert ack["request_id"] == "req-old"


def test_config_ack_reports_error_when_persist_fails(tmp_path: Path) -> None:
    """Persistence failures should return explicit error ACKs instead of crashing silently."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)
    mqtt_client = FakeMQTT()

    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, DummyAudio()),
        led_feedback=cast(Any, DummyLED()),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    def _raise_persist_error() -> None:
        raise OSError("disk full")

    service._persist_runtime_config = _raise_persist_error  # type: ignore[assignment]
    service.handle_mqtt_message(
        node_config_topic("test-node"),
        json.dumps({"audio": {"volume": 0.3}, "config_version": 9, "_request_id": "req-fail"}),
    )

    ack = next(
        body
        for topic, body in mqtt_client.published
        if topic == node_ack_topic("test-node")
    )
    assert ack["status"] == "error"
    assert ack["error"] == "persist_failed"
    assert ack["request_id"] == "req-fail"


def test_persist_runtime_config_preserves_existing_file_on_write_failure(tmp_path: Path) -> None:
    """Failed config writes should not truncate the last known-good config file."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)
    original_config = config_path.read_text(encoding="utf-8")
    mqtt_client = FakeMQTT()

    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, DummyAudio()),
        led_feedback=cast(Any, DummyLED()),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    original_safe_dump = yaml.safe_dump

    def _raising_safe_dump(*args: Any, **kwargs: Any) -> str:
        original_safe_dump(*args, **kwargs)
        raise yaml.YAMLError("write interrupted")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(yaml, "safe_dump", _raising_safe_dump)
    try:
        with pytest.raises(yaml.YAMLError):
            service._persist_runtime_config()
    finally:
        monkeypatch.undo()

    assert config_path.read_text(encoding="utf-8") == original_config
    assert not config_path.with_name(f"{config_path.name}.tmp").exists()


def test_light_test_action_acknowledges_without_persisting(tmp_path: Path) -> None:
    """Staff light tests should trigger hardware feedback without changing config version."""
    audio_file = tmp_path / "clip.mp3"
    audio_file.write_text("dummy", encoding="utf-8")
    config_path = _write_config(tmp_path)
    mqtt_client = FakeMQTT()
    led = DummyLED()

    service = NodeService(
        config_path=config_path,
        sensor=MockProximitySensor([900]),
        audio_player=cast(Any, DummyAudio()),
        led_feedback=cast(Any, led),
        haptics=cast(Any, DummyHaptics()),
        mqtt_client=mqtt_client,
    )

    service.handle_mqtt_message(
        node_config_topic("test-node"),
        json.dumps({"test": {"action": "light"}, "_request_id": "req-test"}),
    )

    ack = next(
        body
        for topic, body in mqtt_client.published
        if topic == node_ack_topic("test-node")
    )
    assert ack["status"] == "ok"
    assert ack["request_id"] == "req-test"
    assert service.config.config_version == 0
    assert led.history[-1][0] == "blink"


def test_node_build_mqtt_client_uses_callback_api_v2_when_available() -> None:
    """Node runtime should opt into the supported paho callback API when available."""
    captured: dict[str, object] = {}

    class FakeClient:
        pass

    def _fake_client(*args: object, **kwargs: object) -> FakeClient:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeClient()

    original_mqtt = node_service_module.mqtt
    node_service_module.mqtt = cast(
        Any,
        SimpleNamespace(
            CallbackAPIVersion=SimpleNamespace(VERSION2="v2"),
            Client=_fake_client,
        ),
    )
    try:
        client = _build_mqtt_client()
    finally:
        node_service_module.mqtt = original_mqtt

    assert isinstance(client, FakeClient)
    assert captured["kwargs"] == {"callback_api_version": "v2"}
