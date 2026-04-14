"""Flask dashboard application serving the EchoTrace hub UI."""

from __future__ import annotations

import functools
import hmac
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, TypeVar, cast
from urllib.parse import urlparse
from typing_extensions import Protocol

from flask import (
    Flask,
    Request,
    Response,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.wrappers.response import Response as WerkzeugResponse

from .accessibility_store import (
    ACCESSIBILITY_PATH,
    apply_preset,
    derive_runtime_payloads,
    ensure_quiet_hours_valid,
    load_profiles,
    save_profiles,
    set_per_node_override,
)
from .config_loader import HubConfig, load_config
from .content_manager import ContentManager, ContentPack, MediaAsset, PackValidationReport
from .hub_listener import ConfigPushError
from .event_logging import CsvEventLogger, summarize_events
from .narrative_state import NarrativeState
from .staff_settings import (
    STAFF_SETTINGS_PATH,
    default_display_name,
    load_staff_settings,
    pop_undo_snapshot,
    push_undo_snapshot,
    save_staff_settings,
)

ALLOWED_NODE_TEST_ACTIONS = frozenset({"sound", "light", "sensor", "reconnect"})


class HubControllerProtocol(Protocol):
    def push_node_config(self, node_id: str, payload: dict[str, Any]) -> bool:
        ...

    def push_node_configs(self, payloads: dict[str, dict[str, Any]]) -> dict[str, bool]:
        ...

    def get_state_snapshot(self) -> dict[str, Any]:
        ...

    def reset_state(self) -> None:
        ...

    def get_health_snapshot(self) -> dict[str, dict[str, Any]]:
        ...


class InProcessHubController:
    """Minimal controller used when no HubListener is attached."""

    def __init__(self, narrative_state: NarrativeState) -> None:
        self._state = narrative_state
        self._health: dict[str, dict[str, Any]] = {}

    def push_node_config(self, node_id: str, payload: dict[str, Any]) -> bool:  # pragma: no cover
        logging.getLogger(__name__).debug(
            "In-process hub controller received push to %s: %s", node_id, payload
        )
        return True

    def push_node_configs(self, payloads: dict[str, dict[str, Any]]) -> dict[str, bool]:
        return {
            node_id: self.push_node_config(node_id, payload)
            for node_id, payload in payloads.items()
        }

    def get_state_snapshot(self) -> dict[str, Any]:
        return self._state.snapshot()

    def reset_state(self) -> None:
        self._state.reset()

    def get_health_snapshot(self) -> dict[str, dict[str, Any]]:
        return dict(self._health)


@dataclass
class DashboardContext:
    """Bundle services and state shared by dashboard routes."""

    config: HubConfig
    content_manager: ContentManager
    accessibility: dict[str, Any]
    staff_settings: dict[str, Any]
    current_pack: ContentPack | None = None
    hub_controller: HubControllerProtocol | None = None

    def select_pack(self, pack_name: str) -> ContentPack:
        pack = self.content_manager.load_pack(pack_name)
        self.current_pack = pack
        self.staff_settings["selected_pack"] = pack_name
        self.save_staff_settings()
        return pack

    def reload_accessibility(self) -> None:
        self.accessibility = load_profiles(ACCESSIBILITY_PATH)

    def reload_staff_settings(self) -> None:
        self.staff_settings = load_staff_settings(STAFF_SETTINGS_PATH)

    def save_staff_settings(self) -> None:
        save_staff_settings(self.staff_settings, STAFF_SETTINGS_PATH)

    def snapshot_for_undo(self) -> None:
        push_undo_snapshot(
            self.staff_settings,
            selected_pack=self.current_pack.name if self.current_pack else "",
            accessibility=self.accessibility,
            node_display_names=self.display_names(),
            last_preset_name=str(self.staff_settings.get("last_preset_name", "")),
        )
        self.save_staff_settings()

    def restore_undo(self) -> dict[str, Any] | None:
        snapshot = pop_undo_snapshot(self.staff_settings)
        if snapshot is None:
            return None
        self.accessibility = dict(snapshot.get("accessibility", {}))
        save_profiles(self.accessibility, ACCESSIBILITY_PATH)
        self.staff_settings["node_display_names"] = dict(snapshot.get("node_display_names", {}))
        self.staff_settings["last_preset_name"] = str(snapshot.get("last_preset_name", ""))
        selected_pack = str(snapshot.get("selected_pack", "")).strip()
        if selected_pack:
            self.select_pack(selected_pack)
        self.save_staff_settings()
        return snapshot

    def display_name_for(self, node_id: str, pack_node_meta: dict[str, Any] | None = None) -> str:
        custom_names = self.staff_settings.get("node_display_names", {})
        if isinstance(custom_names, dict):
            chosen = custom_names.get(node_id)
            if isinstance(chosen, str) and chosen.strip():
                return chosen.strip()
        label = ""
        if isinstance(pack_node_meta, dict):
            raw_label = pack_node_meta.get("label")
            if isinstance(raw_label, str):
                label = raw_label.strip()
        return default_display_name(node_id, label)

    def display_names(self) -> dict[str, str]:
        nodes = self.current_pack.nodes if self.current_pack else {}
        return {
            node_id: self.display_name_for(node_id, meta)
            for node_id, meta in nodes.items()
        }

    def museum_staff_mode(self) -> bool:
        return bool(self.staff_settings.get("museum_staff_mode", True))

    def setup_complete(self) -> bool:
        return bool(self.staff_settings.get("setup_complete", False))

    def _today_key(self) -> str:
        return date.today().isoformat()

    def _daily_start_state(self) -> dict[str, Any]:
        raw = self.staff_settings.get("daily_start", {})
        if not isinstance(raw, dict):
            raw = {}
        node_tests = raw.get("node_tests")
        if not isinstance(node_tests, dict):
            node_tests = {}
        state = {
            "last_preset_date": str(raw.get("last_preset_date", "")),
            "last_preset_label": str(raw.get("last_preset_label", "")),
            "node_tests": node_tests,
        }
        self.staff_settings["daily_start"] = state
        return state

    def note_operation_preset_applied(self, label: str) -> None:
        state = self._daily_start_state()
        state["last_preset_date"] = self._today_key()
        state["last_preset_label"] = label
        self.save_staff_settings()

    def note_node_test_success(self, node_id: str, action: str) -> None:
        state = self._daily_start_state()
        node_tests = state.setdefault("node_tests", {})
        existing = node_tests.get(node_id)
        if not isinstance(existing, dict):
            existing = {}
        actions = existing.get("actions", [])
        if not isinstance(actions, list):
            actions = []
        if action not in actions:
            actions.append(action)
        node_tests[node_id] = {
            "date": self._today_key(),
            "actions": actions,
        }
        self.save_staff_settings()

    def daily_start_progress(self) -> dict[str, Any]:
        today = self._today_key()
        state = self._daily_start_state()
        node_tests = state.get("node_tests", {})
        if not isinstance(node_tests, dict):
            node_tests = {}
        tested_today: dict[str, list[str]] = {}
        for node_id, record in node_tests.items():
            if not isinstance(record, dict):
                continue
            if str(record.get("date", "")) != today:
                continue
            actions = record.get("actions", [])
            if not isinstance(actions, list):
                actions = []
            tested_today[str(node_id)] = [str(action) for action in actions]

        nodes = self.current_pack.nodes if self.current_pack else {}
        node_statuses = {
            node_id: {
                "tested_today": node_id in tested_today,
                "actions": tested_today.get(node_id, []),
            }
            for node_id in nodes
        }
        return {
            "readiness_complete": not any(
                problem["severity"] == "red" for problem in self.system_problems()
            ),
            "preset_complete": str(state.get("last_preset_date", "")) == today,
            "preset_label": str(state.get("last_preset_label", "")),
            "nodes_complete": bool(node_statuses)
            and all(status["tested_today"] for status in node_statuses.values()),
            "node_statuses": node_statuses,
        }

    def setup_progress(self) -> dict[str, Any]:
        nodes = self.current_pack.nodes if self.current_pack else {}
        named_nodes = len(self.display_names()) if nodes else 0
        return {
            "has_pack": self.current_pack is not None,
            "setup_complete": self.setup_complete(),
            "named_nodes": named_nodes,
            "total_nodes": len(nodes),
        }

    def has_story_packs(self) -> bool:
        return bool(self.content_manager.list_packs())

    def discovered_nodes(self) -> list[str]:
        return sorted(self.health_snapshot().keys())

    def expected_nodes(self) -> list[str]:
        if self.current_pack is None:
            return []
        return sorted(self.current_pack.nodes.keys())

    def missing_expected_nodes(self) -> list[str]:
        expected = set(self.expected_nodes())
        discovered = set(self.discovered_nodes())
        return sorted(expected - discovered)

    def unexpected_nodes(self) -> list[str]:
        expected = set(self.expected_nodes())
        discovered = set(self.discovered_nodes())
        if not expected:
            return self.discovered_nodes()
        return sorted(discovered - expected)

    def commissioning_status(self) -> dict[str, Any]:
        packs = self.content_manager.list_packs()
        discovered = self.discovered_nodes()
        expected = self.expected_nodes()
        missing = self.missing_expected_nodes()
        unexpected = self.unexpected_nodes()
        return {
            "dashboard_ready": True,
            "broker_host": self.config.broker_host,
            "broker_port": self.config.broker_port,
            "has_story_packs": bool(packs),
            "story_pack_count": len(packs),
            "active_pack_name": self.current_pack.name if self.current_pack else "",
            "discovered_nodes": discovered,
            "discovered_count": len(discovered),
            "expected_nodes": expected,
            "expected_count": len(expected),
            "missing_expected_nodes": missing,
            "unexpected_nodes": unexpected,
            "setup_complete": self.setup_complete(),
            "ready_for_opening": self.setup_complete()
            and self.current_pack is not None
            and (not expected or not missing),
        }

    def commissioning_steps(self) -> list[dict[str, str]]:
        status = self.commissioning_status()
        steps: list[dict[str, str]] = [
            {
                "title": "Confirm the hub is reachable",
                "state": "complete",
                "detail": (
                    "You are already in the dashboard. The hub is running at "
                    f"{self.config.dashboard_host}:{self.config.dashboard_port}."
                ),
                "action_label": "",
                "action_href": "",
            }
        ]
        if status["has_story_packs"]:
            steps.append(
                {
                    "title": "Load at least one story pack",
                    "state": "complete",
                    "detail": f"{status['story_pack_count']} story pack(s) are available.",
                    "action_label": "Open Change Story",
                    "action_href": url_for("content"),
                }
            )
        else:
            steps.append(
                {
                    "title": "Load at least one story pack",
                    "state": "pending",
                    "detail": (
                        "No story packs are available yet. Copy a pack into "
                        "content-packs/ on the hub, then return here."
                    ),
                    "action_label": "Open Change Story",
                    "action_href": url_for("content"),
                }
            )

        discovered = cast(list[str], status["discovered_nodes"])
        if discovered:
            steps.append(
                {
                    "title": "Confirm the Raspberry Pi nodes are checking in",
                    "state": "complete",
                    "detail": (
                        f"{status['discovered_count']} node(s) have checked in: "
                        + ", ".join(discovered)
                    ),
                    "action_label": "Open Object Status",
                    "action_href": url_for("nodes"),
                }
            )
        else:
            steps.append(
                {
                    "title": "Confirm the Raspberry Pi nodes are checking in",
                    "state": "pending",
                    "detail": (
                        "No nodes have checked in yet. Power on the nodes, confirm they "
                        "share the hub network, and check broker_host in node_config.yaml."
                    ),
                    "action_label": "Open Object Status",
                    "action_href": url_for("nodes"),
                }
            )

        if self.current_pack is None:
            steps.append(
                {
                    "title": "Choose the story and configure the exhibit",
                    "state": "pending",
                    "detail": "No active story is selected yet.",
                    "action_label": "Open Set Up Exhibit",
                    "action_href": url_for("setup"),
                }
            )
        elif status["missing_expected_nodes"]:
            missing_labels = [
                self.display_name_for(node_id, self.current_pack.nodes.get(node_id, {}))
                for node_id in cast(list[str], status["missing_expected_nodes"])
            ]
            steps.append(
                {
                    "title": "Match the active story to the physical objects",
                    "state": "pending",
                    "detail": (
                        "The active story expects these object nodes: "
                        + ", ".join(missing_labels)
                        + "."
                    ),
                    "action_label": "Open Object Status",
                    "action_href": url_for("nodes"),
                }
            )
        elif self.setup_complete():
            steps.append(
                {
                    "title": "Finish exhibit setup",
                    "state": "complete",
                    "detail": (
                        "The exhibit has been commissioned and can move into "
                        "daily opening checks."
                    ),
                    "action_label": "Open Exhibit",
                    "action_href": url_for("daily_start"),
                }
            )
        else:
            steps.append(
                {
                    "title": "Finish exhibit setup",
                    "state": "pending",
                    "detail": (
                        "Now that the hub, nodes, and story are visible, finish naming "
                        "objects and run the first hardware checks."
                    ),
                    "action_label": "Open Set Up Exhibit",
                    "action_href": url_for("setup"),
                }
            )
        return steps

    def operation_presets(self) -> dict[str, dict[str, Any]]:
        return {
            "school_group": {
                "label": "School Group",
                "preset_name": "hard_of_hearing",
                "global": {"mobility_buffer_ms": 650},
            },
            "quiet_morning": {
                "label": "Quiet Morning",
                "global": {"quiet_hours": ["08:00-12:00"], "sensory_friendly": True},
            },
            "busy_gallery": {
                "label": "Busy Gallery",
                "global": {"captions": True, "mobility_buffer_ms": 500},
            },
            "sensory_friendly_hour": {
                "label": "Sensory-Friendly Hour",
                "preset_name": "sensory_friendly",
                "global": {"quiet_hours": ["13:00-14:00"]},
            },
        }

    def apply_operation_preset(self, preset_key: str) -> dict[str, Any]:
        presets = self.operation_presets()
        if preset_key not in presets:
            raise KeyError(f"Unknown operation preset '{preset_key}'.")
        preset = presets[preset_key]
        self.snapshot_for_undo()
        preset_name = str(preset.get("preset_name", "")).strip()
        if preset_name:
            apply_preset(self.accessibility, preset_name)
            self.staff_settings["last_preset_name"] = preset_name
        global_settings = preset.get("global", {})
        if isinstance(global_settings, dict):
            self.accessibility.setdefault("global", {}).update(global_settings)
        save_profiles(self.accessibility, ACCESSIBILITY_PATH)
        self.reload_accessibility()
        self.save_staff_settings()
        return preset

    def pack_validation(self, pack_name: str) -> PackValidationReport:
        return self.content_manager.validate_pack(pack_name)

    def safe_pack_validation(self, pack_name: str) -> PackValidationReport:
        try:
            return self.pack_validation(pack_name)
        except (FileNotFoundError, ValueError) as exc:
            return PackValidationReport(
                pack_name=pack_name,
                missing_assets=[str(exc)],
                missing_nodes=[],
                languages_by_node={},
                valid=False,
            )

    def pack_validation_map(self) -> dict[str, PackValidationReport]:
        return {
            pack_name: self.safe_pack_validation(pack_name)
            for pack_name in self.content_manager.list_packs()
        }

    def current_pack_validation(self) -> PackValidationReport | None:
        if self.current_pack is None:
            return None
        return self.safe_pack_validation(self.current_pack.name)

    def known_node_ids(self) -> set[str]:
        known = set(self.health_snapshot())
        if self.current_pack is not None:
            known.update(self.current_pack.nodes)
        return known

    def pack_languages(self, pack: ContentPack | None = None) -> list[str]:
        chosen_pack = pack or self.current_pack
        if chosen_pack is None:
            return []
        return sorted({language for _node_id, language in chosen_pack.media})

    def pack_readiness_summary(self, report: PackValidationReport) -> str:
        if report.valid:
            return "Ready to activate"
        issue_count = len(report.missing_assets) + len(report.missing_nodes)
        suffix = "issue" if issue_count == 1 else "issues"
        return f"Needs attention ({issue_count} {suffix})"

    def object_status(self, node_id: str, meta: dict[str, Any]) -> dict[str, str]:
        health = self.health_snapshot().get(node_id, {})
        age = health.get("age")
        config_sync = str(health.get("config_sync", "unknown"))
        if isinstance(age, (int, float)) and age > 120:
            return {
                "label": "Needs power or reconnect",
                "detail": "This object has not checked in recently.",
            }
        if config_sync == "stale":
            return {
                "label": "Needs settings update",
                "detail": "The hub is waiting for this object to catch up.",
            }
        if isinstance(age, (int, float)):
            return {
                "label": "Ready",
                "detail": f"Checked in {int(age)} second(s) ago.",
            }
        return {
            "label": "Waiting for first check-in",
            "detail": "This object has not reported to the hub yet.",
        }

    def recommended_task(self) -> dict[str, str]:
        problems = self.system_problems()
        if not self.has_story_packs() or not self.discovered_nodes():
            return {
                "title": "Connect exhibit devices",
                "detail": (
                    "Use this page to confirm the hub, nodes, and story packs are "
                    "all visible before exhibit setup."
                ),
                "href": url_for("commission"),
                "label": "Open Hardware Status",
            }
        if not self.setup_complete():
            return {
                "title": "Finish exhibit setup",
                "detail": "Use Set Up Exhibit before relying on the opening checklist.",
                "href": url_for("setup"),
                "label": "Open Set Up Exhibit",
            }
        if any(problem["severity"] == "red" for problem in problems):
            return {
                "title": "Resolve active issues",
                "detail": (
                    "The exhibit is not ready to open until the critical "
                    "problems are cleared."
                ),
                "href": url_for("problems_page"),
                "label": "Fix an Issue",
            }
        return {
            "title": "Run the opening check",
            "detail": (
                "Open Exhibit is the main page for confirming today’s preset, "
                "object tests, and readiness."
            ),
            "href": url_for("daily_start"),
            "label": "Open Exhibit",
        }

    def system_problems(self) -> list[dict[str, str]]:
        problems: list[dict[str, str]] = []
        pack_validation = self.current_pack_validation()
        if pack_validation is None:
            problems.append(
                {
                    "severity": "red",
                    "title": "No content pack is active.",
                    "action": "Open Change Story and activate a pack before visitors arrive.",
                    "link_href": url_for("content"),
                    "link_label": "Open Change Story",
                }
            )
        elif not pack_validation.valid:
            problems.append(
                {
                    "severity": "red",
                    "title": f"Pack '{pack_validation.pack_name}' is missing required files.",
                    "action": (
                        "Open Change Story, review the checklist, and replace missing "
                        "audio or transcript files."
                    ),
                    "link_href": url_for("content"),
                    "link_label": "Open Change Story",
                }
            )

        if not self.has_story_packs():
            problems.append(
                {
                    "severity": "red",
                    "title": "No story packs are available on the hub.",
                    "action": (
                        "Copy at least one pack into content-packs/ on the hub, then "
                        "activate it from Change Story."
                    ),
                    "link_href": url_for("commission"),
                    "link_label": "Open Hardware Status",
                }
            )

        discovered_nodes = self.discovered_nodes()
        if not discovered_nodes:
            problems.append(
                {
                    "severity": "yellow",
                    "title": "No Raspberry Pi nodes have checked in yet.",
                    "action": (
                        "Power on the nodes, confirm they share the hub network, and "
                        "check broker_host in node_config.yaml."
                    ),
                    "link_href": url_for("commission"),
                    "link_label": "Open Hardware Status",
                }
            )

        for node_id, meta in (self.current_pack.nodes if self.current_pack else {}).items():
            health = self.health_snapshot().get(node_id, {})
            age = health.get("age")
            config_sync = str(health.get("config_sync", "unknown"))
            display_name = self.display_name_for(node_id, meta)
            if isinstance(age, (int, float)) and age > 120:
                problems.append(
                    {
                        "severity": "red",
                        "title": f"{display_name} has not checked in for {int(age)} seconds.",
                        "action": (
                            "Check power and network, then use Reconnect from Daily "
                            "Start or Object Status."
                        ),
                        "node_id": node_id,
                        "node_action": "reconnect",
                        "link_href": url_for("daily_start"),
                        "link_label": "Open Exhibit",
                    }
                )
            elif config_sync == "stale":
                problems.append(
                    {
                        "severity": "yellow",
                        "title": f"{display_name} is using older settings than the hub.",
                        "action": (
                            "The hub will retry automatically. If it stays stale, "
                            "run a node test or reconnect it."
                        ),
                        "node_id": node_id,
                        "node_action": "reconnect",
                        "link_href": url_for("nodes"),
                        "link_label": "Open Object Status",
                    }
                )

        if not self.setup_complete():
            problems.append(
                {
                    "severity": "yellow",
                    "title": "First-run setup has not been completed.",
                    "action": (
                        "Run Set Up Exhibit once to name nodes and confirm the "
                        "exhibit is ready."
                    ),
                    "link_href": url_for("setup"),
                    "link_label": "Open Set Up Exhibit",
                }
            )
        return problems

    def status_banner(self) -> dict[str, str]:
        problems = self.system_problems()
        red_count = sum(1 for problem in problems if problem["severity"] == "red")
        yellow_count = sum(1 for problem in problems if problem["severity"] == "yellow")
        if red_count:
            return {
                "tone": "error",
                "headline": "Action needed before opening.",
                "detail": f"{red_count} critical issue(s) need attention.",
            }
        if yellow_count:
            return {
                "tone": "warning",
                "headline": "Review needed before opening.",
                "detail": f"{yellow_count} warning(s) should be checked before visitors arrive.",
            }
        return {
            "tone": "success",
            "headline": "System ready.",
            "detail": "All visible nodes are healthy and the exhibit is ready to open.",
        }

    def apply_node_names(self, names: dict[str, str]) -> None:
        cleaned = {
            node_id: name.strip()
            for node_id, name in names.items()
            if isinstance(name, str) and name.strip()
        }
        self.staff_settings["node_display_names"] = cleaned
        self.staff_settings["setup_complete"] = True
        self.save_staff_settings()

    def set_staff_mode(self, enabled: bool) -> None:
        self.staff_settings["museum_staff_mode"] = bool(enabled)
        self.save_staff_settings()

    def push_config_to_node(self, node_id: str, payload: dict[str, Any]) -> bool:
        controller = self.hub_controller
        if controller is None:
            raise ConfigPushError("Hub controller unavailable.", status_code=503)
        try:
            return bool(controller.push_node_config(node_id, payload))
        except ConfigPushError:
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.getLogger(__name__).warning("Failed to push config to %s: %s", node_id, exc)
            raise ConfigPushError(
                f"Unexpected error while pushing configuration to {node_id}: {exc}",
                status_code=502,
            ) from exc

    def push_accessibility_configs(self) -> dict[str, bool]:
        if not self.current_pack:
            return {}
        payloads = derive_runtime_payloads(self.accessibility, self.current_pack.nodes)
        pack = self.current_pack
        for node_id, node_meta in pack.nodes.items():
            default_lang = str(node_meta.get("default_language") or self.config.default_language)
            asset = pack.media.get((node_id, default_lang))
            if asset is None:
                asset = next(
                    (
                        media_asset
                        for (media_node_id, _), media_asset in pack.media.items()
                        if media_node_id == node_id
                    ),
                    None,
                )
            if asset is None:
                continue
            payloads.setdefault(node_id, {}).setdefault("audio", {})
            payloads[node_id]["audio"]["fragment_file"] = str(asset.audio_path)
        controller = self.hub_controller
        if controller is None:
            return {}
        try:
            return dict(controller.push_node_configs(payloads))
        except Exception:
            results: dict[str, bool] = {}
            for node_id, payload in payloads.items():
                try:
                    results[node_id] = self.push_config_to_node(node_id, payload)
                except ConfigPushError:
                    results[node_id] = False
            return results

    def state_snapshot(self) -> dict[str, Any]:
        controller = self.hub_controller
        if controller is None:
            return {}
        try:
            return dict(controller.get_state_snapshot())
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.getLogger(__name__).warning(
                "Failed to pull state snapshot from hub controller: %s", exc
            )
            return {}

    def health_snapshot(self) -> dict[str, dict[str, Any]]:
        controller = self.hub_controller
        if controller is None:
            return {}
        try:
            return dict(controller.get_health_snapshot())
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.getLogger(__name__).warning(
                "Failed to pull health snapshot from hub controller: %s", exc
            )
            return {}

    def reset_state(self) -> dict[str, Any]:
        controller = self.hub_controller
        if controller is None:
            return {}
        try:
            controller.reset_state()
            return self.state_snapshot()
        except Exception as exc:  # pragma: no cover - defensive logging
            logging.getLogger(__name__).warning(
                "Failed to reset hub controller narrative state: %s", exc
            )
            return {}


def create_app(config: HubConfig | None = None, hub_controller: Any | None = None) -> Flask:
    """Create and configure the Flask application."""
    hub_config = config or load_config()

    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )
    css_asset = Path(app.static_folder or "static") / "css" / "style.css"
    js_asset = Path(app.static_folder or "static") / "js" / "app.js"

    try:
        accessibility = load_profiles(ACCESSIBILITY_PATH)
    except ValueError as exc:
        app.logger.warning("Falling back to default accessibility profiles: %s", exc)
        accessibility = {"global": {}, "presets": {}, "per_node_overrides": {}}
    staff_settings = load_staff_settings(STAFF_SETTINGS_PATH)
    narrative_state = NarrativeState(
        required_fragments=hub_config.narrative.required_fragments_to_unlock
    )
    controller = hub_controller or InProcessHubController(narrative_state)

    context = DashboardContext(
        config=hub_config,
        content_manager=ContentManager(),
        accessibility=accessibility,
        staff_settings=staff_settings,
        hub_controller=controller,
    )

    available_packs = context.content_manager.list_packs()
    preferred_pack = str(context.staff_settings.get("selected_pack", "")).strip()
    initial_pack = preferred_pack if preferred_pack in available_packs else ""
    if not initial_pack and available_packs:
        initial_pack = available_packs[0]
    if initial_pack:
        try:
            pack = context.content_manager.load_pack(initial_pack)
            context.current_pack = pack
            context.staff_settings["selected_pack"] = initial_pack
            context.save_staff_settings()
        except Exception as exc:  # pragma: no cover - defensive
            app.logger.warning("Failed to load initial pack '%s': %s", initial_pack, exc)

    app.config["DASHBOARD_CONTEXT"] = context
    app.config["HUB_CONTROLLER"] = controller

    credentials: tuple[str, str] | None = None
    if hub_config.security.require_basic_auth:
        username = os.getenv(hub_config.security.admin_user_env)
        password = os.getenv(hub_config.security.admin_pass_env)
        if not username or not password:
            raise RuntimeError(
                "Basic authentication is required but administrator credentials are not configured."
            )
        credentials = (username, password)
    app.config["ADMIN_CREDENTIALS"] = credentials

    def get_context() -> DashboardContext:
        return cast(DashboardContext, app.config["DASHBOARD_CONTEXT"])

    F = TypeVar("F", bound=Callable[..., Any])

    def get_expected_credentials() -> tuple[str, str] | None:
        raw = app.config.get("ADMIN_CREDENTIALS")
        if not isinstance(raw, tuple) or len(raw) != 2:
            return None
        username, password = raw
        if not isinstance(username, str) or not isinstance(password, str):
            return None
        return (username, password)

    def require_auth(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _is_same_origin_request(
                request
            ):
                abort(403, description="Cross-origin requests are not allowed for state changes.")
            expected = get_expected_credentials()
            if not expected:
                return func(*args, **kwargs)
            auth = request.authorization
            if not auth:
                return _auth_required_response()
            provided_user = auth.username or ""
            provided_pass = auth.password or ""
            if not (
                hmac.compare_digest(provided_user, expected[0])
                and hmac.compare_digest(provided_pass, expected[1])
            ):
                return _auth_required_response()
            return func(*args, **kwargs)

        return cast(F, wrapper)

    @app.context_processor
    def inject_globals() -> dict[str, Any]:
        ctx = get_context()
        accessibility_global = ctx.accessibility.get("global", {})
        global_view = accessibility_global if isinstance(accessibility_global, dict) else {}
        nav_sections = [
            (
                "index",
                "Overview",
                url_for("index"),
                "overview",
                "layout-dashboard",
            ),
            (
                "commission",
                "Hardware Status",
                url_for("commission"),
                "primary",
                "radio",
            ),
            ("daily_start", "Open Exhibit", url_for("daily_start"), "primary", "play"),
            ("setup", "Set Up Exhibit", url_for("setup"), "primary", "wand"),
            (
                "problems_page",
                "Fix an Issue",
                url_for("problems_page"),
                "primary",
                "triangle-alert",
            ),
            ("content", "Change Story", url_for("content"), "primary", "book-open"),
            (
                "accessibility_page",
                "Support Visitors",
                url_for("accessibility_page"),
                "primary",
                "accessibility",
            ),
            ("labels_page", "Print Cards", url_for("labels_page"), "secondary", "printer"),
            ("nodes", "Object Status", url_for("nodes"), "secondary", "radio"),
            ("analytics", "Reports", url_for("analytics"), "secondary", "chart-column"),
        ]
        return {
            "hub_config": ctx.config,
            "accessibility_profiles": ctx.accessibility,
            "accessibility_global": global_view,
            "active_pack": ctx.current_pack,
            "node_display_names": ctx.display_names(),
            "museum_staff_mode": ctx.museum_staff_mode(),
            "status_banner": ctx.status_banner(),
            "active_endpoint": request.endpoint or "",
            "nav_sections": nav_sections,
            "style_asset_version": int(css_asset.stat().st_mtime) if css_asset.exists() else 0,
            "script_asset_version": int(js_asset.stat().st_mtime) if js_asset.exists() else 0,
        }

    @app.after_request
    def apply_security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/api/") or request.path in {"/", "/nodes", "/analytics"}:
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    # ------------------------------------------------------------------ Routes

    @app.route("/health")
    def health() -> Response:
        """Return a simple JSON response indicating the app is healthy."""
        return jsonify({"ok": True})

    @app.route("/")
    @require_auth
    def index() -> str:
        ctx = get_context()
        state = ctx.state_snapshot()
        return render_template(
            "index.html",
            state=state,
            active_pack=ctx.current_pack,
            available_packs=ctx.content_manager.list_packs(),
            problems=ctx.system_problems(),
            status_banner=ctx.status_banner(),
            recommended_task=ctx.recommended_task(),
            commissioning_status=ctx.commissioning_status(),
            title="Overview",
        )

    @app.route("/commission")
    @require_auth
    def commission() -> str:
        ctx = get_context()
        return render_template(
            "commission.html",
            commissioning_status=ctx.commissioning_status(),
            commissioning_steps=ctx.commissioning_steps(),
            title="Hardware Status",
        )

    @app.route("/nodes")
    @require_auth
    def nodes() -> str:
        ctx = get_context()
        pack = ctx.current_pack
        nodes = pack.nodes if pack else {}
        health = ctx.health_snapshot()
        heartbeat_ages = {
            node_id: _format_age_seconds(health_row.get("age"))
            for node_id, health_row in health.items()
        }
        assignments: dict[str, MediaAsset] = {}
        object_statuses = {
            node_id: ctx.object_status(node_id, meta) for node_id, meta in nodes.items()
        }
        if pack:
            for (node_id, lang), asset in pack.media.items():
                default_lang = nodes.get(node_id, {}).get("default_language")
                if default_lang == lang:
                    assignments[node_id] = asset
        return render_template(
            "nodes.html",
            nodes=nodes,
            health=health,
            heartbeat_ages=heartbeat_ages,
            assignments=assignments,
            object_statuses=object_statuses,
            commissioning_status=ctx.commissioning_status(),
            display_names=ctx.display_names(),
            museum_staff_mode=ctx.museum_staff_mode(),
            title="Object Status",
        )

    @app.route("/accessibility")
    @require_auth
    def accessibility_page() -> str:
        ctx = get_context()
        profiles = ctx.accessibility
        return render_template(
            "accessibility.html",
            profiles=profiles,
            nodes=ctx.current_pack.nodes if ctx.current_pack else {},
            display_names=ctx.display_names(),
            operation_presets=ctx.operation_presets(),
            title="Support Visitors",
        )

    @app.route("/calibration")
    @require_auth
    def calibration() -> str:
        ctx = get_context()
        pack = ctx.current_pack
        nodes = pack.nodes if pack else {}
        return render_template("calibration.html", nodes=nodes)

    @app.route("/content")
    @require_auth
    def content() -> str:
        ctx = get_context()
        pack = ctx.current_pack
        all_packs = ctx.content_manager.list_packs()
        return render_template(
            "content.html",
            active_pack=pack,
            pack_names=all_packs,
            validation_map=ctx.pack_validation_map(),
            pack_languages=ctx.pack_languages(pack),
            commissioning_status=ctx.commissioning_status(),
            title="Change Story",
        )

    @app.route("/analytics")
    @require_auth
    def analytics() -> str:
        ctx = get_context()
        state = ctx.state_snapshot()
        return render_template(
            "analytics.html",
            state=state,
            health=ctx.health_snapshot(),
            title="Analytics",
        )

    @app.route("/setup")
    @require_auth
    def setup() -> str:
        ctx = get_context()
        nodes = ctx.current_pack.nodes if ctx.current_pack else {}
        return render_template(
            "setup.html",
            nodes=nodes,
            display_names=ctx.display_names(),
            pack_names=ctx.content_manager.list_packs(),
            validation_map=ctx.pack_validation_map(),
            operation_presets=ctx.operation_presets(),
            commissioning_status=ctx.commissioning_status(),
            setup_progress=ctx.setup_progress(),
            title="Set Up Exhibit",
        )

    @app.route("/daily-start")
    @require_auth
    def daily_start() -> str:
        ctx = get_context()
        return render_template(
            "daily_start.html",
            nodes=ctx.current_pack.nodes if ctx.current_pack else {},
            display_names=ctx.display_names(),
            status_banner=ctx.status_banner(),
            problems=ctx.system_problems(),
            validation=ctx.current_pack_validation(),
            operation_presets=ctx.operation_presets(),
            daily_progress=ctx.daily_start_progress(),
            title="Open Exhibit",
        )

    @app.route("/problems")
    @require_auth
    def problems_page() -> str:
        ctx = get_context()
        return render_template(
            "problems.html",
            problems=ctx.system_problems(),
            display_names=ctx.display_names(),
            title="Fix an Issue",
        )

    @app.route("/labels")
    @require_auth
    def labels_page() -> str:
        ctx = get_context()
        pack = ctx.current_pack
        labels: list[dict[str, str]] = []
        if pack:
            for node_id, meta in pack.nodes.items():
                language = meta.get("default_language", ctx.config.default_language)
                transcript = ctx.content_manager.get_transcript_url(node_id, language)
                labels.append(
                    {
                        "node_id": node_id,
                        "display_name": ctx.display_name_for(node_id, meta),
                        "language": language,
                        "transcript": transcript or "Unavailable",
                        "pack_name": pack.name,
                        "ready_status": (
                            "Ready to print" if transcript else "Transcript unavailable"
                        ),
                    }
                )
        return render_template(
            "labels.html",
            labels=labels,
            active_pack=pack,
            title="Print Visitor Cards",
        )

    @app.route("/api/health")
    @require_auth
    def api_health() -> Response:
        ctx = get_context()
        return jsonify({"nodes": ctx.health_snapshot()})

    @app.route("/api/state")
    @require_auth
    def api_state() -> Response:
        ctx = get_context()
        return jsonify(ctx.state_snapshot())

    @app.route("/api/reset-state", methods=["POST"])
    @require_auth
    def api_reset_state() -> Response:
        ctx = get_context()
        _require_json(request)
        snapshot = ctx.reset_state()
        return jsonify(
            {
                "ok": True,
                "state": snapshot,
                "message": "Exhibit progress has been reset for a fresh visitor run.",
            }
        )

    @app.route("/api/push-config", methods=["POST"])
    @require_auth
    def api_push_config() -> Response:
        ctx = get_context()
        data = _require_json(request)
        node_id = _require_known_node_id(ctx, _require_field(data, "node_id"))
        payload = data.get("payload")
        if not isinstance(payload, dict):
            abort(400, description="payload must be an object")
        app.logger.info("Configuration push requested for %s: %s", node_id, payload)
        try:
            acknowledged = ctx.push_config_to_node(node_id, payload)
        except ConfigPushError as exc:
            app.logger.warning("Configuration push to %s failed: %s", node_id, exc)
            abort(getattr(exc, "status_code", 409), description=str(exc))
        return jsonify({"ok": acknowledged, "acknowledged": acknowledged, "node_id": node_id})

    @app.route("/api/apply-preset", methods=["POST"])
    @app.route("/api/apply_preset", methods=["POST"])
    @require_auth
    def api_apply_preset() -> Response:
        ctx = get_context()
        data = _require_json(request)
        preset_name = data.get("preset_name")
        profiles = ctx.accessibility

        if preset_name:
            try:
                ctx.snapshot_for_undo()
                apply_preset(profiles, preset_name)
                ctx.staff_settings["last_preset_name"] = str(preset_name)
            except KeyError as exc:
                abort(400, description=str(exc))
        elif "global" in data:
            global_settings = data["global"]
            if not isinstance(global_settings, dict):
                abort(400, description="global must be an object")
            ctx.snapshot_for_undo()
            profiles.setdefault("global", {}).update(global_settings)
        else:
            abort(400, description="Provide preset_name or global settings to apply.")

        try:
            ensure_quiet_hours_valid(profiles.setdefault("global", {}).get("quiet_hours"))
        except ValueError as exc:
            abort(400, description=str(exc))
        save_profiles(profiles, ACCESSIBILITY_PATH)
        ctx.reload_accessibility()
        push_results = ctx.push_accessibility_configs()
        return jsonify(
            {
                "ok": True,
                "global": ctx.accessibility.get("global", {}),
                "push": push_results,
                "message": "Accessibility settings were saved and sent to active nodes.",
            }
        )

    @app.route("/api/accessibility/override", methods=["POST"])
    @require_auth
    def api_accessibility_override() -> Response:
        ctx = get_context()
        data = _require_json(request)
        node_id = _require_known_node_id(ctx, _require_field(data, "node_id"))
        overrides = data.get("overrides")
        if not isinstance(overrides, dict):
            abort(400, description="overrides must be an object")

        ctx.snapshot_for_undo()
        set_per_node_override(ctx.accessibility, node_id, overrides)
        save_profiles(ctx.accessibility, ACCESSIBILITY_PATH)
        ctx.reload_accessibility()
        ctx.save_staff_settings()
        push_results = ctx.push_accessibility_configs()
        per_node = ctx.accessibility.get("per_node_overrides", {}).get(node_id, {})
        display_name = ctx.display_name_for(
            node_id, ctx.current_pack.nodes.get(node_id, {}) if ctx.current_pack else None
        )
        return jsonify(
            {
                "ok": True,
                "overrides": per_node,
                "push": push_results,
                "message": f"{display_name} accessibility settings were updated.",
            }
        )

    @app.route("/api/select-pack", methods=["POST"])
    @require_auth
    def api_select_pack() -> Response | tuple[Response, int]:
        ctx = get_context()
        data = _require_json(request)
        pack_name = _require_field(data, "pack_name")
        report = _require_pack_validation(ctx, pack_name)
        if not report.valid and not bool(data.get("confirm_invalid", False)):
            return (
                jsonify(
                    {
                        "ok": False,
                        "message": (
                            "Pack validation failed. Review the checklist before "
                            "activating."
                        ),
                        "missing_assets": report.missing_assets,
                        "missing_nodes": report.missing_nodes,
                    }
                ),
                409,
            )
        try:
            ctx.snapshot_for_undo()
            pack = ctx.select_pack(pack_name)
        except FileNotFoundError:
            abort(404, description=f"Content pack '{pack_name}' not found.")
        except ValueError as exc:
            abort(400, description=str(exc))
        push_results = ctx.push_accessibility_configs()
        return jsonify(
            {
                "ok": True,
                "pack": pack.name,
                "push": push_results,
                "message": f"Content pack '{pack.name}' is now active.",
            }
        )

    @app.route("/api/undo-last-change", methods=["POST"])
    @require_auth
    def api_undo_last_change() -> Response | tuple[Response, int]:
        ctx = get_context()
        snapshot = ctx.restore_undo()
        if snapshot is None:
            return jsonify({"ok": False, "message": "Nothing to undo."}), 404
        push_results = ctx.push_accessibility_configs()
        return jsonify(
            {
                "ok": True,
                "push": push_results,
                "snapshot": snapshot,
                "message": "The most recent dashboard setting change was undone.",
            }
        )

    @app.route("/api/staff-mode", methods=["POST"])
    @require_auth
    def api_staff_mode() -> Response:
        ctx = get_context()
        data = _require_json(request)
        enabled = bool(data.get("enabled", True))
        ctx.set_staff_mode(enabled)
        detail = "Advanced controls are hidden." if enabled else "Advanced controls are visible."
        return jsonify(
            {
                "ok": True,
                "enabled": enabled,
                "message": f"Simple View saved. {detail}",
            }
        )

    @app.route("/api/node-display-names", methods=["POST"])
    @require_auth
    def api_node_display_names() -> Response:
        ctx = get_context()
        data = _require_json(request)
        names = data.get("names")
        if not isinstance(names, dict):
            abort(400, description="names must be an object")
        ctx.snapshot_for_undo()
        ctx.apply_node_names({str(node_id): str(value) for node_id, value in names.items()})
        return jsonify(
            {
                "ok": True,
                "names": ctx.display_names(),
                "message": "Object names were saved for the current exhibit setup.",
            }
        )

    @app.route("/api/node-test", methods=["POST"])
    @require_auth
    def api_node_test() -> Response:
        ctx = get_context()
        data = _require_json(request)
        node_id = _require_known_node_id(ctx, _require_field(data, "node_id"))
        action = _require_node_test_action(data)
        payload = {"test": {"action": action}}
        try:
            acknowledged = ctx.push_config_to_node(node_id, payload)
        except ConfigPushError as exc:
            abort(getattr(exc, "status_code", 409), description=str(exc))
        if acknowledged:
            ctx.note_node_test_success(node_id, action)
        display_name = ctx.display_name_for(
            node_id, ctx.current_pack.nodes.get(node_id, {}) if ctx.current_pack else None
        )
        action_labels = {
            "sound": "Sound test sent",
            "light": "Light test sent",
            "sensor": "Sensor check sent",
            "reconnect": "Trying to reconnect",
        }
        return jsonify(
            {
                "ok": acknowledged,
                "node_id": node_id,
                "action": action,
                "message": f"{action_labels[action]} to {display_name}.",
            }
        )

    @app.route("/api/operation-preset", methods=["POST"])
    @require_auth
    def api_operation_preset() -> Response:
        ctx = get_context()
        data = _require_json(request)
        preset_key = _require_field(data, "preset_key")
        try:
            preset = ctx.apply_operation_preset(preset_key)
        except KeyError as exc:
            abort(404, description=str(exc))
        label = str(preset.get("label", preset_key))
        ctx.note_operation_preset_applied(label)
        push_results = ctx.push_accessibility_configs()
        return jsonify(
            {
                "ok": True,
                "preset": preset,
                "push": push_results,
                "message": f"Operational preset '{label}' was applied.",
            }
        )

    @app.route("/api/export-csv")
    @require_auth
    def api_export_csv() -> Response:
        ctx = get_context()
        logger = CsvEventLogger(ctx.config.logs_dir)
        latest = logger.latest_csv()
        logger.close()
        if latest is None or not latest.exists():
            abort(404, description="No analytics CSV available yet.")
        return send_file(latest, mimetype="text/csv", as_attachment=True, download_name=latest.name)

    @app.route("/api/analytics/summary")
    @require_auth
    def api_analytics_summary() -> Response | tuple[Response, int]:
        ctx = get_context()
        summary = summarize_events(ctx.config.logs_dir)
        if summary is None:
            return jsonify({"ok": False, "message": "No analytics available."}), 404
        return jsonify(
            {
                "ok": True,
                "by_node": summary.by_node,
                "heartbeat_by_node": summary.heartbeat_by_node,
                "narrative_unlocks": summary.narrative_unlocks,
                "total_triggers": summary.total_triggers,
                "completion_rate": summary.completion_rate,
                "mean_trigger_interval_seconds": summary.mean_trigger_interval_seconds,
                "recent_events": summary.recent_events,
            }
        )

    @app.route("/transcripts/<pack_name>/<path:filename>")
    def serve_transcript(pack_name: str, filename: str) -> WerkzeugResponse:
        # Validate pack_name contains no path separators
        if "/" in pack_name or "\\" in pack_name or ".." in pack_name:
            abort(404)
        if Path(filename).suffix.lower() != ".html":
            abort(404)
        base_dir = (Path("content-packs") / pack_name / "transcripts").resolve()
        target_path = (base_dir / filename).resolve()
        # Ensure resolved path is strictly within the base directory
        try:
            target_path.relative_to(base_dir)
        except ValueError:
            abort(404)
        if not target_path.is_file():
            abort(404)
        response = cast(WerkzeugResponse, send_file(target_path, mimetype="text/html"))
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; sandbox"
        )
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    @app.route("/logout")
    @require_auth
    def logout() -> WerkzeugResponse:
        response = redirect(url_for("index"))
        response.headers["WWW-Authenticate"] = 'Basic realm="EchoTrace"'
        return cast(WerkzeugResponse, response)

    return app


