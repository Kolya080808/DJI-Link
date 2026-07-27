---
title: DJI Link v0.7.0 — the app finds the Pi again (LAN, /24 sweep and Wi-Fi AP)
version: 0.7.1
prerelease: true
---

## Highlights

- 

## Added

- 

## Fixed

- 

## Changed

- Tried to fix Pi code - will see how it works in a few hours.

## Known limitations

- The Pi bundle is still Python + shell; the C++ rewrite of `pi/` has not started, so
  the Pi keeps needing `python3` and NetworkManager.
- The AP's NAT rules are not persisted across reboots by design — `netctl.py`
  re-applies them every time the hotspot comes up.
- On Windows the list of access points comes from the Wi-Fi service's cache
  (`netsh wlan show networks`); a Pi powered on seconds earlier may need one Retry.
- Joining the Pi's access point moves the PC off its own Wi-Fi. Internet then depends
  on the Pi having an uplink of its own — the discovery screen says when it does not.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.7.0 => version: 0.7.0).
  2. Commit UPDATE.md.
  3. git tag v0.7.1 && git push origin v0.7.1
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
