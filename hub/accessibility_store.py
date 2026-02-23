"""Utilities for loading, persisting, and deriving accessibility profiles."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, time as time_cls
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import]

ACCESSIBILITY_PATH = Path(__file__).resolve().parent / "accessibility_profiles.yaml"


def load_profiles(path: Path | None = None) -> dict[str, Any]:
    """Load accessibility profiles from disk."""
    target = path or ACCESSIBILITY_PATH
    if not target.exists():
        return {"global": {}, "presets": {}, "per_node_overrides": {}}
    with target.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError("Accessibility profiles file must contain a mapping.")
    global_settings = _require_mapping_section(data, "global")
    _require_mapping_section(data, "presets")
    _require_mapping_section(data, "per_node_overrides")
    try:
        ensure_quiet_hours_valid(global_settings.get("quiet_hours"))
    except ValueError as exc:
        raise ValueError(f"Invalid quiet_hours configuration: {exc}") from exc
    return data


def save_profiles(profiles: dict[str, Any], path: Path | None = None) -> None:
    """Persist accessibility profiles to disk."""
    target = path or ACCESSIBILITY_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(profiles, handle, sort_keys=True)


def apply_preset(profiles: dict[str, Any], preset_name: str) -> dict[str, Any]:
    """Apply a preset to the global accessibility configuration."""
    presets = profiles.get("presets", {})
    if preset_name not in presets:
        raise KeyError(f"Preset '{preset_name}' not found.")
    global_settings = profiles.setdefault("global", {})
    preset_values = presets[preset_name] or {}
    if not isinstance(global_settings, dict):
        raise ValueError("Global accessibility settings must be a mapping.")
    if not isinstance(preset_values, dict):
        raise ValueError("Preset values must be a mapping.")
    global_settings.update(preset_values)
    return profiles


def set_per_node_override(
    profiles: dict[str, Any],
    node_id: str,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Persist per-node overrides, removing entries when overrides are empty."""
    per_node = profiles.setdefault("per_node_overrides", {})
    if not isinstance(per_node, dict):
        raise ValueError("per_node_overrides must be a mapping.")
    normalised = {key: value for key, value in overrides.items() if value not in (None, "")}
    if normalised:
        per_node[node_id] = normalised
    else:
        per_node.pop(node_id, None)
    return profiles