def _auth_required_response() -> WerkzeugResponse:
    response = Response(status=401)
    response.headers["WWW-Authenticate"] = 'Basic realm="EchoTrace"'
    return response


def _require_json(req: Request) -> dict[str, Any]:
    if not req.is_json:
        abort(400, description="Expected JSON body.")
    data = req.get_json()
    if not isinstance(data, dict):
        abort(400, description="JSON body must be an object.")
    return data


def _require_field(data: dict[str, Any], field_name: str) -> str:
    value: object = data.get(field_name)
    if not isinstance(value, str) or not value:
        abort(400, description=f"Field '{field_name}' is required.")
    return value


def _require_pack_validation(ctx: DashboardContext, pack_name: str) -> PackValidationReport:
    try:
        return ctx.pack_validation(pack_name)
    except FileNotFoundError:
        abort(404, description=f"Content pack '{pack_name}' not found.")
    except ValueError as exc:
        abort(400, description=str(exc))


def _require_node_test_action(data: dict[str, Any]) -> str:
    action = _require_field(data, "action").strip().lower()
    if action not in ALLOWED_NODE_TEST_ACTIONS:
        allowed = ", ".join(sorted(ALLOWED_NODE_TEST_ACTIONS))
        abort(400, description=f"Field 'action' must be one of: {allowed}.")
    return action


def _require_known_node_id(ctx: DashboardContext, node_id: str) -> str:
    known_node_ids = ctx.known_node_ids()
    if known_node_ids and node_id not in known_node_ids:
        abort(404, description=f"Node '{node_id}' is not known to the current exhibit.")
    return node_id


def _format_age_seconds(age: object) -> str:
    if isinstance(age, (int, float)):
        return f"{age:.1f} s"
    return "—"


def _is_same_origin_request(req: Request) -> bool:
    origin = req.headers.get("Origin")
    referer = req.headers.get("Referer")
    if not origin and not referer:
        return True

    expected = urlparse(req.host_url)
    candidate = origin or referer
    parsed = urlparse(candidate or "")
    if not parsed.scheme or not parsed.netloc:
        return False
    return parsed.scheme == expected.scheme and parsed.netloc == expected.netloc


if __name__ == "__main__":
    # Development server entry point - not for production use.
    # Production deployments should use run_hub.py with Waitress.
    os.environ.setdefault("ECHOTRACE_ADMIN_USER", "admin")
    os.environ.setdefault("ECHOTRACE_ADMIN_PASS", "changeme")  # noqa: S105
    development_app = create_app()
    development_app.run(host="127.0.0.1", port=8080, debug=True)
