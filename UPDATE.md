---
title: DJI Link v0.8.0 — fix Wi-Fi reconnect and AP after disconnect
version: 0.8.0
prerelease: true
---

## Fixed

- **Reconnecting to a previously used network failed with
  `802-11-wireless-security-key-mgmt.property-is-missing`.** The stale profile was deleted with
  `nmcli con delete <ssid>`, but that command takes a profile *name*, not an SSID — on a
  netplan/NetworkManager Pi the profile for `MyNet` is named `netplan-wlan0-MyNet`, so the
  delete silently did nothing. `nmcli dev wifi connect` then reused the stale profile, discarded
  the supplied password, and tripped over a profile with no security section. `netctl.py` now
  enumerates profiles by their `802-11-WIRELESS.SSID` field and deletes every match regardless
  of profile name, so the credentials are always reapplied.

- **The Pi's AP address (`10.42.0.1`) stopped responding after disconnecting the uplink.** The
  BCM43430 has a single radio, so the AP on `uap0` shares wlan0's channel. Dropping the uplink
  took that channel away while hostapd kept the old one, leaving `uap0` up with its address
  assigned but off the air. `disconnect()` now restarts `dji-ap` on a daemon thread (matching
  `connect()`), letting `ap.sh` fall back to channel 6. Expect a brief AP drop while it
  retunes — reconnect the laptop if it does not come back on its own.

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
  1. Keep "version" above equal to the tag you push (tag v0.7.9 => version: 0.7.9).
  2. Commit UPDATE.md.
  3. git tag v0.7.9 && git push origin v0.7.9
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
