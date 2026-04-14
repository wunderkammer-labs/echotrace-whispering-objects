"""Integration tests for authenticated dashboard endpoints."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any, Dict, Iterator, Tuple

import pytest
import yaml  # type: ignore[import]

from flask.testing import FlaskClient

from hub.config_loader import load_config
from hub.content_manager import ContentManager
from hub.dashboard_app import DashboardContext
from hub.hub_listener import ConfigPushError


class FakeHubController:
    """Capture configuration pushes without requiring a live broker."""

    def __init__(self) -> None:
        self.calls: list[Tuple[str, Dict[str, Any]]] = []
        self.state: Dict[str, Any] = {"unlocked": False, "triggered": []}
        self.health: Dict[str, float] = {}
        self.error: ConfigPushError | None = None
        self.version: int = 0

    def push_node_config(self, node_id: str, payload: Dict[str, Any]) -> bool:
        if self.error:
            raise self.error
        self.calls.append((node_id, payload))
        return True

    def push_node_configs(self, payloads: Dict[str, Dict[str, Any]]) -> Dict[str, bool]:
        self.version += 1
        results: Dict[str, bool] = {}
        for node_id, payload in payloads.items():
            stamped = dict(payload)
            stamped["config_version"] = self.version
            results[node_id] = self.push_node_config(node_id, stamped)
        return results

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
    import hub.staff_settings as staff_settings

    staff_path = tmp_path / "staff_settings.yaml"
    if staff_settings.STAFF_SETTINGS_PATH.exists():
        staff_path.write_text(staff_settings.STAFF_SETTINGS_PATH.read_text(), encoding="utf-8")
    else:
        staff_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(staff_settings, "STAFF_SETTINGS_PATH", staff_path)
    monkeypatch.setattr(dashboard_app, "STAFF_SETTINGS_PATH", staff_path)

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
    assert b"Start Here" in authed.data
    assert b"Open Exhibit" in authed.data


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
    object1_payload = next(payload for node_id, payload in controller.calls if node_id == "object1")
    assert "fragment_file" in object1_payload["audio"]
    assert isinstance(object1_payload.get("config_version"), int)


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


def test_select_pack_pushes_fragment_files(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Selecting a pack should broadcast runtime audio fragment assignments."""
    testing_client, controller, _path = client
    controller.calls.clear()

    response = testing_client.post(
        "/api/select-pack",
        json={"pack_name": "sample-pack"},
        headers=_auth_header(),
    )
    assert response.status_code == 200
    assert controller.calls

    object1_payload = next(payload for node_id, payload in controller.calls if node_id == "object1")
    assert "audio" in object1_payload
    assert "fragment_file" in object1_payload["audio"]
    assert isinstance(object1_payload.get("config_version"), int)


