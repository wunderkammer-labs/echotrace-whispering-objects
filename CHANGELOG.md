# Changelog

## Unreleased

### Stability
- Prevented false `in_sync` health status by allowing hub-reported node config versions to move backward when heartbeats indicate rollback/restart.
- Rejected stale out-of-order node config updates to avoid runtime rollback from delayed MQTT messages.
- Added explicit error ACK behavior for node config persistence failures to avoid silent partial applies.
- Prevented non-success ACK payloads from completing hub config push waiters.
- Returned controlled `404`/`400` responses when staff attempt to activate an unknown or malformed content pack.
- Rejected unsupported node test actions at the dashboard API boundary instead of forwarding them to nodes.
- Switched node runtime config persistence to atomic replace so failed writes do not corrupt the saved config.
- Prevented the Nodes page from failing when a node has never reported a heartbeat.

### Maintainability
- Introduced small hub helpers for version recording and sync-state derivation to reduce duplicated logic.
- Consolidated sync-status generation in one path used by hub health snapshot responses.
- Added dashboard helpers for pack validation and node-test action normalization to keep route logic small and consistent.
- Moved heartbeat-age rendering into a Python helper instead of inline template formatting.

### Testing/Verification
- Added hub sync tests for version downgrade and non-success ACK handling.
- Strengthened node service tests for stale-version rejection and persistence-failure ACK behavior.
- Added dashboard API tests for missing packs, unsupported node test actions, and missing-heartbeat rendering.
- Added a node persistence test to verify failed writes preserve the last known-good config file.

### Notes / Deferred
- Remaining low-risk warning: `paho-mqtt` callback API v1 deprecation in `hub/hub_listener.py`.
