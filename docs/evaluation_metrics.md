# Evaluation Metrics

EchoTrace collects lightweight analytics so facilitators can tune visitor support without capturing personally identifiable information (PII). Metrics remain offline on the hub and can be exported as CSV when needed.

## Logged Events

The hub records the following event types in `hub/logs/YYYY-MM-DD_events.csv`:

- `heartbeat_received`: Node health beacons for uptime and disconnect detection.
- `fragment_triggered`: Proximity events that start whisper audio.
- `narrative_unlocked`: Moments when enough fragments were triggered to unlock the mystery object.
- `config_push_ok` / `config_push_timeout`: Dashboard configuration pushes and acknowledgement status.
- `admin_action`: Staff interventions such as manual narrative resets.

Each line captures the ISO 8601 timestamp, the event name, an optional node identifier, and a detail payload (JSON string or short message).

## Derived Metrics

The dashboard summarises the latest log with:

- **Trigger counts by node**: How often each object triggered during the logging window.
- **Narrative completion rate**: Ratio of `narrative_unlocked` events to fragment triggers, capped at 100%.
- **Average trigger interval**: Approximate dwell indicator based on time between consecutive fragment triggers.
- **Heartbeat tally**: Quick check that nodes remain responsive.
- **Recent events list**: The ten most recent log entries.

These summaries are recomputed on request; no historical aggregation is stored beyond the raw CSVs.

## Privacy and Ethics

- No visitor identifiers, photos, audio recordings, or typed input are collected.
- CSV files remain on the Raspberry Pi hub; exporting requires staff action via the dashboard.
- Staff can clear the CSV directory or archive files between exhibitions if needed.
- Data is intended for formative evaluation: identifying popular objects, diagnosing node failures, and monitoring the pacing of collaborative discovery.

## Recommended Evaluation Practice

1. **Baseline observation**: Run the installation for one day and export CSV logs to capture typical traffic.
2. **Intervention tracking**: When changing content packs, accessibility presets, or layout, add an `admin_action` note and compare before/after logs.
3. **Reflective debrief**: Pair summary metrics with floor observations to interpret collaboration and dwell patterns.
4. **Retention policy**: Store exported CSV files securely, label context (date, exhibition, facilitator), and delete local hub copies after transfer.

Use these metrics with observational notes to tune the experience while keeping privacy constraints intact.
