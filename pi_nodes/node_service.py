"""Node service loop coordinating EchoTrace interactions on Raspberry Pi nodes."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

import yaml  # type: ignore[import]

try:  # pragma: no cover - exercised only on devices with MQTT client installed
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover - development fallback
    mqtt = None  # type: ignore[assignment]

if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from paho.mqtt.client import Client as MQTTClient  # type: ignore[import]
    from paho.mqtt.client import MQTTMessage  # type: ignore[import]
else:
    MQTTClient = Any
    MQTTMessage = Any

from .audio_player import AudioPlayer
from .haptics import Haptics
from .led_feedback import LedFeedback
from .logging_utils import configure_node_logging
from .mqtt_topics import (
    hub_state_topic,
    node_ack_topic,
    node_config_topic,
    trigger_topic,
    health_topic,
)
from .proximity_sensor import ProximitySensor

LOGGER = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "node_config.yaml"
HEARTBEAT_INTERVAL_SECONDS = 15.0
RETRIGGER_COOLDOWN_SECONDS = 5.0
STORY_RESET_SECONDS = 8.0


@dataclass
class ProximitySettings:
    """Configuration values controlling proximity thresholds."""

    min_mm: int = 100
    max_mm: int = 1200
    story_threshold_mm: int = 700
    hysteresis_mm: int = 50

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProximitySettings:
        return cls(
            min_mm=int(data.get("min_mm", 100)),
            max_mm=int(data.get("max_mm", 1200)),
            story_threshold_mm=int(data.get("story_threshold_mm", 700)),
            hysteresis_mm=int(data.get("hysteresis_mm", 50)),
        )

    def update(self, values: dict[str, Any]) -> None:
        for key in ("min_mm", "max_mm", "story_threshold_mm", "hysteresis_mm"):
            if key in values:
                setattr(self, key, int(values[key]))


@dataclass
class AudioSettings:
    """Audio fragment metadata."""

    fragment_file: str = ""
    volume: float = 0.7

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioSettings:
        return cls(
            fragment_file=str(data.get("fragment_file", "")),
            volume=float(data.get("volume", 0.7)),
        )

    def update(self, values: dict[str, Any]) -> None:
        if "fragment_file" in values:
            self.fragment_file = str(values["fragment_file"])
        if "volume" in values:
            self.volume = float(values["volume"])


@dataclass
class AccessibilitySettings:
    """Accessibility preferences applied at the node."""

    captions: bool = False
    visual_pulse: bool = False
    proximity_glow: bool = True
    mobility_buffer_ms: int = 800
    repeat: int = 0
    pace: float = 1.0
    safety_limiter: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccessibilitySettings:
        return cls(
            captions=bool(data.get("captions", False)),
            visual_pulse=bool(data.get("visual_pulse", False)),
            proximity_glow=bool(data.get("proximity_glow", True)),
            mobility_buffer_ms=int(data.get("mobility_buffer_ms", 800)),
            repeat=cls._clamp_repeat(data.get("repeat", 0)),
            pace=cls._clamp_pace(data.get("pace", 1.0)),
            safety_limiter=bool(data.get("safety_limiter", True)),
        )

    def update(self, values: dict[str, Any]) -> None:
        if "captions" in values:
            self.captions = bool(values["captions"])
        if "visual_pulse" in values:
            self.visual_pulse = bool(values["visual_pulse"])
        if "proximity_glow" in values:
            self.proximity_glow = bool(values["proximity_glow"])
        if "mobility_buffer_ms" in values:
            self.mobility_buffer_ms = max(0, int(values["mobility_buffer_ms"]))
        if "repeat" in values:
            self.repeat = self._clamp_repeat(values["repeat"])
        if "pace" in values:
            self.pace = self._clamp_pace(values["pace"])
        if "safety_limiter" in values:
            self.safety_limiter = bool(values["safety_limiter"])

    @staticmethod
    def _clamp_repeat(value: Any) -> int:
        try:
            repeat = int(value)
        except (TypeError, ValueError):
            repeat = 0
        return max(0, min(2, repeat))

    @staticmethod
    def _clamp_pace(value: Any) -> float:
        try:
            pace = float(value)
        except (TypeError, ValueError):
            pace = 1.0
        return max(0.85, min(1.15, pace))

    def safety_limit(self) -> float:
        """Return the safety volume cap."""
        return 0.75 if self.safety_limiter else 1.0


@dataclass
class NodeConfig:
    """Aggregated node configuration."""

    node_id: str
    role: str
    default_language: str
    led_pin: Optional[int]
    haptic_pin: Optional[int]
    proximity: ProximitySettings
    audio: AudioSettings
    accessibility: AccessibilitySettings

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeConfig:
        gpio = data.get("gpio", {}) or {}
        return cls(
            node_id=str(data.get("node_id", "node-unknown")),
            role=str(data.get("role", "whisper")),
            default_language=str(data.get("language_default", "en")),
            led_pin=cls._safe_pin(gpio.get("led_pin")),
            haptic_pin=cls._safe_pin(gpio.get("haptic_pin")),
            proximity=ProximitySettings.from_dict(data.get("proximity", {})),
            audio=AudioSettings.from_dict(data.get("audio", {})),
            accessibility=AccessibilitySettings.from_dict(data.get("accessibility", {})),
        )

    @staticmethod
    def _safe_pin(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None


class NodeService:
    """Coordinate proximity sensing, audio playback, and MQTT messaging."""

    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG_PATH,
        *,
        sensor: Optional[ProximitySensor] = None,
        audio_player: Optional[AudioPlayer] = None,
        led_feedback: Optional[LedFeedback] = None,
        haptics: Optional[Haptics] = None,
        mqtt_client: Optional[Any] = None,
        auto_connect: bool = False,
    ) -> None:
        self.config_path = config_path
        self._raw_config: dict[str, Any] = {}
        self.config = self._load_config()

        self._sensor = sensor or ProximitySensor()
        self._audio = audio_player or AudioPlayer()
        self._led = led_feedback or self._create_led()
        self._haptics = haptics or self._create_haptics()

        self._mqtt = mqtt_client or self._create_mqtt_client()
        self._heartbeat_interval = HEARTBEAT_INTERVAL_SECONDS
        self._last_heartbeat_ts = 0.0
        self._last_trigger_ts = 0.0
        self._cooldown_until = 0.0
        self._pending_story_at: Optional[float] = None
        self._story_active = False
        self._story_reset_time = 0.0
        self._mystery_played = False

        self._load_audio_fragment()
        self._apply_accessibility()

        if auto_connect:
            self._connect_mqtt()

    # ------------------------------------------------------------------ Configuration

    def _load_config(self) -> NodeConfig:
        try:
            with self.config_path.open("r", encoding="utf-8") as handle:
                self._raw_config = yaml.safe_load(handle) or {}
        except FileNotFoundError:
            LOGGER.warning("Configuration missing at %s; using defaults.", self.config_path)
            self._raw_config = {}
        except yaml.YAMLError as exc:
            raise RuntimeError(f"Invalid YAML in {self.config_path}: {exc}") from exc
        return NodeConfig.from_dict(self._raw_config)

    def _load_audio_fragment(self) -> None:
        fragment_path = self.audio_fragment_path
        if fragment_path is None:
            LOGGER.info("No audio fragment configured for node %s.", self.config.node_id)
            return
        if not fragment_path.exists():
            LOGGER.warning("Audio fragment missing at %s", fragment_path)
            return
        self._audio.load(fragment_path)

    def _apply_accessibility(self) -> None:
        self._audio.set_safety_limit(self.config.accessibility.safety_limit())
        self._audio.set_volume(self.config.audio.volume)

    @property
    def audio_fragment_path(self) -> Optional[Path]:
        fragment = self.config.audio.fragment_file.strip()
        if not fragment:
            return None
        path = Path(fragment)
        if not path.is_absolute():
            path = self.config_path.parent / path
        return path

    # ------------------------------------------------------------------ MQTT

    def _create_mqtt_client(self) -> Any:
        if mqtt is None:
            raise RuntimeError("paho-mqtt is required for node operation.")
        client = mqtt.Client()
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        return client

    def _connect_mqtt(self) -> None:
        if not hasattr(self._mqtt, "connect"):
            return
        broker_host = str(self._raw_config.get("broker_host", "localhost"))
        broker_port = int(self._raw_config.get("broker_port", 1883))
        LOGGER.info("Connecting to MQTT broker at %s:%s", broker_host, broker_port)
        try:
            self._mqtt.connect(broker_host, broker_port, keepalive=60)
            if hasattr(self._mqtt, "loop_start"):
                self._mqtt.loop_start()
        except Exception as exc:  # pragma: no cover - requires broker
            LOGGER.error("Failed to connect to MQTT broker: %s", exc)

    def _on_connect(
        self,
        client: MQTTClient,
        _userdata: Any,
        _flags: Any,
        rc: int,
    ) -> None:  # pragma: no cover - requires broker
        if rc != 0:
            LOGGER.error("MQTT connection failed with rc=%s", rc)
            return
        client.subscribe(node_config_topic(self.config.node_id))
        if self.config.role == "mystery":
            client.subscribe(hub_state_topic())

    def _on_message(
        self,
        _client: MQTTClient,
        _userdata: Any,
        message: MQTTMessage,
    ) -> None:  # pragma: no cover - requires broker
        payload = message.payload.decode("utf-8") if message.payload else ""
        self.handle_mqtt_message(message.topic, payload)

    def handle_mqtt_message(self, topic: str, payload: str) -> None:
        """Handle inbound MQTT messages; exposed for unit testing."""
        if topic == node_config_topic(self.config.node_id):
            self._handle_config_message(payload)
        elif topic == hub_state_topic() and self.config.role == "mystery":
            self._handle_state_message(payload)
        else:
            LOGGER.debug("Unhandled MQTT topic %s", topic)

    def _handle_config_message(self, payload: str) -> None:
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            LOGGER.warning("Invalid configuration payload: %s", payload)
            return
        if not isinstance(data, dict):
            LOGGER.warning("Configuration payload must be an object.")
            return

        applied: list[str] = []
        if "audio" in data and isinstance(data["audio"], dict):
            self.config.audio.update(data["audio"])
            self._load_audio_fragment()
            applied.append("audio")
        if "proximity" in data and isinstance(data["proximity"], dict):
            self.config.proximity.update(data["proximity"])
            applied.append("proximity")
        if "accessibility" in data and isinstance(data["accessibility"], dict):
            self.config.accessibility.update(data["accessibility"])
            self._apply_accessibility()
            applied.append("accessibility")

        ack_payload = json.dumps(
            {
                "node_id": self.config.node_id,
                "status": "ok",
                "applied": applied,
            }
        )
        self._mqtt.publish(node_ack_topic(self.config.node_id), ack_payload, qos=1)

    def _handle_state_message(self, payload: str) -> None:
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            LOGGER.warning("Invalid hub state payload: %s", payload)
            return
        unlocked = bool(data.get("unlocked"))
        if unlocked and not self._mystery_played:
            LOGGER.info("Narrative unlocked; playing finale fragment on %s.", self.config.node_id)
            now = time.time()
            self._start_story(now, force=True, mystery=True)
            self._mystery_played = True
        elif not unlocked:
            self._mystery_played = False

    # ------------------------------------------------------------------ Hardware helpers

    def _create_led(self) -> Optional[LedFeedback]:
        if self.config.led_pin is None:
            return None
        try:
            return LedFeedback(self.config.led_pin)
        except Exception as exc:  # pragma: no cover - hardware only
            LOGGER.warning("Unable to initialise LED on pin %s: %s", self.config.led_pin, exc)
            return None

    def _create_haptics(self) -> Optional[Haptics]:
        if self.config.haptic_pin is None:
            return None
        try:
            return Haptics(self.config.haptic_pin)
        except Exception as exc:  # pragma: no cover - hardware only
            LOGGER.warning(
                "Unable to initialise haptics on pin %s: %s",
                self.config.haptic_pin,
                exc,
            )
            return None

    # ------------------------------------------------------------------ Runtime loop

    def run_once(self, now: Optional[float] = None) -> dict[str, Any]:
        """Execute a single iteration of the service loop; returns telemetry for testing."""
        timestamp = now if now is not None else time.time()
        distance = self._sensor.read_distance_mm()

        self._process_distance(distance, timestamp)
        self._process_pending_story(timestamp)
        self._update_story_state(timestamp)
        heartbeat = self._publish_heartbeat_if_due(timestamp)

        return {
            "node_id": self.config.node_id,
            "role": self.config.role,
            "distance_mm": distance,
            "timestamp": timestamp,
            "heartbeat": heartbeat,
        }

    def run_forever(self) -> None:  # pragma: no cover - blocking loop
        LOGGER.info("Starting node service for %s (%s)", self.config.node_id, self.config.role)
        configure_node_logging()
        self._connect_mqtt()
        try:
            while True:
                self.run_once()
                time.sleep(0.2)
        except KeyboardInterrupt:
            LOGGER.info("Node service terminated by operator.")
        finally:
            if hasattr(self._mqtt, "loop_stop"):
                self._mqtt.loop_stop()

    # ------------------------------------------------------------------ Distance handling

    def _process_distance(self, distance: Optional[int], now: float) -> None:
        if self.config.role == "mystery":
            return  # mystery nodes react to hub state instead of proximity

        if distance is None:
            self._cancel_pending_story()
            if self._led and not self._story_active:
                self._led.off()
            return

        proximity = self.config.proximity
        start_threshold = proximity.story_threshold_mm - proximity.hysteresis_mm

        if distance <= start_threshold:
            self._queue_story(now)
        else:
            self._cancel_pending_story()
            if self._led:
                if self.config.accessibility.proximity_glow:
                    intensity = self._calculate_glow(distance)
                    self._led.glow(intensity)
                elif not self._story_active:
                    self._led.off()

    def _calculate_glow(self, distance: int) -> float:
        proximity = self.config.proximity
        span = max(1, proximity.max_mm - proximity.min_mm)
        value = 1.0 - max(0.0, min(1.0, (distance - proximity.min_mm) / span))
        return max(0.0, min(1.0, value))

    def _queue_story(self, now: float) -> None:
        if self._story_active or now < self._cooldown_until:
            return
        buffer_seconds = self.config.accessibility.mobility_buffer_ms / 1000.0
        if buffer_seconds <= 0:
            self._start_story(now)
        else:
            if self._pending_story_at is None:
                self._pending_story_at = now + buffer_seconds

    def _cancel_pending_story(self) -> None:
        self._pending_story_at = None

    def _process_pending_story(self, now: float) -> None:
        if self._pending_story_at is not None and now >= self._pending_story_at:
            self._start_story(now)
            self._pending_story_at = None

    def _start_story(self, now: float, *, force: bool = False, mystery: bool = False) -> None:
        if not force and (now < self._cooldown_until or self._story_active):
            return
        fragment_path = self.audio_fragment_path
        if fragment_path is None or not fragment_path.exists():
            LOGGER.warning("Unable to play story; audio fragment missing.")
            return

        self._audio.load(fragment_path)
        self._apply_accessibility()
        self._audio.play(
            loop=self.config.accessibility.repeat > 0,
            pace=self.config.accessibility.pace,
            repeat=self.config.accessibility.repeat,
        )
        self._story_active = True
        self._last_trigger_ts = now
        self._cooldown_until = now + RETRIGGER_COOLDOWN_SECONDS
        self._story_reset_time = now + STORY_RESET_SECONDS

        if self._led:
            if mystery:
                self._led.blink(on_s=0.2, off_s=0.2)
            elif self.config.accessibility.visual_pulse:
                self._led.blink(on_s=0.4, off_s=0.4)
            else:
                self._led.glow(1.0)
        if self._haptics:
            self._haptics.pulse(180)

        trigger_payload = json.dumps(
            {
                "node_id": self.config.node_id,
                "role": self.config.role,
                "ts": now,
            }
        )
        self._mqtt.publish(trigger_topic(self.config.node_id), trigger_payload, qos=1)

    def _update_story_state(self, now: float) -> None:
        if self._story_active and now >= self._story_reset_time:
            self._story_active = False
            if self._led and not self.config.accessibility.proximity_glow:
                self._led.off()

    # ------------------------------------------------------------------ Heartbeat

    def _publish_heartbeat_if_due(self, now: float) -> Optional[dict[str, Any]]:
        if now - self._last_heartbeat_ts < self._heartbeat_interval:
            return None

        payload = {
            "node_id": self.config.node_id,
            "role": self.config.role,
            "ts": now,
            "rssi": self._get_rssi(),
            "sensor_status": self._sensor.status,
        }
        message = json.dumps(payload)
        info = self._mqtt.publish(health_topic(self.config.node_id), message, qos=0)
        if hasattr(info, "rc") and info.rc != 0:
            LOGGER.warning("Heartbeat publish returned rc=%s", info.rc)
        self._last_heartbeat_ts = now
        return payload

    def _get_rssi(self) -> int:
        """Estimate Wi-Fi signal strength in dBm."""
        try:
            with open("/proc/net/wireless", encoding="utf-8") as handle:
                for line in handle:
                    if "wlan0" in line or "wlan1" in line:
                        parts = line.split()
                        if len(parts) >= 4:
                            # The level is usually the 4th field (index 3)
                            # Some drivers report it as a negative dBm value directly,
                            # others as a positive quality value. We assume dBm if negative.
                            val = float(parts[3].rstrip("."))
                            return int(val) if val < 0 else int(val - 100)
        except (FileNotFoundError, ValueError, IndexError):
            pass
        return 0


def main() -> None:  # pragma: no cover - script entry point
    configure_node_logging()
    NodeService(auto_connect=True).run_forever()


if __name__ == "__main__":  # pragma: no cover - script entry point
    main()