def derive_runtime_payloads(
    profiles: dict[str, Any],
    nodes: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Return node-specific configuration payloads derived from accessibility settings."""
    global_settings = _ensure_mapping(profiles.get("global"))
    overrides = _ensure_mapping(profiles.get("per_node_overrides"))
    quiet_hours_active = _quiet_hours_active(global_settings.get("quiet_hours"), now=now)

    payloads: dict[str, dict[str, Any]] = {}
    for node_id in nodes.keys():
        node_override = _ensure_mapping(overrides.get(node_id))
        payloads[node_id] = _build_node_payload(
            global_settings,
            node_override,
            quiet_mode=quiet_hours_active,
        )
    return payloads


def _build_node_payload(
    global_settings: dict[str, Any],
    node_override: dict[str, Any],
    quiet_mode: bool,
) -> dict[str, Any]:
    captions = bool(node_override.get("captions", global_settings.get("captions", False)))
    visual_pulse = bool(node_override.get("visual_pulse", False))
    proximity_glow = bool(node_override.get("proximity_glow", True))
    default_buffer = _clamp_int(global_settings.get("mobility_buffer_ms", 800), 0, 60000)
    mobility_buffer_ms = _clamp_int(
        node_override.get("mobility_buffer_ms", default_buffer),
        0,
        60000,
    )
    repeat = _clamp_int(node_override.get("repeat", 0), 0, 2)
    base_pace = 0.9 if global_settings.get("sensory_friendly") else 1.0
    pace = _clamp_float(node_override.get("pace", base_pace), 0.85, 1.15)
    safety_limiter = bool(
        node_override.get("safety_limiter", global_settings.get("safety_limiter", True))
    )

    volume = node_override.get("volume")
    if volume is None:
        volume = 0.7
        if global_settings.get("sensory_friendly"):
            volume = min(volume, 0.55)
        if quiet_mode:
            volume = min(volume, 0.45)
    volume = _clamp_float(volume, 0.0, 1.0)

    if quiet_mode:
        if "visual_pulse" not in node_override:
            visual_pulse = False
        if "proximity_glow" not in node_override:
            proximity_glow = False

    accessibility_payload = {
        "captions": captions,
        "visual_pulse": visual_pulse,
        "proximity_glow": proximity_glow,
        "mobility_buffer_ms": max(0, mobility_buffer_ms),
        "repeat": repeat,
        "pace": pace,
        "safety_limiter": safety_limiter,
    }

    return {
        "audio": {"volume": volume},
        "accessibility": accessibility_payload,
    }


def _ensure_mapping(candidate: Any) -> dict[str, Any]:
    if isinstance(candidate, Mapping):
        return dict(candidate)
    return {}


def _require_mapping_section(data: dict[str, Any], section_name: str) -> dict[str, Any]:
    value = data.get(section_name)
    if value is None:
        section: dict[str, Any] = {}
        data[section_name] = section
        return section
    if not isinstance(value, Mapping):
        raise ValueError(f"Accessibility profiles '{section_name}' section must be a mapping.")
    section = dict(value)
    data[section_name] = section
    return section


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def _quiet_hours_active(config_value: Any, now: datetime | None = None) -> bool:
    windows = _normalise_quiet_hours(config_value)
    if not windows:
        return False
    current = (now or datetime.now()).time()
    for start, end in windows:
        if start <= end:
            if start <= current < end:
                return True
        else:
            if current >= start or current < end:
                return True
    return False


def _normalise_quiet_hours(value: Any) -> list[tuple[time_cls, time_cls]]:
    try:
        candidates = _coerce_quiet_hour_entries(value)
    except ValueError:
        return []
    windows: list[tuple[time_cls, time_cls]] = []
    for entry in candidates:
        parts = [part.strip() for part in entry.split("-", 1)]
        if len(parts) != 2:
            continue
        start = _parse_time(parts[0])
        end = _parse_time(parts[1])
        if start is None or end is None:
            continue
        windows.append((start, end))
    return windows


def _parse_time(value: str) -> time_cls | None:
    try:
        hours, minutes = value.split(":", 1)
        hour_i = int(hours)
        minute_i = int(minutes)
        if not (0 <= hour_i < 24 and 0 <= minute_i < 60):
            return None
        return time_cls(hour=hour_i, minute=minute_i)
    except ValueError:
        return None


def _coerce_quiet_hour_entries(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        raise ValueError("quiet_hours must be a list of HH:MM-HH:MM strings.")
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, Sequence):
        candidates = [str(item) for item in value]
    else:
        raise ValueError("quiet_hours must be provided as a string or list.")
    entries: list[str] = []
    for entry in candidates:
        if not isinstance(entry, str):
            raise ValueError("quiet_hours entries must be strings.")
        stripped = entry.strip()
        if stripped:
            entries.append(stripped)
    return entries


def ensure_quiet_hours_valid(value: Any) -> None:
    entries = _coerce_quiet_hour_entries(value)
    if not entries:
        return
    invalid: list[str] = []
    for entry in entries:
        parts = [part.strip() for part in entry.split("-", 1)]
        if len(parts) != 2:
            invalid.append(entry)
            continue
        if _parse_time(parts[0]) is None or _parse_time(parts[1]) is None:
            invalid.append(entry)
    if invalid:
        raise ValueError(
            "Invalid quiet_hours entries (expected HH:MM-HH:MM): " + ", ".join(invalid)
        )


__all__ = [
    "ACCESSIBILITY_PATH",
    "apply_preset",
    "derive_runtime_payloads",
    "ensure_quiet_hours_valid",
    "load_profiles",
    "save_profiles",
    "set_per_node_override",
]
