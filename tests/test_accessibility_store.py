"""Tests for accessibility profile runtime derivation."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from hub.accessibility_store import derive_runtime_payloads, load_profiles


def test_quiet_hours_dim_output() -> None:
    """Quiet hours should reduce sensory output and volume."""
    profiles = {
        "global": {
            "quiet_hours": ["08:00-09:00"],
            "captions": False,
            "sensory_friendly": False,
            "safety_limiter": True,
        },
        "per_node_overrides": {},
    }
    nodes: dict[str, dict[str, Any]] = {"object1": {}}

    payloads = derive_runtime_payloads(
        profiles,
        nodes,
        now=datetime(2025, 1, 1, 8, 30),
    )
    node_payload = payloads["object1"]

    assert node_payload["audio"]["volume"] <= 0.45
    assert node_payload["accessibility"]["visual_pulse"] is False
    assert node_payload["accessibility"]["proximity_glow"] is False


def test_quiet_hours_respect_explicit_overrides() -> None:
    """Explicit LED overrides should persist during quiet hours."""
    profiles = {
        "global": {
            "quiet_hours": [{"bogus": "value"}],
            "sensory_friendly": False,
        },
        "per_node_overrides": {
            "object1": {"visual_pulse": True, "proximity_glow": True},
        },
    }
    nodes: dict[str, dict[str, Any]] = {"object1": {}}

    payloads = derive_runtime_payloads(
        profiles,
        nodes,
        now=datetime(2025, 1, 1, 1, 0),
    )
    node_payload = payloads["object1"]
    assert node_payload["accessibility"]["visual_pulse"] is True
    assert node_payload["accessibility"]["proximity_glow"] is True


def test_load_profiles_validates_quiet_hours(tmp_path: Path) -> None:
    """Loading profiles should raise when quiet_hours entries are invalid."""
    cfg = tmp_path / "profiles.yaml"
    cfg.write_text(
        "global:\n  quiet_hours:\n    - invalid\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_profiles(cfg)


@pytest.mark.parametrize("section_name", ["global", "presets", "per_node_overrides"])
def test_load_profiles_requires_mapping_sections(tmp_path: Path, section_name: str) -> None:
    """Invalid section types should raise a controlled validation error."""
    cfg = tmp_path / "profiles.yaml"
    cfg.write_text(f"{section_name}:\n  - invalid\n", encoding="utf-8")

    with pytest.raises(ValueError, match=f"'{section_name}' section must be a mapping"):
        load_profiles(cfg)
