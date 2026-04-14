<img src="echotrace_logo.svg" alt="echotrace logo" width="220">

# echotrace: Whispering Objects

EchoTrace is an interactive storytelling installation designed for museums, galleries, and cultural heritage sites. It transforms objects into whispering narrators that share fragments of a larger story when visitors come near. Each “whisper node” uses a Raspberry Pi, a distance sensor, an LED, and a speaker to respond to movement and create a multisensory experience of curiosity and discovery.

As visitors explore, they hear pieces of a hidden story scattered among several artifacts. When enough fragments have been heard, a “mystery object” plays a final chapter that connects the pieces into a shared conclusion. Every step and pause becomes part of the interpretive process. Visitors learn through movement, listening, and reflection.

For museum staff, EchoTrace is designed to be easy to manage and adaptable to different exhibitions. It includes a browser-based dashboard for guided setup, daily opening checks, accessibility settings, content pack changes, transcript label printing, and engagement review. The system runs entirely on a local network, without requiring internet access. EchoTrace demonstrates how physical computing and inclusive design can deepen engagement and learning in informal museum settings.

## Features

- **Distributed architecture (hub and nodes)**  
  The system includes a central “hub” Raspberry Pi and five “node” devices: four whispering objects and one mystery object. The hub manages coordination, content, and analytics, while each node handles sensing and playback. Communication uses **MQTT (Message Queuing Telemetry Transport)**, a lightweight messaging protocol common in Internet of Things systems. MQTT allows reliable communication across the local network even without internet access.

- **Proximity-responsive storytelling**  
  Each node uses a **VL53L1X time-of-flight sensor** to detect distance. At a distance, the object emits a faint whisper. As a visitor approaches, the story becomes clear and audible. This shifting soundscape connects movement and interpretation, making space and curiosity part of the learning experience.

- **Narrative unlock system**  
  The hub tracks which story fragments have played. When a configurable number of unique fragments have been heard, the hub signals the mystery object to play the final chapter. This encourages collaboration, since no single visitor can hear the entire story alone.

- **Accessibility and inclusion**  
  EchoTrace can be adapted to visitor needs. Features include captions and transcripts via QR codes, multilingual content in English, French, and Spanish, adjustable sound and light levels, high-contrast visual options, and sensory-friendly settings. Presets make it easy for staff to enable profiles such as “hard of hearing,” “low vision,” “sensory-friendly,” or “mobility-aware.”

- **Modular content packs**  
  Story content is stored in modular packs that contain YAML metadata, MP3 or WAV audio files, and multilingual HTML transcripts. Staff can replace or edit packs without programming. This makes the system reusable across exhibitions and adaptable to different stories and collections.

- **Staff-friendly operations**  
  The dashboard includes a setup wizard, a Daily Start page, one-click node tests, a plain-language problem list, operational presets, and printable transcript labels. These tools are designed for museum teams who are comfortable with exhibit operations but do not work in code.

- **Offline analytics**  
  All visitor interactions are logged locally as CSV files on the hub. The dashboard summarizes engagement data such as number of triggers and completion rate. No personal information is collected, keeping data management simple and ethical.

## Visitor Interactions

1. **Attraction: the call of the whisper**  
   Faint sounds and soft light draw visitors closer. Each object seems alive and quietly invites approach.

2. **Embodied listening: movement as participation**  
   As a visitor approaches, the whisper becomes clear. The LED brightens, and optional haptic feedback adds a tactile rhythm. The visitor’s motion becomes part of the story experience.

3. **Interpretation through exploration**  
   Each object tells only a fragment. Visitors compare what they have heard, revisit objects, and scan QR codes for transcripts. The story becomes a puzzle to piece together.

4. **Shared discovery and the mystery reveal**  
   After enough fragments have been triggered, whether by one visitor or several, the mystery object plays the final chapter. The group experiences the conclusion together.

5. **Reflection and return**  
   Visitors often return to earlier objects to re-listen. This reflection deepens understanding and highlights how movement and curiosity shape interpretation.

## Admin Dashboard Highlights

The admin dashboard provides clear, real-time control over the installation. It is designed for museum staff and requires no programming knowledge. Each section offers visual feedback, simple controls, and instant updates through MQTT communication.

1. **Overview**  
   Displays the current content pack, node status, and progress toward the story’s conclusion. Administrators can pause or restart the experience and monitor system health.

2. **Setup Wizard**  
   Guides staff through first-run tasks such as naming objects, selecting a content pack, checking node readiness, and confirming that the exhibit is ready to open. This is the recommended starting point after installation or exhibition changes.

3. **Daily Start**  
   Provides a simple opening routine for front-line staff. It surfaces the current system status, highlights any problems that need attention, and offers one-click audio, light, sensor, and reconnect tests.

