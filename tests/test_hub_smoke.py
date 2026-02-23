"""Smoke tests for the dashboard Flask application."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("ECHOTRACE_ADMIN_USER", "admin")
os.environ.setdefault("ECHOTRACE_ADMIN_PASS", "secret")

from hub.dashboard_app import create_app  # noqa: E402


def test_health_endpoint_returns_ok() -> None:
    """Ensure the /health endpoint responds with a JSON payload."""
    app = create_app()
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_health_endpoint_has_security_headers() -> None:
    """Baseline responses should include hardening headers."""
    app = create_app()
    client = app.test_client()
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "same-origin"


def test_transcript_endpoint_sets_sandbox_csp() -> None:
    """Transcript responses should be sandboxed to reduce script execution risk."""
    app = create_app()
    client = app.test_client()
    response = client.get("/transcripts/sample-pack/object1_en.html")

    assert response.status_code == 200
    csp = response.headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert response.headers.get("Cross-Origin-Resource-Policy") == "same-origin"


def test_create_app_falls_back_on_invalid_accessibility_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed accessibility YAML should not prevent app boot."""
    invalid_profiles = tmp_path / "accessibility_profiles.yaml"
    invalid_profiles.write_text("global:\n  - not-a-mapping\n", encoding="utf-8")

    import hub.accessibility_store as store
    import hub.dashboard_app as dashboard_app

    monkeypatch.setattr(store, "ACCESSIBILITY_PATH", invalid_profiles)
    monkeypatch.setattr(dashboard_app, "ACCESSIBILITY_PATH", invalid_profiles)

    app = dashboard_app.create_app()
    context = app.config["DASHBOARD_CONTEXT"]

    assert context.accessibility == {"global": {}, "presets": {}, "per_node_overrides": {}}
