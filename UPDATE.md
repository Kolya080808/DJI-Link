---
title: DJI Link v0.7.8 — fix Wi-Fi scan never starting
version: 0.7.8
prerelease: true
---

## Fixed

- **Wi-Fi scan screen was permanently stuck on "Asking the Pi to scan...".**
  `WifiUi::spawn()` checked `busy()` before starting the background thread, but
  `refresh()`, `connect()` and `disconnect()` all set a busy state *before* calling
  `spawn()` — so `busy()` was always true and the thread was never created. The guard
  was redundant: the UI buttons are disabled while `busy()` is true, preventing
  concurrent calls, and the initial `refresh()` from the constructor always arrives
  in the `Idle` state. Removing the guard lets the thread start on every call.

## Known limitations

- The Pi bundle is still Python + shell; the C++ rewrite of `pi/` has not started.
- On Windows the AP scan comes from `netsh`'s cached list; a Pi powered on seconds
  earlier may need one Retry on the discovery screen.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.7.8 => version: 0.7.8).
  2. Commit UPDATE.md.
  3. git tag v0.7.8 && git push origin v0.7.8
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
