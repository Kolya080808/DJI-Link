# Recording The Demo (its just for me)

The README demo should answer three questions quickly: what DJI Link looks like, what data it shows, and what the project controls.

## Recommended clip

- 10–20 seconds, 16:9, 1080p or higher.
- Start on the preflight screen, then show the flight HUD with live video and telemetry.
- Include one safe interaction such as opening the settings/help overlay or showing a camera/gimbal control.
- Keep the aircraft on the ground unless the recording is an intentional flight test. Never show credentials, Wi-Fi passwords, serial numbers, or private logs.
- Export a lightweight GIF for the README as `docs/demo.gif` (about 8 MB or less is a good target). Keep a higher-quality MP4 for the project site or release notes.

## Before recording

1. Confirm the drone, controller, Pi bridge, and desktop app are connected.
2. Remove propellers for a ground-only UI demonstration.
3. Set the desktop resolution and window size before starting the capture.
4. Close unrelated applications and notifications.

## Simulator fallback

For a UI-only clip without aircraft hardware:

```bash
dji-link --sim --windowed
```

The simulator demonstrates the interface, but it must be labelled as simulated. Do not present simulator telemetry as a real flight.
