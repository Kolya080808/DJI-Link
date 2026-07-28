---
title: DJI Link v0.7.7 — Pi Wi-Fi setup from the PC
version: 0.7.7
prerelease: true
---

## Highlights

- The C++ client can now connect the Raspberry Pi to a home Wi-Fi network without
  touching the Pi at all. A dedicated **Pi Wi-Fi setup** screen works on every platform
  (Windows, macOS, Linux): it lists the networks the Pi sees, accepts a WPA2 passphrase
  with a show/hide toggle, and handles the channel-retune cycle that the Pi's single
  radio always causes when joining an uplink.

- A `--wifi` flag covers headless setups: `dji-link --wifi` finds the Pi, prints a
  numbered network list, and takes a selection interactively. With `--wifi-ssid` /
  `--wifi-psk` it connects non-interactively, which is useful in an install script.

## Added

- **Wi-Fi setup screen** (`src/gui/gui.cpp`). Appears from the "Finding the Raspberry
  Pi" screen once the Pi is found — a **Pi Wi-Fi setup** button next to Retry/Back.
  All work runs on a background thread so the render loop never stalls: a scan takes
  ~3 s on the Pi, a join up to a minute. The current AP name, uplink, and internet
  reachability are shown at the top; after a successful join they refresh from a fresh
  `/status` call. Scroll support for long network lists. The "Pi reports no uplink"
  discovery-screen hint was updated to point to the new button.

- **`--wifi` CLI flag** with `--wifi-ssid SSID` and `--wifi-psk PSK` (`src/main.cpp`).
  Works whether the GUI was compiled in or not, and uses the same `netfind::` calls as
  the screen. No-args mode is interactive: it prints the Pi's current status, scans, and
  reads a number or SSID from stdin; `--wifi-ssid`/`--wifi-psk` make it non-interactive.

- **`netfind::pi_scan_wifi`, `pi_connect_wifi`, `pi_disconnect_wifi`, `pi_status`,
  `wait_for_pi`** (`src/core/netfind.hpp/.cpp`). Pure HTTP to the Pi's netctl API on
  port 9911 — no platform-specific Wi-Fi code on the PC side. `wait_for_pi` polls until
  the Pi's control port answers again after an AP retune (the single-radio hand-off that
  briefly drops the laptop's own link to the Pi).

- **`netctl_post`** — POST counterpart of the existing `netctl_get`, so `/connect` and
  `/disconnect` can be driven without `curl`.

- **Inline JSON helpers** (`json_bool`, `json_int`, `json_str`, `json_quote`) and public
  `parse_networks`, `parse_status`, `parse_action` — flat-JSON scan of netctl's
  `json.dumps` replies, no library dependency. `json_str` unescapes `\uXXXX` and `\"`
  so SSIDs with non-ASCII or quotes reach `nmcli`/`netsh` exactly as entered.

- **`tests/netctl_parse_test.cpp`** — unit test for all three parse functions using real
  netctl output fixtures. Registered with `ctest` and CMake's `enable_testing()`, so it
  runs on every CI platform in the matrix (GCC+Clang, Linux/macOS/Windows, 64/32/arm64)
  without a display or a Pi.

## Changed

- `TextInput` gained `password` and `reveal` fields: the passphrase entry shows dots by
  default and a **Show/Hide** button sits next to the field.

## Known limitations

- Wi-Fi setup was verified by code inspection and the unit tests. Flight-path changes are
  untested in this release; the fixes from v0.7.6 carry forward.
- The Pi bundle is still Python + shell; the C++ rewrite of `pi/` has not started.
- On Windows the AP scan comes from `netsh`'s cached list; a Pi powered on seconds
  earlier may need one Retry on the discovery screen.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.7.7 => version: 0.7.7).
  2. Commit UPDATE.md.
  3. git tag v0.7.7 && git push origin v0.7.7
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
