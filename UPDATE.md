---
title: DJI Link v0.7.9 — fix Pi Wi-Fi connect
version: 0.7.9
prerelease: true
---

## Fixed

- **Wi-Fi scan screen was permanently stuck on "Asking the Pi to scan...".** `WifiUi::spawn()`
  checked `busy()` before starting the background thread, but `refresh()`, `connect()` and
  `disconnect()` all set a busy state *before* calling `spawn()` — so `busy()` was always
  true and the thread was never created. Removing the redundant guard lets the thread start.

- **Pi always reported "did not answer" even on a successful Wi-Fi join.** `netctl.py connect()`
  called `systemctl restart dji-ap` synchronously before returning, which tore down the TCP
  connection the HTTP server was about to reply on. The C++ client always saw a broken socket.
  Fix: the AP restart now happens on a daemon thread so the HTTP response goes out first. The
  C++ side also no longer treats "no answer" as a definitive failure — it calls `wait_for_pi`
  and then `pi_status` to determine the actual outcome regardless.

- **Password was silently ignored when a saved Wi-Fi profile already existed.** `nmcli dev wifi
  connect SSID password PSK` reuses an existing saved profile and discards the `password`
  argument. `netctl.py connect()` now deletes any profile for the target SSID before connecting,
  so the given credentials are always applied.

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
