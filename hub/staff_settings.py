"""Persistence helpers for staff-facing dashboard preferences and undo state."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import]


STAFF_SETTINGS_PATH = Path(__file__).resolve().parent / "staff_settings.yaml"
MAX_UNDO_DEPTH = 10


def _default_settings() -> dict[str, Any]:
    return {
        "museum_staff_mode": True,
        "selected_pack": "",
        "node_display_names": {},
        "setup_complete": False,
        "last_preset_name": "",
        "daily_start": {
            "last_preset_date": "",
            "last_preset_label": "",
            "node_tests": {},
        },
        "undo_stack": [],
    }


def load_staff_settings(path: Path | None = None) -> dict[str, Any]:
    """Load persisted staff settings, filling in any missing defaults."""
    target = path or STAFF_SETTINGS_PATH
    if not target.exists():
        return _default_settings()
    with target.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, Mapping):
        return _default_settings()
    merged = _default_settings()
    merged.update(dict(data))
    if not isinstance(merged.get("node_display_names"), Mapping):
        merged["node_display_names"] = {}
    if not isinstance(merged.get("daily_start"), Mapping):
        merged["daily_start"] = _default_settings()["daily_start"]
    if not isinstance(merged.get("undo_stack"), list):
        merged["undo_stack"] = []
    return merged


def save_staff_settings(settings: dict[str, Any], path: Path | None = None) -> None:
    """Persist staff settings to disk."""
    target = path or STAFF_SETTINGS_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(settings, handle, sort_keys=True)


def default_display_name(node_id: str, fallback: str | None = None) -> str:
    """Return a readable display name when a staff label has not been set."""
    if fallback:
        return fallback
    return node_id.replace("_", " ").replace("-", " ").title()


def push_undo_snapshot(
    settings: dict[str, Any],
    *,
    selected_pack: str,
    accessibility: dict[str, Any],
    node_display_names: dict[str, str],
    last_preset_name: str,
) -> None:
    """Store a reversible dashboard state snapshot."""
    snapshot = {
        "selected_pack": selected_pack,
        "accessibility": deepcopy(accessibility),
        "node_display_names": dict(node_display_names),
        "last_preset_name": last_preset_name,
    }
    undo_stack = settings.setdefault("undo_stack", [])
    if not isinstance(undo_stack, list):
        undo_stack = []
        settings["undo_stack"] = undo_stack
    undo_stack.append(snapshot)
    if len(undo_stack) > MAX_UNDO_DEPTH:
        del undo_stack[:-MAX_UNDO_DEPTH]


def pop_undo_snapshot(settings: dict[str, Any]) -> dict[str, Any] | None:
    """Pop the most recent undo snapshot if available."""
    undo_stack = settings.get("undo_stack", [])
    if not isinstance(undo_stack, list) or not undo_stack:
        return None
    raw = undo_stack.pop()
    if not isinstance(raw, Mapping):
        return None
    return dict(raw)
