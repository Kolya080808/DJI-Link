---
title: DJI Link v0.5.0 — first C++ release (all DJI Link beta features)
version: 0.5.0
prerelease: true
---

## Highlights

- First release of the C++ rewrite of DJI Link (migrated from the Python `dji_link_beta`).
- Ships for **every supported platform**: Linux x86_64/arm64, macOS arm64/x86_64,
  and Windows x64 — plus best-effort 32-bit Linux and Windows arm64/x86 builds.
- SDL2 flight GUI (video, HUD, settings, preflight/discovery menu, in-app updater),
  with the `ffmpeg` video runtime bundled per platform.

## Added

- All functionality from the beta:
    - **Flight:** arm/takeoff/land, virtual-stick control, ground-station mode, RTH.
    - **Camera:** photo, record start/stop, camera mode, ISO/shutter/EV, zoom, codec.
    - **Gimbal:** angle/speed control and recenter.
    - **Limits:** max altitude, max distance, RTH altitude, no-GPS unlock.
    - Home point (current location / explicit lat-lon).
- Headless console client (`--console`) for the Pi / debugging.
- Raspberry Pi one-command installer (`install-pi.sh` + `dji-link-pi.tar.gz`).

## Changed

- CI now compile-checks **every release platform** (its build matrix mirrors the
  release matrix 1:1), so no platform builds for the first time only during a release.

## Fixed

- Windows build no longer fails to compile: `WinMain` stopped redeclaring the CRT
  `__argc`/`__argv` globals (they are macros on MSVC), which had broken the entire
  Windows-x64 build and, with it, every release.
- macOS build no longer risks a missing `PATH_MAX`: the updater now includes
  `<limits.h>` on Apple, matching the other modules.
- Own program entry point now cooperates with SDL2 (`SDL_MAIN_HANDLED` +
  `SDL_SetMainReady()`) so no `SDL2main` linkage is needed on Windows/macOS.
- Release targets a live x86_64 macOS runner (`macos-15-intel`); the previous
  `macos-13` runner was retired in December 2025 and failed the whole release.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.5.0 => version: 0.5.0).
  2. Commit UPDATE.md.
  3. git tag v0.5.0 && git push origin v0.5.0
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
