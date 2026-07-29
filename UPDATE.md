---
title: DJI Link v0.8.2 — Wi-Fi uplink re-join, and an access point that stays up
version: 0.8.2
prerelease: true
---

## Fixed

- **Re-joining a network the Pi had already used failed with
  `802-11-wireless-security.key-mgmt: property is missing`.** The v0.8.1 fix only ran as a
  fallback; the primary path was still `nmcli dev wifi connect <ssid> password <psk>`.

  That command hands NetworkManager a profile carrying a PSK and **no `key-mgmt`**, expecting
  the daemon to infer it from the scan entry for that SSID. There is nothing to infer from
  when the AP is not in the scan cache at that instant — right after a disconnect, on a
  re-join, on a hidden SSID, or while the single radio is busy holding the AP up — and NM's
  `verify()` rejects the profile with exactly the reported message. `dev wifi connect` is now
  gone entirely: every uplink is built explicitly with `nmcli con add … key-mgmt <wpa-psk|sae|
  none|owe> … psk-flags 0` and then `con up`. The key management type comes from the scan
  (WPA2 and WPA2+WPA3 → `wpa-psk`, WPA3-only → `sae`, plus WEP, open and OWE); `psk-flags 0`
  marks the secret system-owned, since the default waits for a secret agent that does not
  exist on a headless Pi. 802.1X networks are refused with a clear message instead of failing
  obscurely.

- **v0.8.1 could leave the Pi with no networking at all — no `PI_DJI_LINK-*` access point and
  no answer on the LAN.** `ap.sh` copied the uplink's channel into `hostapd.conf` unchecked. A
  5 GHz uplink produced `hw_mode=a` on a 2.4 GHz-only radio, and an uplink on channel 12/13
  produced a channel the world regulatory domain marks NO-IR. hostapd refuses both,
  `Restart=always` / `RestartSec=3` retried, and `ExecStopPost` deleted `uap0` every time — a
  three-second create/destroy loop on the shared brcmfmac radio that took `wlan0` down with
  it. The channel is now intersected with what the kernel says this radio may actually beacon
  on (`iw phy … info`, minus `disabled` / `no IR` / `radar detection`), falling back to 6 → 1
  → 11, and `post` no longer deletes `uap0`.

- **A failed join left the Pi with no saved profile at all.** v0.8.1 deleted every profile for
  the target SSID *before* attempting the join. The new profile is now staged under
  `dji-uplink-<slug>`, competing profiles are *parked* (`autoconnect no`) rather than deleted,
  and only a successful join deletes them and renames the profile to the SSID. A failure
  removes the staging profile, un-parks the others, and reactivates whatever was connected
  before.

- **The Pi's own network went away while it had no uplink.** `disconnect()` restarted the AP
  unconditionally and `connect()` restarted it after every join, so the laptop was dropped
  during exactly the operation it was watching. The AP is now restarted only when its channel
  must change or it is unhealthy — never "because a connect happened", and never on
  `disconnect`.

- **The Pi went quiet a few minutes after connecting.** NetworkManager re-enables Wi-Fi power
  save on every new connection, undoing `ap.sh`'s one-off `power_save off`. `setup_pi.sh` now
  writes `/etc/NetworkManager/conf.d/98-dji-wifi.conf` with `wifi.powersave = 2` and
  `wifi.scan-rand-mac-address = no`.

- **`/scan` and `/connect` blocked `/status`.** `netctl.py` serves on a `ThreadingHTTPServer`
  with per-command timeouts and a cached `have_internet()`, so the discovery screen keeps
  answering while a scan or a join is in flight.

## Added

- **`rescue.sh`** — a standalone repair script that uses nothing from the bundle. Run it on the
  Pi, or straight off the SD card's FAT partition via the `systemd.run=` first-boot hook, to
  get the access point back without a keyboard.
- **Automatic rollback.** After an upgrade `install.sh` verifies the AP came up; if it did not,
  the previous bundle is restored and the failing tag is recorded in `$PREFIX/BAD_VERSION`, so
  `dji-update.timer` stops reinstalling it every 6 hours.
- **Health checks** — `netctl.py doctor` and `GET /doctor`, plus `ap.sh health` and `ap.sh
  chan`. A watchdog re-checks the whole AP (interface, address, hostapd, dnsmasq, NAT) every
  15 s with exponential backoff, and now respects `hotspot off` instead of undoing it.

## Known limitations

- Only the Pi bundle changed. The desktop binaries are identical to v0.8.1 apart from the
  version they report — every `/status`, `/scan` and `/connect` reply keeps the shape the
  shipped client already parses.
- **Untested on hardware.** 49 behavioural checks against a simulated NetworkManager, 8
  channel-selection cases against a simulated `iw`, and a C++ test that parses the new replies
  with the shipped client all pass — but no Pi was available. `rescue.sh`, the health gate in
  the installer and the automatic rollback exist precisely for that reason.
- The Pi bundle is still Python + shell; the C++ rewrite of `pi/` has not started.
- On Windows the AP scan comes from `netsh`'s cached list; a Pi powered on seconds
  earlier may need one Retry on the discovery screen.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.8.2 => version: 0.8.2).
  2. Commit UPDATE.md.
  3. git tag v0.8.2 && git push origin v0.8.2
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