def test_select_pack_missing_returns_404(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Selecting an unknown pack should return a controlled 404 instead of a 500."""
    testing_client, _controller, _path = client
    response = testing_client.post(
        "/api/select-pack",
        json={"pack_name": "does-not-exist"},
        headers=_auth_header(),
    )
    assert response.status_code == 404


def test_setup_and_labels_pages_render(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Staff-facing setup and print views should render for operators."""
    testing_client, _controller, _path = client
    setup_response = testing_client.get("/setup", headers=_auth_header())
    assert setup_response.status_code == 200
    assert b"Set Up Exhibit" in setup_response.data

    labels_response = testing_client.get("/labels", headers=_auth_header())
    assert labels_response.status_code == 200
    assert b"Print Visitor Cards" in labels_response.data


def test_setup_wizard_puts_pack_selection_before_naming(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """First-run setup should start with pack selection, then move to object naming."""
    testing_client, _controller, _path = client
    response = testing_client.get("/setup", headers=_auth_header())
    assert response.status_code == 200
    page = response.data.decode("utf-8")
    assert page.index("1. Choose the Story") < page.index("2. Name the Objects for Staff")


def test_accessibility_page_uses_plain_language_for_quiet_hours(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Accessibility settings should avoid raw JSON language in the operator UI."""
    testing_client, _controller, _path = client
    response = testing_client.get("/accessibility", headers=_auth_header())
    assert response.status_code == 200
    page = response.data.decode("utf-8")
    assert "JSON array" not in page
    assert "Enter one time range per line" in page


def test_content_page_shows_readiness_language(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Content management should emphasize operational readiness instead of raw file jargon."""
    testing_client, _controller, _path = client
    response = testing_client.get("/content", headers=_auth_header())
    assert response.status_code == 200
    page = response.data.decode("utf-8")
    assert "Visitor-ready" in page
    assert "Current Story Review" in page


def test_safe_pack_validation_handles_malformed_pack(tmp_path: Path) -> None:
    """Dashboard pack summaries should degrade gracefully when one pack is malformed."""
    bad_pack = tmp_path / "broken-pack"
    bad_pack.mkdir()
    (bad_pack / "pack.yaml").write_text("name: [broken", encoding="utf-8")

    context = DashboardContext(
        config=load_config(),
        content_manager=ContentManager(packs_root=tmp_path),
        accessibility={"global": {}, "presets": {}, "per_node_overrides": {}},
        staff_settings={},
    )

    report = context.safe_pack_validation("broken-pack")
    assert report.valid is False
    assert report.missing_assets
    assert "Failed to parse pack.yaml" in report.missing_assets[0]


def test_nodes_page_handles_missing_heartbeat(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Nodes page should render even when a node has never reported health."""
    testing_client, _controller, _path = client
    response = testing_client.get("/nodes", headers=_auth_header())
    assert response.status_code == 200
    assert b"Object Status" in response.data
    assert b"Waiting for first check-in" in response.data


def test_daily_start_progress_is_server_backed(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Daily opening progress should reflect server-side actions."""
    testing_client, _controller, _path = client
    initial = testing_client.get("/daily-start", headers=_auth_header())
    page = initial.data.decode("utf-8")
    assert "Choose preset" in page
    assert "Check objects" in page

    preset_response = testing_client.post(
        "/api/operation-preset",
        json={"preset_key": "quiet_morning"},
        headers=_auth_header(),
    )
    assert preset_response.status_code == 200

    test_response = testing_client.post(
        "/api/node-test",
        json={"node_id": "object1", "action": "sound"},
        headers=_auth_header(),
    )
    assert test_response.status_code == 200

    refreshed = testing_client.get("/daily-start", headers=_auth_header())
    refreshed_page = refreshed.data.decode("utf-8")
    assert "Quiet Morning" in refreshed_page
    assert "Checked today via sound" in refreshed_page


def test_problems_page_offers_in_place_actions(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Problem cards should offer direct next actions instead of only descriptive text."""
    testing_client, controller, _path = client
    controller.health = {
        "object1": {
            "age": 180.0,
            "config_sync": "stale",
            "sensor_status": "ok",
            "rssi": -40,
        }
    }
    response = testing_client.get("/problems", headers=_auth_header())
    assert response.status_code == 200
    page = response.data.decode("utf-8")
    assert "Try Reconnect" in page
    assert "Open Daily Start" in page


def test_node_display_names_and_undo_work(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Friendly names should persist and be reversible."""
    testing_client, _controller, _path = client
    rename_response = testing_client.post(
        "/api/node-display-names",
        json={"names": {"object1": "Entrance Vase"}},
        headers=_auth_header(),
    )
    assert rename_response.status_code == 200
    assert rename_response.get_json()["names"]["object1"] == "Entrance Vase"

    undo_response = testing_client.post(
        "/api/undo-last-change",
        json={},
        headers=_auth_header(),
    )
    assert undo_response.status_code == 200


def test_node_test_action_uses_push_config(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """One-click node tests should route through the hub controller."""
    testing_client, controller, _path = client
    controller.calls.clear()
    response = testing_client.post(
        "/api/node-test",
        json={"node_id": "object1", "action": "light"},
        headers=_auth_header(),
    )
    assert response.status_code == 200
    node_id, payload = controller.calls[-1]
    assert node_id == "object1"
    assert payload["test"]["action"] == "light"
    assert response.get_json()["message"].startswith("Light test sent to ")


def test_node_test_rejects_unknown_action(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Node test API should reject unsupported actions at the request boundary."""
    testing_client, controller, _path = client
    controller.calls.clear()
    response = testing_client.post(
        "/api/node-test",
        json={"node_id": "object1", "action": "format_disk"},
        headers=_auth_header(),
    )
    assert response.status_code == 400
    assert controller.calls == []


def test_node_test_rejects_unknown_node_id(
    client: tuple[FlaskClient, FakeHubController, Path]
) -> None:
    """Node-control endpoints should reject node IDs outside the current exhibit."""
    testing_client, controller, _path = client
    controller.calls.clear()
    response = testing_client.post(
        "/api/node-test",
        json={"node_id": "ghost-node", "action": "light"},
        headers=_auth_header(),
    )
    assert response.status_code == 404
    assert controller.calls == []
