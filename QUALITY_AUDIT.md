# Quality Audit

## Repo Profile
- Language/runtime: Python 3.10
- Main services: Flask hub (`hub/`) and Raspberry Pi node runtime (`pi_nodes/`)
- Transport/state: MQTT topics in `shared/mqtt_topics.py`
- Existing quality gates: `pytest`, `ruff`, `mypy` (via `Makefile` and `pyproject.toml`)
- Entry points: `python -m hub.run_hub`, `python -m pi_nodes.node_service`

## Findings
| ID | Severity | Status | Summary | Rationale |
|---|---|---|---|---|
| F-001 | P1 | Fixed | Node-reported config version could only increase in hub memory, masking drift after reboot/reset. | Could falsely show `in_sync` even when node rolled back. |
| F-002 | P1 | Fixed | Node accepted stale out-of-order config versions. | Network reordering could rollback active config. |
| F-003 | P1 | Fixed | Node persistence failures during config apply had no explicit failure ACK path. | Hub could treat apply as successful while node failed to persist. |
| F-004 | P1 | Fixed | Dashboard pack activation could raise an unhandled exception for unknown or malformed packs. | Staff-facing API could return a 500 instead of a controlled operator error. |
| F-005 | P1 | Fixed | Dashboard node-test endpoint accepted arbitrary action strings. | Invalid control payloads could be forwarded to nodes from the operator UI. |
| F-006 | P1 | Fixed | Node runtime config persisted in-place. | Interrupted writes could leave a truncated config and break node restart behavior. |
| F-007 | P1 | Fixed | Nodes page rendered missing heartbeat ages unsafely. | A node with no heartbeat could break the readiness view. |
| F-008 | P2 | Fixed | Request-boundary normalization lived inline in dashboard routes. | Duplicated parsing/validation increased maintenance cost. |
| F-009 | P2 | Fixed | Heartbeat display formatting lived in the template. | Mixed rendering logic made missing-data handling brittle. |

## Fixes Applied
- Hub sync correctness:
  - [hub/hub_listener.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/hub/hub_listener.py)
  - Added `_set_reported_version` and `_config_sync_state`; heartbeat now updates reported version directly.
  - Non-success ACK (`status != ok`) no longer completes push events.
- Node config apply hardening:
  - [pi_nodes/node_service.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/pi_nodes/node_service.py)
  - Reject stale config versions, emit explicit `"stale"` ACK.
  - Catch persist failures and emit `"error"` ACK with `persist_failed`.
- Dashboard request hardening:
  - [hub/dashboard_app.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/hub/dashboard_app.py)
  - Added `_require_pack_validation` to return controlled `404`/`400` responses for bad pack activation requests.
  - Added `_require_node_test_action` to reject unsupported node test commands before they reach the node runtime.
- Node durability hardening:
  - [pi_nodes/node_service.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/pi_nodes/node_service.py)
  - Switched runtime config persistence to temp-file plus atomic replace to preserve the last known-good config on write failure.
- Staff view resilience:
  - [hub/dashboard_app.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/hub/dashboard_app.py)
  - [hub/templates/nodes.html](/Users/watrall/Documents/Github/echotrace-whispering-objects/hub/templates/nodes.html)
  - Added safe heartbeat-age formatting so uninitialized nodes render without template errors.
- Test strengthening:
  - [tests/test_hub_listener_sync.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/tests/test_hub_listener_sync.py)
  - [tests/test_node_smoke.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/tests/test_node_smoke.py)
  - [tests/test_dashboard_api.py](/Users/watrall/Documents/Github/echotrace-whispering-objects/tests/test_dashboard_api.py)

## How to Validate
- `python3 -m ruff check .`
- `python3 -m mypy hub pi_nodes shared`
- `python3 -m pytest -q`

## Toolchain Change Request (Approval Required)
- None requested.
