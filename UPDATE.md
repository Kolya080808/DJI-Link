---
title: DJI Link v0.8.6 — AP recovery wins over uplink
version: 0.8.6
prerelease: true
---

## Fixed

- **The Pi installer could roll back a working upgrade while the uplink was on channel
  7 or another non-1/6/11 2.4 GHz channel.** After repeated short hostapd starts,
  `ap.sh` treated the AP as unstable and pinned it to the safe fallback list
  (`6 -> 1 -> 11`) before checking the current uplink channel.

- **AP recovery now wins over the uplink after repeated hostapd failures.** While the AP
  is healthy, it still follows the station channel when that channel is usable. After
  repeated instant failures, `ap.sh` stops following `wlan0`, disconnects the uplink in
  `ExecStartPre`, and starts the Pi's own AP on a safe 2.4 GHz channel instead. This can
  temporarily drop internet/LAN uplink, but keeps `PI_DJI_LINK-*` reachable.

- **The release Pi bundle now runs the Wi-Fi regression tests before publishing.**
  `release.yml` executes `tests/ap_channel_test.sh` and `tests/netctl_sim_test.py`
  before packaging `dji-link-pi.tar.gz`, so the AP recovery rollback case and the
  NetworkManager explicit-profile fixes are checked during tagged releases.

## Kept

- The v0.8.5 bridge behavior is unchanged: `dji-bridge` still starts independently,
  listens on `:9910` immediately, retries AOA setup in the background, and logs crashes
  to the journal plus `/var/log/dji-link/bridge.log`.

- The existing AOA process self-restart on dirty USB disconnect remains in place.

## Known limitations

- This is a Pi AP/release-gate fix. Desktop flying code is unchanged.
- If `/dev/raw-gadget` is missing on a first install, `install.sh` still reports that
  bridge activation completes after reboot; `setup_pi.sh` still installs the service,
  and the service then opens `:9910` immediately when it starts.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.8.6 => version: 0.8.6).
  2. Commit UPDATE.md.
  3. git tag v0.8.6 && git push origin v0.8.6
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
