# Content Pack Authoring Guide

EchoTrace content packs group audio fragments, multilingual transcripts, and node role metadata so teams can swap narrative sets without code changes.

This guide is written for museum staff, educators, and exhibit developers who may be comfortable editing files but do not build software for a living. The goal is to help you create a complete story pack that the dashboard can activate without surprises.

## Before you start

Make these decisions first:

1. Which five physical objects are in the exhibit.
2. Which four are whisper objects.
3. Which one is the mystery object.
4. Which language should be the default for each object.
5. Whether each object has all the audio and transcript files it needs.

If you have not made those decisions yet, stop here and do that first. It is much easier to create the pack once the exhibit plan is settled.

## Directory Layout

```
content-packs/
  sample-pack/
    pack.yaml
    audio/
      object1_en.mp3
      ...
    transcripts/
      object1_en.html
      ...
```

Create one folder per pack. Place `pack.yaml` at the root alongside `audio/` and `transcripts/` directories that mirror the filenames declared in the YAML.

## Recommended workflow for creating a new pack

Use this order:

1. Copy `content-packs/sample-pack/` to a new folder name.
2. Rename the pack in `pack.yaml`.
3. Replace the object labels so they match the real exhibit.
4. Replace audio files one object at a time.
5. Replace transcript files one object at a time.
6. Activate the pack in **Change Story** and let the dashboard validate it.
7. Print new visitor cards only after the dashboard says the pack is ready.

This workflow reduces the chance of losing track of which files still need to be replaced.

## pack.yaml Schema

```yaml
name: sample-pack
nodes:
  object1:
    label: Entry Artifact
    role: whisper   # whisper | mystery
    default_language: en
  mystery:
    label: Mystery Object
    role: mystery
    default_language: en
media:
  object1:
    en:
      audio: audio/object1_en.mp3
      transcript: transcripts/object1_en.html
    fr:
      audio: audio/object1_fr.mp3
      transcript: transcripts/object1_fr.html
  mystery:
    en:
      audio: audio/mystery_en.mp3
      transcript: transcripts/mystery_en.html
```

### Nodes

- `object1`, `object2`, `object3`, `object4`, and `mystery` should match the `node_id` values used in your node configuration files.
- `label` is the staff-facing and visitor-facing name used throughout the dashboard. Use names that match the exhibit floor.
- `role` defines node behavior: `whisper` triggers fragments on approach, and `mystery` unlocks after enough fragments are triggered.
- `default_language` is used when a requested fragment is unavailable.

### Media Map

- Each node lists available languages.
- `audio` points to a WAV or MP3 file relative to the pack directory.
- `transcript` points to a static HTML transcript published at `/transcripts/<pack>/<filename>.html`.

Include English (`en`) plus any additional languages you support (default project includes `fr` and `es`).

## What a complete pack must contain

Before the dashboard can safely activate a pack, it should have:

1. One `pack.yaml` file.
2. Five node entries in `nodes:`
   - four whisper objects
   - one mystery object
3. At least one audio file and one transcript file for every node.
4. Matching relative paths in the `media:` section.
5. A valid default language for every node.

If any of these are missing, the dashboard should flag the pack as not ready.

## Naming rules that prevent confusion

Use stable, simple names.

- Pack folder name: short and lowercase, such as `harbor-secrets`
- Node IDs: keep the existing hardware IDs unless you are rebuilding the entire installation
- Labels: use the public-facing object name, such as `Harbor Lantern`
- Audio files: include node ID and language, such as `object1_en.mp3`
- Transcript files: match the audio naming pattern, such as `object1_en.html`

This keeps the files easy to trace during troubleshooting.

## Audio & Transcript Authoring Tips

- Keep audio files under ~2 minutes to suit gallery dwell times.
- Normalise audio levels consistently across nodes; the node safety limiter will scale down if required.
- Write transcripts as short-form HTML documents with descriptive headings and access notes. Avoid inline scripts or external references.

## Safe way to replace the sample pack

If you are new to EchoTrace, do not edit the sample pack first.

Instead:

1. Copy `sample-pack` to a new folder.
2. Leave the sample pack untouched as a fallback.
3. Replace one object’s media at a time in the new folder.
4. Validate the new pack in the dashboard.
5. Only activate the new pack after the dashboard marks it ready.

This gives you one known-good pack to return to if your new pack is incomplete.

## Deploying a Pack

1. Copy the pack directory into `content-packs/` on the hub.
2. Open **Change Story** in the dashboard.
3. Select the pack and activate it.
4. Read the readiness summary carefully.
5. If the dashboard reports missing files:
   - do not continue to visitor setup
   - correct the missing files in the pack
   - activate again
6. Once the pack is marked ready:
   - open **Set Up Exhibit** if object labels changed
   - open **Print Visitor Cards** if transcript links or names changed
   - open **Open Exhibit** and test the objects in the gallery

At the end of deployment, the dashboard should show the new story as active and the objects should receive their assigned media automatically.

## Common mistakes to avoid

- Changing node IDs in the pack without changing the physical nodes
- Forgetting to include the mystery object
- Adding a transcript file but forgetting the matching audio file
- Using a file path in `pack.yaml` that does not exactly match the real filename
- Activating a new pack before printing updated visitor cards
- Replacing the sample pack instead of copying it first

## Testing With Mocks

During development, you can load a pack on a workstation. The Flask dashboard serves transcripts locally, and unit tests validate asset resolution.
