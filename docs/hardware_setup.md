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

Prepare the hub first, then prepare one node at a time.

1. Flash Raspberry Pi OS Lite onto each microSD card.
2. Boot each Raspberry Pi once with a keyboard and monitor attached, or prepare SSH access if that is how your museum manages Raspberry Pis.
3. On every Raspberry Pi, enable SSH so you can return to the device later:
   - run `sudo raspi-config`
   - open **Interface Options**
   - enable **SSH**
4. On each node Raspberry Pi, also enable I2C for the VL53L1X sensor:
   - run `sudo raspi-config`
   - open **Interface Options**
   - enable **I2C**
5. Confirm that every Raspberry Pi joins the same local network before continuing.

### Hub software setup

1. On the hub, install Mosquitto:
   ```
   sudo apt update
   sudo apt install mosquitto mosquitto-clients
   sudo systemctl enable --now mosquitto
   ```
2. Clone this repository onto the hub at `/opt/echotrace`.
3. Create a Python virtual environment and install dependencies:
   ```
   cd /opt/echotrace
   python3 -m venv .venv
   . .venv/bin/activate
   make install
   ```
4. Review `hub/config.yaml`.
   For most museums, leave these defaults as they are:
   - `broker_host: localhost`
   - `broker_port: 1883`
   - `dashboard_port: 8080`
5. Set dashboard administrator credentials in the environment used by the service:
   - `ECHOTRACE_ADMIN_USER`
   - `ECHOTRACE_ADMIN_PASS`
6. Note the hub IP address with:
   ```
   hostname -I
   ```

At the end of hub setup, you should know the hub IP address and be able to say, “this is the Raspberry Pi the dashboard will run on.”

### Node software setup

1. Clone this repository onto one node at a time, typically at `/opt/echotrace-node`.
2. Create a Python virtual environment and install dependencies:
   ```
   cd /opt/echotrace-node
   python3 -m venv .venv
   . .venv/bin/activate
   make install
   ```
3. Open `pi_nodes/node_config.yaml`.
4. Set `node_id` to match the physical object you are building.
   Recommended IDs for the sample project are:
   - `object1`
   - `object2`
   - `object3`
   - `object4`
   - `mystery`
5. Set `role`:
   - use `whisper` for the four regular objects
   - use `mystery` for the final object that reveals the ending
6. Set the GPIO pins so they match your wiring.
   If you followed the wiring in this guide, the defaults are correct:
   - `led_pin: 18`
   - `haptic_pin: 23`
7. Add the hub address to the node configuration so the node knows where the MQTT broker lives:
   - `broker_host: <hub-ip-address>`
   - `broker_port: 1883`
8. Leave `audio.fragment_file` blank during first setup.
   This is normal. The dashboard assigns the correct audio file later when you activate a story pack.
9. Keep `language_default: en` unless your exhibition should start in another language.

At the end of each node setup, you should know:
- which physical object this Raspberry Pi belongs to
- which `node_id` it uses
- whether it is a `whisper` or `mystery` node
- which hub IP it should contact

### Configuration example for a whisper node

```yaml
node_id: object1
role: whisper
language_default: en
broker_host: 192.168.1.25
broker_port: 1883
gpio:
  led_pin: 18
  haptic_pin: 23
audio:
  fragment_file: ""
  volume: 0.7
```

### Configuration example for the mystery node

```yaml
node_id: mystery
role: mystery
language_default: en
broker_host: 192.168.1.25
broker_port: 1883
gpio:
  led_pin: 18
  haptic_pin: 23
audio:
  fragment_file: ""
  volume: 0.7
```

## Enabling Services

1. On the hub, copy `system/hub.service` to `/etc/systemd/system/echotrace-hub.service`.
2. On each node, copy `system/node.service` to `/etc/systemd/system/echotrace-node.service`.
3. Optionally create `/etc/default/echotrace` or `/etc/default/echotrace-node` to override environment variables such as:
   - `ECHOTRACE_ADMIN_USER`
   - `ECHOTRACE_ADMIN_PASS`
   - `ECHOTRACE_DIR`
4. Reload systemd:
   ```
   sudo systemctl daemon-reload
   ```
5. Enable the hub service on the hub:
   ```
   sudo systemctl enable --now echotrace-hub
   ```
6. Enable the node service on each node:
   ```
   sudo systemctl enable --now echotrace-node
   ```
7. Check status with:
   ```
   sudo systemctl status echotrace-hub
   sudo systemctl status echotrace-node
   ```
8. Inspect logs during first startup if needed:
   ```
   journalctl -u echotrace-hub -f
   journalctl -u echotrace-node -f
   ```

At the end of this section:
- the hub should reopen the dashboard automatically after a reboot
- each node should reconnect automatically after a reboot
- the dashboard should begin listing nodes once they check in

## Calibration Tips

- Use the dashboard’s **Calibration** view to note baseline distances. Adjust `story_threshold_mm` or `hysteresis_mm` via overrides if nodes trigger prematurely.
- For reflective environments, add matte shrouds around the VL53 sensor to minimise stray IR reflections.
- Balance audio levels against crowd noise. Start with a base volume around 0.6 and use the safety limiter to prevent spikes.

## Maintenance

- Keep sensors dust-free; clean with a soft lint-free cloth.
- Inspect printed brackets and laser-cut panels for wear. Replace as needed; the STL/DXF files are in `fabrication/` for quick reprints.
- Back up the content pack directory before editing live files.

With reliable wiring, secure mounting, and the provided systemd services, nodes should boot into interactive mode without manual startup steps.
