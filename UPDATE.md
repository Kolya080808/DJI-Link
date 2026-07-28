---
title: DJI Link v0.8.1 — fix Wi-Fi reconnect and AP after disconnect
version: 0.8.1
prerelease: true
---

## Fixed

- **Reconnecting to a previously used network still failed with
  `802-11-wireless-security-key-mgmt.property-is-missing`.** Two separate bugs, both fixed.

  The error is a profile *validation* failure, not a wrong password: a saved profile that has
  an `802-11-wireless-security` section but no `key-mgmt` value cannot be activated at all.
  `nmcli dev wifi connect` is a wrapper that reuses any profile matching the SSID, so once a
  stale one existed, the password passed on the command line never reached it.

  Finding the stale profile was the first bug. `nmcli con delete <ssid>` takes a profile
  *name*, not an SSID, and a generated profile is often named something else entirely, so the
  delete silently did nothing. The previous attempt to fix that queried
  `nmcli -t -f NAME,802-11-WIRELESS.SSID con show` — but setting properties are not valid
  fields for the `con show` *list* (nmcli answers rc=2 and "invalid field"), so the loop
  matched nothing and the deletion was dead code. Profiles are now listed with
  `UUID,NAME,TYPE,FILENAME`, each Wi-Fi profile is queried individually for its SSID, and
  matches are deleted with the explicit `uuid` keyword, since names are not unique. The
  access point's own profile is skipped both by name and by being bound to `uap0`.

  Deletion alone is not enough, which was the second bug. A profile can survive it when it is
  read-only or regenerated from `/etc/netplan`, and `dev wifi connect` will reuse it again.
  When that command fails, `connect()` now creates the profile itself in a single `con add`
  carrying `key-mgmt`, the passphrase, and `psk-flags 0`. All in one call because setting
  `key-mgmt` separately from its dependent properties can fail validation on its own;
  `psk-flags 0` marks the secret system-owned, since the default waits for a secret agent
  that does not exist on a headless Pi and fails with "Secrets were required, but not
  provided". `key-mgmt` is chosen from the scan — `sae` for a WPA3-only AP, otherwise
  `wpa-psk`, which covers WPA2 and WPA3-transition. A profile that fails to activate is
  removed instead of being left behind, and a profile backed by `/etc/netplan` is called out
  in the log because it can come back on its own.

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
  1. Keep "version" above equal to the tag you push (tag v0.8.0 => version: 0.8.0).
  2. Commit UPDATE.md.
  3. git tag v0.8.0 && git push origin v0.8.0
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
