"""Integration tests for authenticated dashboard endpoints."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

import pytest
import yaml  # type: ignore[import]

from flask.testing import FlaskClient

from hub.hub_listener import ConfigPushError


class FakeHubController:
    """Capture configuration pushes without requiring a live broker."""

    def __init__(self) -> None:
        self.calls: list[Tuple[str, Dict[str, Any]]] = []
        self.state: Dict[str, Any] = {"unlocked": False, "triggered": []}
        self.health: Dict[str, float] = {}
        self.error: ConfigPushError | None = None

    def push_node_config(self, node_id: str, payload: Dict[str, Any]) -> bool:
        if self.error:
            raise self.error
        self.calls.append((node_id, payload))
        return True

    def get_state_snapshot(self) -> Dict[str, Any]:
        return dict(self.state)

    def reset_state(self) -> None:
        self.state = {"unlocked": False, "triggered": []}

    def get_health_snapshot(self) -> Dict[str, float]:
        return dict(self.health)


def _auth_header() -> dict[str, str]:
    token = base64.b64encode(b"admin:secret").decode("utf-8")
    return {"Authorization": f"Basic {token}"}


@pytest.fixture()
def client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FlaskClient, FakeHubController, Path]]:
    os.environ.setdefault("ECHOTRACE_ADMIN_USER", "admin")
    os.environ.setdefault("ECHOTRACE_ADMIN_PASS", "secret")

    import hub.accessibility_store as store

    cloned_path = tmp_path / "accessibility_profiles.yaml"
    if store.ACCESSIBILITY_PATH.exists():
        cloned_path.write_text(store.ACCESSIBILITY_PATH.read_text(), encoding="utf-8")
    else:
        cloned_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(store, "ACCESSIBILITY_PATH", cloned_path)

    import hub.dashboard_app as dashboard_app

    monkeypatch.setattr(dashboard_app, "ACCESSIBILITY_PATH", cloned_path)

    controller = FakeHubController()
    app = dashboard_app.create_app(hub_controller=controller)
    app.config.update(TESTING=True)

    with app.test_client() as testing_client:
        yield testing_client, controller, cloned_path


def test_overview_requires_auth(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Ensure unauthenticated access is blocked."""
    testing_client, _controller, _path = client
    response = testing_client.get("/")
    assert response.status_code == 401

    authed = testing_client.get("/", headers=_auth_header())
    assert authed.status_code == 200
    assert b"Installation Snapshot" in authed.data


def test_api_state_and_reset(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Check narrative state JSON surfaces and resets."""
    testing_client, _controller, _path = client
    state_resp = testing_client.get("/api/state", headers=_auth_header())
    assert state_resp.status_code == 200
    payload = state_resp.get_json()
    assert "unlocked" in payload
    assert "triggered" in payload

    reset_resp = testing_client.post("/api/reset-state", json={}, headers=_auth_header())
    assert reset_resp.status_code == 200
    reset_payload = reset_resp.get_json()
    assert reset_payload["ok"] is True


def test_state_change_rejects_cross_origin_post(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """State-changing endpoints should reject cross-origin POSTs."""
    testing_client, _controller, _path = client
    response = testing_client.post(
        "/api/reset-state",
        json={},
        headers={**_auth_header(), "Origin": "https://attacker.invalid"},
    )
    assert response.status_code == 403


def test_state_change_allows_same_origin_post(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Same-origin POSTs should continue to work."""
    testing_client, _controller, _path = client
    response = testing_client.post(
        "/api/reset-state",
        json={},
        headers={**_auth_header(), "Origin": "http://localhost"},
    )
    assert response.status_code == 200


def test_apply_preset_triggers_push(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Applying a preset should broadcast accessibility updates to nodes."""
    testing_client, controller, _path = client
    controller.calls.clear()
    response = testing_client.post(
        "/api/apply-preset",
        json={"preset_name": "hard_of_hearing"},
        headers=_auth_header(),
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert controller.calls, "Expected accessibility broadcast to invoke controller."
    assert "object1" in data["push"]


def test_push_config_conflict_returns_error(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Conflicting config pushes should return HTTP 409."""
    testing_client, controller, _path = client
    controller.error = ConfigPushError("already busy", status_code=409)
    response = testing_client.post(
        "/api/push-config",
        json={"node_id": "object1", "payload": {"audio": {"volume": 0.5}}},
        headers=_auth_header(),
    )
    assert response.status_code == 409
    controller.error = None


def test_invalid_quiet_hours_rejected(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Invalid quiet hour entries should return 400."""
    testing_client, _controller, _path = client
    response = testing_client.post(
        "/api/apply-preset",
        json={"global": {"quiet_hours": ["invalid"]}},
        headers=_auth_header(),
    )
    assert response.status_code == 400


def test_set_per_node_override_updates_yaml(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Per-node overrides should persist and trigger config pushes."""
    testing_client, controller, profiles_path = client
    controller.calls.clear()
    node_override = {
        "visual_pulse": True,
        "repeat": 1,
        "pace": 0.95,
    }
    response = testing_client.post(
        "/api/accessibility/override",
        json={"node_id": "object1", "overrides": node_override},
        headers=_auth_header(),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert controller.calls, "Expected override to push configuration."
    assert "object1" in payload["push"]

    stored = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    assert stored["per_node_overrides"]["object1"]["visual_pulse"] is True


def test_analytics_summary_no_data(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Analytics summary should report lack of data gracefully."""
    testing_client, _controller, _path = client
    response = testing_client.get("/api/analytics/summary", headers=_auth_header())
    assert response.status_code == 404
