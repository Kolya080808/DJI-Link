---
title: DJI Link v0.7.6 — reliable control (no torn frames, no UI freeze)
version: 0.7.6
prerelease: true
---

## Highlights

- Stick, takeoff and land commands now reach the flight controller reliably, and the
  Windows GUI no longer freezes mid-flight. Three separate faults were behind this; all
  are fixed below.

- The Pi AP no longer drops clients every few minutes. BCM43430 (Pi Zero 2 W) firmware
  was crashing under AP+STA load with power saving on, causing 40+ disassociations per
  second and a reconnect loop. Both root causes are now fixed.

- **Pi AP only accepted one connection per boot.** After the laptop disconnected,
  BCM43430 firmware was left in a stuck state — hostapd could not accept new
  associations, leaving the C++ client in an endless "checking network requirements"
  loop. Fix: `netctl.py serve` now runs an `ap_watchdog` thread that polls the DHCP
  lease file; 20 seconds after the last client leaves (allowing time for a quick
  reconnect) it restarts `dji-ap`, so the next connection attempt always finds a
  clean AP.

- **The AP kicked the client roughly 40 times a second.** dmesg showed
  `brcmf_cfg80211_stop_ap: setting AP mode failed -52` / `brcmf_link_down: WLC_DISASSOC
  failed -52` — a BCM43430 firmware reset. The reset was triggered by the wireless power
  management being enabled (`brcmf_cfg80211_set_power_mgmt: power save enabled`) while
  the chip runs AP+STA concurrently. After the reset hostapd received a burst of low-ACK
  events and kicked the client, generating the "40 disassociated per second" storm seen
  in the logs. Fix: `ap.sh pre` now runs `iw dev wlan0 set power_save off` before
  starting hostapd, and `disassoc_low_ack=0` is added to the generated hostapd.conf so
  short packet-loss bursts during a firmware hiccup no longer cause a client kick.

- Log files now land in a fixed, always-writable folder instead of wherever the app
  happened to be started from. On Windows that is
  `%LOCALAPPDATA%\DJI-Link\logs\latest.log`.
- The Pi installer and updater no longer refuse to run without `sudo` — they re-run
  themselves under `sudo` and get on with it.
- Mouse yaw reaches the drone smoothly instead of in sparse jerks, and `L` actually
  lands.

## Added

- The exact log path is printed to stdout at startup, so a run from a terminal tells you
  where to look without guessing.

## Fixed

- **Commands were silently lost to torn DUML frames.** `Drone::cmd()` encoded a packet and
  wrote it to the socket without holding any lock, while the 20 Hz sender loop, the
  telemetry/stats loop, the detached camera threads and the GUI thread all issued commands
  concurrently. Two overlapping `send()` calls interleave their bytes on one socket and the
  flight controller drops the resulting frame, which is why takeoff and land behaved
  erratically — the command never arrived intact. Encode and send now happen under a single
  transmit mutex, so every frame reaches the wire whole. The mutex is shared (not a plain
  member) because the detached `take_photo` and `start_record` threads outlive the call that
  spawned them and must take the same lock; otherwise they would have remained a hole in the
  serialisation.
- **Duplicate sequence numbers made the FC ignore packets.** `seq_` was a plain `uint16_t`
  incremented from all of those threads at once, so two packets could go out with the same
  seq and the flight controller treated the second as a repeat. It is now `std::atomic`.
- **The Windows GUI froze during flight.** `call()` ran the network request directly on the
  render thread, so a blocking `::send()` into a full socket buffer stalled the entire UI —
  unacceptable while airborne. Commands are now queued: the GUI only enqueues, a dedicated
  worker thread performs the transfer, and the UI never waits on the network. `close()`
  drains the queue before shutting down, so a land issued at the last moment still goes out
  while the socket is alive, and every thread is joined.
- **Gimbal commands flooded the socket.** The C++ client sent a gimbal frame on every mouse
  event and every rendered frame — hundreds of packets per second into one socket — where the
  Python beta sends at most one per 100 ms. The C++ side now matches the beta's rate, and
  superseded gimbal frames are not allowed to pile up in the queue.
