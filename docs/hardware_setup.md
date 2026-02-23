# Hardware Setup Guide

This guide outlines the components and wiring required to deploy EchoTrace in a gallery environment.

## Bill of Materials

**Hub (x1)**
- Raspberry Pi 4 or 5 (4 GB RAM recommended)
- microSD card (32 GB+), Raspberry Pi OS Lite
- USB-C power supply
- Heatsink or case with ventilation
- Optional: UPS HAT for graceful shutdown

**Nodes (x4 whisper + x1 mystery)**
- Raspberry Pi Zero 2 W per node
- microSD card (16 GB+)
- VL53L1X time-of-flight sensor breakout (Adafruit or Pimoroni)
- Class-D audio amplifier (PAM8302 or similar) + small 4 Ω speaker
- GPIO-controllable LED with 220 Ω resistor (use PWM-capable pin such as GPIO18)
- Optional: coin vibration motor with NPN transistor + flyback diode for haptics
- 5 V 2 A power supply (USB-C or micro USB depending on model)
- 3D-printed mounts or laser-cut panels for sensor alignment

## Wiring Overview

- Connect VL53L1X via I2C: SDA → GPIO2, SCL → GPIO3, VIN → 3V3, GND → GND. Keep wires short and shielded where possible.
- LED: anode to GPIO18 through a 220 Ω resistor, cathode to GND.
- Audio amplifier: Feed from Pi headphone jack (or USB audio dongle), power from 5 V rail, speaker to amplifier output.
- Haptics (optional): GPIO23 → base of NPN transistor via 1 kΩ resistor, motor between 5 V and collector, emitter to GND, diode across motor.

Refer to `fabrication/wiring_fritzing.png` for a starter schematic. Adapt the layout to your mounts and enclosures.

## Physical Installation

1. Mount sensors at visitor chest height, angled downward (~10–15°) to avoid cross-talk between nodes.
2. Use the provided STL files to 3D print bezels and LED holders. Secure with museum-safe adhesives or mechanical fasteners.
3. Route speaker cables and USB power inside exhibit furniture; strain relief is essential for public galleries.
4. Label each node with its ID (object1–object4, mystery) to match the configuration files.

## Software Installation

1. Flash Raspberry Pi OS Lite onto each microSD card.
2. Enable SSH (`sudo raspi-config` → Interface Options → SSH) for remote updates.
3. On the hub, install Mosquitto (`sudo apt install mosquitto mosquitto-clients`).
4. Clone this repository into `/opt/echotrace` on the hub and `/opt/echotrace-node` on each node.
5. Create a Python virtual environment and install dependencies: `python3 -m venv .venv && . .venv/bin/activate && make install` (or `pip install -r requirements.txt -r requirements-dev.txt`).
6. Review `hub/config.yaml` and each node’s `pi_nodes/node_config.yaml` to ensure broker hostnames, node IDs, and audio file paths are correct.

## Enabling Services

1. Copy `system/hub.service` to `/etc/systemd/system/echotrace-hub.service` on the hub and `system/node.service` to `/etc/systemd/system/echotrace-node.service` on nodes.
2. Optionally create `/etc/default/echotrace` or `/etc/default/echotrace-node` to override environment variables (e.g., `ECHOTRACE_ADMIN_USER`, `ECHOTRACE_DIR`).
3. Reload systemd: `sudo systemctl daemon-reload`.
4. Enable services: `sudo systemctl enable --now echotrace-hub` (hub) and `sudo systemctl enable --now echotrace-node` (nodes).
5. Check status with `sudo systemctl status echotrace-hub` and inspect logs via `journalctl -u echotrace-hub -f` during initial runs.

## Calibration Tips

- Use the dashboard’s **Calibration** view to note baseline distances. Adjust `story_threshold_mm` or `hysteresis_mm` via overrides if nodes trigger prematurely.
- For reflective environments, add matte shrouds around the VL53 sensor to minimise stray IR reflections.
- Balance audio levels against crowd noise. Start with a base volume around 0.6 and use the safety limiter to prevent spikes.

## Maintenance

- Keep sensors dust-free; clean with a soft lint-free cloth.
- Inspect printed brackets and laser-cut panels for wear. Replace as needed; the STL/DXF files are in `fabrication/` for quick reprints.
- Back up the content pack directory before editing live files.

With reliable wiring, secure mounting, and the provided systemd services, nodes should boot into interactive mode without manual startup steps.
