---
title: DJI Link v0.8.5 — Bridge is always reachable
version: 0.8.5
prerelease: true
---

## Fixed

- **`Start flying` could look like it did nothing after the v0.8.x Pi Wi-Fi work.**
  Discovery found the Pi through the netctl API on `:9911`, but the flight screen then
  needs the AOA bridge on `:9910`. The bridge process used to open `:9910` only after
  the AOA/UDC path was ready, so the desktop could find the Pi and immediately fail to
  enter flight mode because the bridge port was still closed.

- **`dji-bridge.service` no longer waits on `dji-netctl.service`.** The bridge is an
  independent data path and now starts as soon as `network.target` is up. This keeps the
  flying endpoint available even if the Wi-Fi control service is restarting, blocked on
  AP recovery, or otherwise slow.

- **`bridge.py` now listens on `:9910` immediately.** AOA setup runs in a background
  worker and retries there. If the laptop connects before the RC/UDC is ready, the TCP
  connection is accepted and incoming frames are explicitly logged as dropped until AOA
  becomes available, instead of making the port look dead.

- **Bridge crashes and background-thread failures are now visible.** `bridge.py` tees
  stdout/stderr to systemd journal and `/var/log/dji-link/bridge.log`, installs
  `sys.excepthook`, `threading.excepthook`, and `faulthandler`, and logs full tracebacks
  for AOA worker crashes, TCP session crashes, and fatal main-loop failures.

## Kept

- The existing AOA process self-restart on dirty USB disconnect is intentionally kept.
  It is still the recovery path for the UDC state machine; this release only makes the
  normal bridge listener come up earlier and improves logging around failures.

## Known limitations

- This is a Pi bundle / service wiring fix. Desktop flying code is unchanged.
- If `/dev/raw-gadget` is missing on a first install, `install.sh` still reports that
  bridge activation completes after reboot; `setup_pi.sh` still installs the service,
  and the service then opens `:9910` immediately when it starts.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.8.5 => version: 0.8.5).
  2. Commit UPDATE.md.
  3. git tag v0.8.5 && git push origin v0.8.5
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