- **Camera and RTH settings could send a stale value.** The settings-panel handlers built
  their lambdas with a by-reference capture and were executed later on another thread, so RTH
  altitude, EV, ISO and shutter could transmit a number that had changed in between. They now
  capture the value at click time.
- **Logs were written to an unpredictable directory.** The log folder was resolved from
  the process working directory. For a double-clicked GUI build that is whatever Explorer
  set it to (the shortcut target, or `System32`), so `logs\latest.log` was not next to
  the executable and often was not created at all. The location is now absolute and
  chosen per platform: `%LOCALAPPDATA%\DJI-Link\logs` on Windows,
  `~/.local/state/dji-link/logs` on Linux and macOS, with `TEMP` as a last resort. An
  installed build's own directory is deliberately not used — under `Program Files` it
  needs administrator rights to write, which would silently break logging again.
- **Mouse yaw was mostly discarded.** The GUI accumulated `SDL_MOUSEMOTION` deltas into a
  per-frame variable and cleared it every rendered frame. Rendering runs at about 60 Hz
  while stick setpoints are sent at 20 Hz, and the two loops are independent, so roughly
  two of every three mouse frames were zeroed before the sender ever read them. The
  accumulator now lives in the client: the GUI only adds to it and the sender takes and
  clears it, so every count is sent exactly once. It is also flushed while control is
  off, so enabling control no longer applies a burst of yaw collected beforehand.
- **`L` did not land.** The key handler requested `AUTO_LANDING` but left virtual stick
  mode enabled, so the sender kept pushing velocity setpoints 20 times a second and
  overrode the landing command right after the flight controller accepted it — the drone
  acknowledged and stayed put. `L` now releases control the way the console `land`
  command already did.

- **`linux-x86` CI job failed every release.** `johnvansickle.com` returns HTTP 415 to
  GitHub Actions runner IPs, so the 32-bit ffmpeg download always failed and broke the
  job. The step now tries `eugeneware/ffmpeg-static` first (GitHub Releases, never
  blocked) and handles both a bare executable and a tar.xz. If every source fails,
  bundling is skipped gracefully (`exit 0`) — ffmpeg is optional in the CMake build and
  users can supply it themselves.

## Changed

- `install.sh`, `update_pi.sh` and `setup_pi.sh` re-exec themselves through `sudo` when
  started as a normal user, replacing the previous hard error. A `DJI_REEXEC` guard stops
  any re-entry loop, and if `sudo` is missing the scripts say so and exit. The
  `curl | bash` path has no file on disk to re-run, so it re-downloads the installer to a
  temporary file and elevates that; if the download fails it prints the exact
  `curl ... | sudo bash` command to use instead.
- All Pi script output is ASCII. The previous Russian root-check message is gone.
- Line endings for shell scripts are pinned to LF in `.gitattributes`. Several files in
  the working tree had drifted to CRLF, which stops a shell script from running on the Pi
  outright — the interpreter line ends up with a trailing carriage return.

## Known limitations

- The control fixes (transmit serialisation, atomic seq, command queue, gimbal rate) were
  verified by code inspection and compilation, not yet against the drone. `client.cpp` and
  `drone.cpp` pass a syntax-only compile locally; the GUI translation unit is not compiled
  locally (no SDL2 here), so `gui.cpp` — which carries most of the queue changes — is first
  built on CI. Landing on `L` and the mouse-yaw fix from the previous release are likewise
  unverified in flight.
- The Pi bundle is still Python + shell; the C++ rewrite of `pi/` has not started, so
  the Pi keeps needing `python3` and NetworkManager.
- The AP's NAT rules are not persisted across reboots by design — `netctl.py`
  re-applies them every time the hotspot comes up.
- On Windows the list of access points comes from the Wi-Fi service's cache
  (`netsh wlan show networks`); a Pi powered on seconds earlier may need one Retry.
- Joining the Pi's access point moves the PC off its own Wi-Fi. Internet then depends
  on the Pi having an uplink of its own — the discovery screen says when it does not.
- A PC with no Wi-Fi adapter cannot join the Pi's access point at all. On such a machine
  the Pi has to be reached over the wired LAN, which works but is not what the discovery
  screen suggests first.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.7.6 => version: 0.7.6).
  2. Commit UPDATE.md.
  3. git tag v0.7.6 && git push origin v0.7.6
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