4. **Nodes**  
   Lists all active nodes with their names, roles, and signal strength. Each entry includes quick tools to test LEDs, audio playback, and sensors. Administrators can push new settings or restart nodes remotely.

5. **Accessibility**  
   Lets staff enable captions, adjust brightness or volume, and switch between accessibility presets. Settings can be applied globally or per node. All updates take effect immediately.

6. **Content Management**  
   Allows staff to activate new content packs, check language availability, and confirm that required audio and transcript files exist before going live. Metadata and audio links are displayed for verification before deployment.

7. **Calibration**  
   Shows live sensor readings and threshold distances. Staff can adjust placement or sensitivity while viewing real-time feedback to ensure smooth performance in the gallery space.

8. **Problems and Labels**  
   The Problems view lists issues in plain language with suggested actions. The Labels view prepares transcript links in a print-friendly layout so staff can update QR labels without manual URL work.

9. **Analytics**  
   Displays engagement statistics such as number of interactions, completion rates, and average time between triggers. CSV data can be exported for evaluation and reporting. The dashboard provides insights into how visitors are engaging without tracking individuals.

## Hardware Checklist

| Role | Core Hardware |
|------|----------------|
| Hub | Raspberry Pi 4 or 5, 32 GB microSD, Mosquitto MQTT broker, Flask dashboard |
| Whisper Nodes (×4) | Raspberry Pi Zero 2 W, VL53L1X sensor, LED + resistor, small amplifier + speaker, optional haptic motor |
| Mystery Node (×1) | Same as whisper node with finale audio content |
| Fabrication | 3D-printed bezels or LED holders (STL), laser-cut panels (DXF), mounting hardware |

See `docs/hardware_setup.md` for detailed wiring, installation, and maintenance instructions.

## Content Packs

- Stored under `content-packs/<pack-name>/` with YAML metadata, audio files, and HTML transcripts.  
- Each pack defines node roles, languages, and file mappings.  
- Transcripts are served locally at `/transcripts/<pack>/<filename>.html` and can be linked with QR codes.  
- See `docs/content_pack_guide.md` for authoring and localization details.

## Accessibility Suite

- Global toggles for captions, high-contrast mode, sensory-friendly playback, volume limits, mobility buffers, and quiet hours.  
- Presets for hearing, vision, sensory, and mobility support.  
- Per-node overrides for brightness, playback speed, repetition, and volume.  
- Accessibility profiles are saved in YAML and applied instantly through MQTT.

## Analytics and Privacy

- Logged events include `heartbeat_received`, `fragment_triggered`, `narrative_unlocked`, `config_push_ok`, `config_push_timeout`, and `admin_action`.  
- Dashboard summaries display counts, rates, and timing averages without collecting identifying data.  
- Data remain local to the hub unless exported by an administrator for evaluation.

## Educational and Learning Goals

- **Constructivist learning through storytelling**  
  Visitors build understanding by gathering and connecting story fragments. Each interaction becomes an act of investigation, mirroring how learning in museums often happens through exploration and curiosity.

- **Embodied cognition and spatial learning**  
  Movement is part of how visitors make sense of information. The sensors and light or sound feedback connect motion, perception, and interpretation, showing how physical engagement supports learning.

- **Collaborative and social learning**  
  No single visitor can hear the whole story alone. The design encourages sharing, discussion, and cooperative discovery, reflecting how people learn through social interaction.

- **Inclusive and multimodal participation**  
  Audio, light, vibration, and text work together so visitors with different sensory or linguistic backgrounds can participate. Accessibility features model Universal Design for Learning principles in a museum setting.

- **Reflection and metacognition**  
  The final mystery object encourages visitors to think about how they formed meaning. Revisiting earlier fragments promotes awareness of their own interpretive process.

## Quick Start

This section is written for museum staff, exhibit developers, and project partners who may be comfortable following technical steps but do not work as software engineers. If you are setting up EchoTrace for the first time, use the checklist below in order.

### Before you begin

Make sure you have:

1. One hub Raspberry Pi with the EchoTrace repository installed.
2. One node Raspberry Pi for each object in the exhibit.
3. A local network that all devices can join.
4. Power, speakers, sensors, and LEDs connected according to `docs/hardware_setup.md`.
5. A keyboard, mouse, and monitor for initial setup, or another computer on the same local network.

### Quick Start for museum staff

1. Power on the hub Raspberry Pi first.
   Wait about one minute for the operating system, dashboard, and MQTT broker to start.

2. Power on each node Raspberry Pi.
   The nodes should begin checking in automatically after boot.

3. Open the dashboard from a browser on the same network.
   Go to `http://<hub-ip>:8080/`.
   If you do not know the hub IP address, connect a monitor to the hub once and run:
   ```
   hostname -I
   ```

4. Sign in with the administrator username and password configured for the exhibit.

