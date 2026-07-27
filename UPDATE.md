---
title: DJI Link v0.7.4 — logs you can find, installers that elevate themselves, working mouse yaw
version: 0.7.4
prerelease: true
---

## Highlights

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

- Landing on `L` and the mouse-yaw fix were verified by inspection and compile only, not
  against the drone. The GUI translation unit is not compiled locally either (no SDL2
  here), so it is first built on CI.
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
  1. Keep "version" above equal to the tag you push (tag v0.7.4 => version: 0.7.4).
  2. Commit UPDATE.md.
  3. git tag v0.7.4 && git push origin v0.7.4
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
