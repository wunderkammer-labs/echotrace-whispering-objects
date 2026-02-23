# Admin Playbook

This playbook covers daily routines, accessibility management, troubleshooting, and maintenance tasks for EchoTrace.

## Daily Start-Up Checklist

1. **Inspect hardware**: Ensure Raspberry Pi nodes, sensors, speakers, and power supplies are connected and strain-free.
2. **Power on**: Start the hub (Raspberry Pi 4/5) and node devices (Pi Zero 2 W). LEDs should glow within 60 seconds.
3. **Verify network**: Confirm local Wi-Fi or wired network availability. The hub must run Mosquitto.
4. **Check dashboard health**: From a device on the same network, open `http://<hub-ip>:8080/` and log in.
5. **Review analytics badge**: Confirm all nodes report heartbeats in the Overview/Nodes panel.

## Setting the Tone for the Day

- **Select a content pack**: In **Content**, choose the pack to load.
- **Choose accessibility mode**: In **Accessibility**, apply the preset needed for that session.
- **Test a whisper**: Approach each node and confirm audio, LED, and optional haptic feedback.

## During Public Hours

- Monitor the dashboard occasionally; the **Analytics** tab lists recent events and trigger counts. Spikes may indicate a popular object or repeated false triggers.
- Use the **Nodes** tab to push targeted adjustments (e.g., lower volume on a busy node) or log a planned restart.
- If accessibility needs change (quiet hour, mobility group), reapply presets or set per-node overrides without restarting hardware.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| Node missing from heartbeats | Check power, sensor cables, then reboot the Pi Zero. In **Nodes**, use “Push Config” with a minimal payload to wake the MQTT client. |
| Audio distorted or silent | Confirm speaker connections and that the fragment file exists in the content pack. Push an accessibility override with a gentler volume or reload the content pack. |
| Mystery object never unlocks | Verify the `required_fragments_to_unlock` setting in `hub/config.yaml`. Confirm four unique nodes (by ID) triggered in the analytics table. |
| Dashboard inaccessible | Make sure the hub service is running (`sudo systemctl status echotrace-hub`). Check Mosquitto service status if hub logs show connection errors. |
| Excess triggers without visitors | The VL53 sensor may see reflections. Adjust the node’s proximity thresholds via per-node override or reposition the sensor to avoid glossy surfaces. |

## Accessibility Suite

- **Global toggles**: Captions, high contrast theme, sensory-friendly pacing, safety limiter, and quiet hours.
- **Presets**: Hard of hearing, low vision, sensory friendly, and mobility aware profiles.
- **Per-node overrides**: Adjust `visual_pulse`, `proximity_glow`, `repeat`, `pace`, mobility buffer, and per-node volume. Overrides persist in `hub/accessibility_profiles.yaml`.

## Maintenance Schedule

- **Weekly**: Export analytics CSV, archive it, and inspect cabling and sensor mounts.
- **Monthly**: Update content packs as needed, review system packages, and check free disk space.
- **Quarterly**: Back up `/opt/echotrace` and content packs, then test UPS or power conditioning hardware.

## Backup and Restore

1. Stop services: `sudo systemctl stop echotrace-hub echotrace-node` on each device.
2. Copy `/opt/echotrace` (hub) or `/opt/echotrace-node` (node) to an external drive.
3. Restore by copying the directories back, reinstalling Python dependencies (`make install` or `pip install -r requirements.txt -r requirements-dev.txt`), and re-enabling services.

## Security & Privacy

- Change administrator credentials regularly (`ECHOTRACE_ADMIN_USER`, `ECHOTRACE_ADMIN_PASS`).
- Keep the hub on a private museum VLAN with no external internet exposure.
- Do not ingest visitor identifiers; transcripts are static and audio playback is one-way.

Use this playbook as a baseline and adapt it to each exhibition schedule.
