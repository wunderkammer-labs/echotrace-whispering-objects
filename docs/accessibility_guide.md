# Accessibility Guide

This guide covers the accessibility controls available in EchoTrace.

## Principles

- **Multiple modalities**: Each fragment can be experienced through audio, optional haptics, LED feedback, and QR transcript.
- **Configurable environment**: Staff can change global settings or per-node overrides during open hours.
- **No individual tracking**: Controls change environment output only. No user accounts or recordings are created.

## Global Settings

Accessible via the dashboard’s **Accessibility** tab:

- **Captions**: Flags transcript availability in the UI. QR codes stay active regardless of this toggle.
- **High contrast**: Applies a high-contrast dashboard theme.
- **Sensory-friendly mode**: Lowers default volume, slows pacing, and softens LED output.
- **Safety limiter**: Caps maximum node volume.
- **Mobility buffer**: Adds a trigger delay in milliseconds.
- **Quiet hours**: Accepts `HH:MM-HH:MM` ranges such as `"18:00-09:00"` to dim LEDs and lower volume.

## Presets

- **Hard of Hearing**: Captions with safety limiter emphasized.
- **Low Vision**: High contrast with captions enabled.
- **Sensory Friendly**: Sensory-friendly defaults with stricter limiting.
- **Mobility Aware**: Adds a 1000 ms mobility buffer to all nodes.

Presets can be layered with manual adjustments. Applying a preset updates `hub/accessibility_profiles.yaml` and pushes new runtime configuration to each node.

## Per-Node Overrides

Staff can highlight specific narratives or accommodate visitors at a particular station by overriding:

- `visual_pulse`: Blink LED during playback instead of steady glow.
- `proximity_glow`: Toggle ambient glow.
- `mobility_buffer_ms`: Extra delay before playback (0 to 60,000 ms).
- `repeat`: Automatic replay count (0 to 2).
- `pace`: Playback rate (0.85 to 1.15). This applies directly to WAV content; other formats use default speed.
- `safety_limiter`: Enable or relax limiter per node.
- `volume`: Optional per-node volume cap (0.0 to 1.0).

Overrides persist across reboots and deploy instantly via MQTT.

## Transcripts & QR Codes

Each transcript includes a short contextual paragraph, reflective prompt, and accessibility note. Print the corresponding QR codes near objects at a reachable height. For tactile labels, include braille or large-print cues indicating that audio is available nearby.

## Haptics & Sensory Management

- Haptics default to ON but can be disabled by removing the transistor or unchecking `visual_pulse`/`proximity_glow` for calmer feedback.
- Sensory-friendly mode automatically reduces LED intensity and audio volume, benefiting visitors with sensory processing differences.
- Quiet hours bring the installation into a calm state for meditation sessions, after-hours tours, or rest periods.

Use these controls as needed during programming hours. Changes apply immediately and persist as configured.