5. Open **Setup Wizard** if this is a new installation or a newly changed exhibition.
   Use it to:
   - assign friendly names to objects
   - choose the active content pack
   - confirm that each node appears in the dashboard
   - verify that the system is marked ready

6. Open **Daily Start** before opening to visitors.
   Use it to:
   - confirm the top status banner says the system is ready
   - run **Test Sound** and **Test Light** for each object
   - apply the correct operational preset for the day
   - review any warnings shown in **Problems**

7. Open **Content** and confirm the correct pack is active.
   The dashboard now checks for missing audio or transcript files before activation. If something is missing, it will show a checklist instead of silently activating an incomplete pack.

8. Open **Accessibility** and choose the correct preset.
   For example, use a quieter preset for sensory-friendly hours or a higher-caption workflow for groups who need more text support.

9. Open **Labels** and print transcript labels if the pack, language, or object names changed.

10. Walk the gallery once before visitors arrive.
    Approach each object to confirm that sound, light, and sensor behavior feel appropriate in the actual space.

### Quick Start for local development and testing

If you are preparing or testing EchoTrace on a workstation:

1. Create a virtual environment and install dependencies:
   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   ```

2. Run the existing verification suite:
   ```
   python3 -m ruff check .
   python3 -m mypy hub pi_nodes shared
   python3 -m pytest -q
   ```

3. Run the hub and node services in mock mode:
   - `make run-hub` starts the dashboard and listener in development mode.
   - `make run-node` starts a mocked node service loop using test hardware mocks.

4. Open the dashboard and test the staff workflow:
   - Setup Wizard
   - Daily Start
   - Content
   - Accessibility
   - Labels

## Deployment Steps (Production)

This section assumes EchoTrace is being installed in a museum, gallery, or classroom exhibition where daily operators may not be technical staff. The goal is a stable, repeatable deployment that can be opened and closed by front-line staff with minimal intervention.

### 1. Prepare the hub

1. Install the repository on the hub Raspberry Pi, typically at `/opt/echotrace`.
2. Create a Python virtual environment and install the application dependencies.
3. Configure administrator credentials in the environment used by the hub service, including:
   - `ECHOTRACE_ADMIN_USER`
   - `ECHOTRACE_ADMIN_PASS`
4. Confirm that Mosquitto or another supported local MQTT broker is installed and enabled on the hub.
5. Confirm the hub is reachable on the museum’s local network.

### 2. Prepare each node

1. Install the node runtime on each Raspberry Pi node, typically at `/opt/echotrace-node`.
2. Connect and secure the speaker, distance sensor, LED, and optional haptic hardware.
3. Confirm each node has the correct `node_id`, role, and hardware pin assignments in its local configuration.
4. Place each node in its final exhibit location if possible, since room acoustics and reflective surfaces affect tuning.

### 3. Configure services to start automatically

Copy the provided systemd unit files, adjust paths if needed, and enable them:

```
sudo systemctl enable --now hub.service
sudo systemctl enable --now node.service
```

After enabling services:

1. Reboot the hub once and confirm the dashboard returns automatically.
2. Reboot a sample node once and confirm it reconnects and appears in the dashboard.

### 4. Load exhibit content

1. Add content packs under `content-packs/`.
2. Confirm each pack contains:
   - `pack.yaml`
   - audio files for each assigned node
   - transcript HTML files for each assigned node and language
3. Open **Content** in the dashboard and activate the pack.
4. Review the validation checklist shown by the dashboard.
   If files are missing, correct the pack before opening the exhibit.

### 5. Complete the staff-facing setup

1. Open **Setup Wizard** and assign friendly names that match gallery labels.
2. Open **Daily Start** and run sound and light tests for every object.
3. Open **Accessibility** and save the default preset for the exhibition.
4. Open **Labels** and print transcript labels.
5. Open **Problems** and make sure no red issues remain.

At this point, the system should be ready for daily operation by museum staff.

### 6. Daily operating model for museum staff

Use the following routine each day:

1. Open the dashboard.
2. Go to **Daily Start**.
3. Confirm the status banner is green or otherwise states that the system is ready.
4. Run one-click sound and light tests.
5. Apply the day’s operational preset if needed.
6. Open the exhibit.

If something goes wrong during the day:

1. Open **Problems**.
2. Read the red issue cards first.
3. Follow the suggested action text.
4. Use **Reconnect** for stale nodes.
5. Use **Nodes** only if a more detailed view is needed.

### 7. Maintenance and support

For regular operations:

1. Export analytics CSV files periodically for reporting.
2. Back up content packs and staff settings after major exhibition changes.
3. Reprint transcript labels whenever a pack or language assignment changes.
4. Review `docs/admin_playbook.md` and `docs/operator_cheat_sheet.md` for daily routines and troubleshooting guidance.

## License

Released under the MIT License. See `LICENSE` for details.
