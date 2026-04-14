# Admin Playbook

This playbook covers daily routines, accessibility management, troubleshooting, and maintenance tasks for EchoTrace.

This version assumes a small museum team with limited technical support. If you are unsure where to start during the day, begin with **Open Exhibit**. If something looks wrong, begin with **Fix an Issue**.

## Daily Start-Up Checklist

1. **Inspect hardware**: Ensure Raspberry Pi nodes, sensors, speakers, and power supplies are connected and strain-free.
2. **Power on**: Start the hub first, then power on the nodes. LEDs should glow within 60 seconds.
3. **Verify network**: Confirm local Wi-Fi or wired network availability. The hub and nodes must all be on the same local network.
4. **Open the dashboard**: From a device on the same network, open `http://<hub-ip>:8080/` and log in.
5. **Open Overview first**: Confirm the landing page does not show a major unresolved problem.
6. **Open the Open Exhibit page**: This is the daily operating page for staff.
7. **Read the status line at the top of Open Exhibit**:
   - if it says the system is ready, continue
   - if it shows a warning or blocker, open **Fix an Issue**
8. **Run one-click checks**: Use **Test Sound** and **Test Light** for each object.
9. **Walk the gallery once**: Approach each object to confirm that sound, light, and sensor behavior feel right in the actual room.

At the end of daily start-up, the exhibit should be ready for visitors and no red issue cards should remain.

## Setting the Tone for the Day

Use this section after the system is running but before visitors enter.

- **Select a story pack**: Open **Change Story** and confirm the correct story is active.
- **Choose a visitor support preset**: Open **Support Visitors** and apply the preset needed for that session.
- **Confirm visitor cards**: Open **Print Visitor Cards** if object names, language choices, or the story pack changed.
- **Test a whisper in the gallery**: Approach each node and confirm audio, LED, and optional haptic feedback.

## During Public Hours

Start with the least disruptive action first.

- Check **Overview** for a quick summary of whether the installation is still behaving normally.
- Use **Fix an Issue** before using deeper pages. It is the fastest route when something is wrong.
- Use **Object Status** only when one specific object needs closer inspection.
- Use **Support Visitors** if accessibility needs change during the day. Reapply a preset before changing per-object settings.
- Open **Reports** only when you want to review patterns or export data. It is not required for normal front-line operation.

## Troubleshooting

| Symptom | Action |
|---------|--------|
| A node does not appear in the dashboard | Check power first, then confirm the node is on the same network as the hub. If it still does not appear, check that `broker_host` in `pi_nodes/node_config.yaml` points to the hub IP address. |
| A node appears but says it needs reconnect or has not checked in recently | Open **Fix an Issue** and use **Try Reconnect** first. If that does not work, reboot that node and check its power and network again. |
| Audio is distorted or silent | Confirm speaker wiring and amplifier power. Then open **Change Story** and confirm the active story pack is valid. If the pack is valid, lower the volume through **Support Visitors** for that object. |
| The mystery object never unlocks | Open **Reports** and confirm that enough different whisper objects were triggered. If not, test the missing objects. If yes, review the `required_fragments_to_unlock` setting in `hub/config.yaml`. |
| The dashboard will not open at all | On the hub, run `sudo systemctl status echotrace-hub` and `sudo systemctl status mosquitto`. If the hub service is running, confirm you are visiting the correct hub IP address. |
| An object triggers even when nobody is nearby | Reposition the sensor to reduce reflections first. If the problem continues, adjust the node’s distance thresholds. |

When in doubt:

1. Open **Fix an Issue** first.
2. Use **Reconnect** before changing settings.
3. Change one thing at a time.
4. Test the object in the gallery after every change.

## Accessibility Suite

Open **Support Visitors** and work from top to bottom.

- **Accessibility presets**: Use these first when the whole exhibition needs the same support mode.
- **Whole-gallery settings**: Use these only if every object needs the same change.
- **Per-object overrides**: Use these only if one object behaves differently from the rest.

Overrides persist in `hub/accessibility_profiles.yaml`.

## Maintenance Schedule

- **Weekly**: Export analytics CSV, archive it, and inspect cabling and sensor mounts.
- **Monthly**: Update content packs as needed, review system packages, and check free disk space.
- **Quarterly**: Back up `/opt/echotrace` and content packs, then test UPS or power conditioning hardware.

## Backup and Restore

1. Stop services:
   - on the hub: `sudo systemctl stop echotrace-hub`
   - on each node: `sudo systemctl stop echotrace-node`
2. Copy `/opt/echotrace` from the hub and `/opt/echotrace-node` from each node to an external drive.
3. Also back up:
   - `content-packs/`
   - `hub/staff_settings.yaml`
   - `hub/accessibility_profiles.yaml`
4. Restore by copying the directories back, reinstalling Python dependencies, and re-enabling services.
5. After restore, confirm that:
   - the dashboard opens
   - the active story pack appears
   - nodes check in again

## Security & Privacy

- Change administrator credentials regularly (`ECHOTRACE_ADMIN_USER`, `ECHOTRACE_ADMIN_PASS`).
- Keep the hub on a private museum VLAN with no external internet exposure.
- Do not ingest visitor identifiers; transcripts are static and audio playback is one-way.

Use this playbook as a baseline and adapt it to each exhibition schedule.
