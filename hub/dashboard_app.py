"""Flask dashboard application serving the EchoTrace hub UI."""

from __future__ import annotations

import functools
import hmac
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar, cast
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
from .content_manager import ContentManager, ContentPack, MediaAsset
from .hub_listener import ConfigPushError
from .event_logging import CsvEventLogger, summarize_events
from .narrative_state import NarrativeState


class HubControllerProtocol(Protocol):
    def push_node_config(self, node_id: str, payload: dict[str, Any]) -> bool:
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
    current_pack: ContentPack | None = None
    hub_controller: HubControllerProtocol | None = None

    def select_pack(self, pack_name: str) -> ContentPack:
        pack = self.content_manager.load_pack(pack_name)
        self.current_pack = pack
        return pack

    def reload_accessibility(self) -> None:
        self.accessibility = load_profiles(ACCESSIBILITY_PATH)

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
        results: dict[str, bool] = {}
        for node_id, payload in payloads.items():
            results[node_id] = self.push_config_to_node(node_id, payload)
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

    try:
        accessibility = load_profiles(ACCESSIBILITY_PATH)
    except ValueError as exc:
        app.logger.warning("Falling back to default accessibility profiles: %s", exc)
        accessibility = {"global": {}, "presets": {}, "per_node_overrides": {}}
    narrative_state = NarrativeState(
        required_fragments=hub_config.narrative.required_fragments_to_unlock
    )
    controller = hub_controller or InProcessHubController(narrative_state)

    context = DashboardContext(
        config=hub_config,
        content_manager=ContentManager(),
        accessibility=accessibility,
        hub_controller=controller,
    )

    available_packs = context.content_manager.list_packs()
    if available_packs:
        try:
            context.select_pack(available_packs[0])
        except Exception as exc:  # pragma: no cover - defensive
            app.logger.warning("Failed to load initial pack '%s': %s", available_packs[0], exc)

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

    route_return = (
        WerkzeugResponse
        | str
        | tuple[WerkzeugResponse, int]
        | tuple[WerkzeugResponse, int, dict[str, Any]]
    )
    F = TypeVar("F", bound=Callable[..., route_return])

    def require_auth(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> route_return:
            expected = cast(tuple[str, str] | None, app.config.get("ADMIN_CREDENTIALS"))
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
        return {
            "hub_config": ctx.config,
            "accessibility_profiles": ctx.accessibility,
            "accessibility_global": global_view,
            "active_pack": ctx.current_pack,
        }

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
        )

    @app.route("/nodes")
    @require_auth
    def nodes() -> str:
        ctx = get_context()
        pack = ctx.current_pack
        nodes = pack.nodes if pack else {}
        health = ctx.health_snapshot()
        assignments: dict[str, MediaAsset] = {}
        if pack:
            for (node_id, lang), asset in pack.media.items():
                default_lang = nodes.get(node_id, {}).get("default_language")
                if default_lang == lang:
                    assignments[node_id] = asset
        return render_template(
            "nodes.html",
            nodes=nodes,
            health=health,
            assignments=assignments,
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
        snapshot = ctx.reset_state()
        return jsonify({"ok": True, "state": snapshot})

    @app.route("/api/push-config", methods=["POST"])
    @require_auth
    def api_push_config() -> Response:
        ctx = get_context()
        data = _require_json(request)
        node_id = _require_field(data, "node_id")
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
                apply_preset(profiles, preset_name)
            except KeyError as exc:
                abort(400, description=str(exc))
        elif "global" in data:
            global_settings = data["global"]
            if not isinstance(global_settings, dict):
                abort(400, description="global must be an object")
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
            }
        )

    @app.route("/api/accessibility/override", methods=["POST"])
    @require_auth
    def api_accessibility_override() -> Response:
        ctx = get_context()
        data = _require_json(request)
        node_id = _require_field(data, "node_id")
        overrides = data.get("overrides")
        if not isinstance(overrides, dict):
            abort(400, description="overrides must be an object")

        set_per_node_override(ctx.accessibility, node_id, overrides)
        save_profiles(ctx.accessibility, ACCESSIBILITY_PATH)
        ctx.reload_accessibility()
        push_results = ctx.push_accessibility_configs()
        per_node = ctx.accessibility.get("per_node_overrides", {}).get(node_id, {})
        return jsonify({"ok": True, "overrides": per_node, "push": push_results})

    @app.route("/api/select-pack", methods=["POST"])
    @require_auth
    def api_select_pack() -> Response:
        ctx = get_context()
        data = _require_json(request)
        pack_name = _require_field(data, "pack_name")
        try:
            pack = ctx.select_pack(pack_name)
        except FileNotFoundError:
            abort(404, description=f"Content pack '{pack_name}' not found.")
        except ValueError as exc:
            abort(400, description=str(exc))
        push_results = ctx.push_accessibility_configs()
        return jsonify({"ok": True, "pack": pack.name, "push": push_results})

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
        return cast(WerkzeugResponse, send_file(target_path, mimetype="text/html"))

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


if __name__ == "__main__":
    # Development server entry point - not for production use.
    # Production deployments should use run_hub.py with Waitress.
    os.environ.setdefault("ECHOTRACE_ADMIN_USER", "admin")
    os.environ.setdefault("ECHOTRACE_ADMIN_PASS", "changeme")  # noqa: S105
    development_app = create_app()
    development_app.run(host="127.0.0.1", port=8080, debug=True)
