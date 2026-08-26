# DJI Link

<p align="center"><img src="docs/dji-link-logo.svg" alt="DJI Link" width="640"></p>

<p align="center">
  <strong>Fly a DJI Mavic Mini 1 from your computer.</strong><br>
  Live HEVC video · telemetry · spectator-style flight control · camera/gimbal · reverse-engineered DUML
</p>

<p align="center">
  <a href="https://github.com/Kolya080808/DJI-Link/releases/latest">Download</a> ·
  <a href="https://github.com/Kolya080808/DJI-Link/wiki/Getting-Started">Quick start</a> ·
  <a href="https://github.com/Kolya080808/DJI-Link/wiki">Wiki</a> ·
  <a href="https://github.com/Kolya080808/DJI-Link/discussions">Discussions</a>
</p>

<p align="center">
  <img src="https://github.com/Kolya080808/DJI-Link/actions/workflows/ci.yml/badge.svg" alt="CI">
  <img src="https://github.com/Kolya080808/DJI-Link/actions/workflows/lint.yml/badge.svg" alt="Lint">
  <img src="https://img.shields.io/badge/license-Apache--2.0-73f7c5.svg" alt="Apache-2.0 license">
</p>

> **The idea:** make the first-generation Mavic Mini feel like a programmable camera platform instead of a phone-only drone.

DJI Link is an open-source desktop ground station for the **DJI Mavic Mini 1 (WM160)**. A small Raspberry Pi acts as a USB accessory bridge to the stock remote controller; the PC handles the UI, DUML protocol, telemetry, video, and control.

There is no official DJI desktop SDK for the Mini 1. DJI Link therefore talks to the aircraft using the drone's own **DUML** protocol, reverse-engineered from DJI Fly and verified against real hardware.

> ⚠️ **Unofficial project.** Not affiliated with, endorsed by, or supported by DJI. Use only with hardware you own or are authorized to operate. Flight-control software can cause crashes, flyaways, damage, or injury. Read the [disclaimer](#disclaimer) before flight.

## Why this project is interesting

This is more than a remote-control GUI. It is a reproducible reverse-engineering project that turns a closed consumer drone link into a documented, scriptable desktop interface.

**What is already working:**

- **Live video** — H.265/HEVC stream decoded in the desktop app.
- **Telemetry** — attitude, altitude, speed, battery, flight mode, home state, GPS information where available, and decoded flight-block reasons.
- **Flight control** — takeoff, landing, RTH, control authority, and continuous virtual-stick input.
- **Spectator-style controls** — mouse to look/yaw, `W A S D` to move, `Space/Shift` for throttle.
- **Camera & gimbal** — photo, recording, zoom, exposure controls, and gimbal tilt.
- **Cross-platform desktop app** — Windows, Linux, and macOS release packages.
- **Raspberry Pi bridge** — Zero 2 W reference setup, automatic discovery, access point, and recovery tooling.
- **Simulator** — open the UI without aircraft hardware.
- **Reverse engineering corpus** — DUML command tables, telemetry notes, flight-gating research, parameter research, media research, and protocol captures.

## See it before you build it

No drone is required to inspect the desktop interface:

```bash
dji-link --sim --windowed
```

The simulator is the recommended first step for a new PC install. For the real hardware setup, use the **[Wiki → Getting Started](https://github.com/Kolya080808/DJI-Link/wiki/Getting-Started)** guide.

> **Demo note:** the repository intentionally does not ship a fake flight video. When a real recording is added, keep it short, label simulated footage clearly, and show the UI/HUD rather than pretending simulator telemetry is a real flight.

## How it works

```text
┌──────────────────────────────┐
│ PC                           │
│ C++ desktop app              │
│                              │
│ UI · DUML · telemetry        │
│ video · control · console    │
└──────────────┬───────────────┘
               │ Wi-Fi 2.4 GHz / TCP
               ▼
┌──────────────────────────────┐
│ Raspberry Pi Zero 2 W        │
│ Dumb USB accessory bridge    │
│ AP + discovery + bridge      │
└──────────────┬───────────────┘
               │ USB Cable / AOA
               ▼
┌──────────────────────────────┐
│ DJI Mavic Mini 1 remote      │
└──────────────┬───────────────┘
               │ DJI radio link
               ▼
┌──────────────────────────────┐
│ DJI Mavic Mini 1 / WM160     │
└──────────────────────────────┘
```

The important trick is the Raspberry Pi: the stock remote expects the mobile application to appear as a USB **accessory** (Android Open Accessory). A normal PC cannot be that USB accessory (usually, PC can be only USB host because it doesn't have OTG controller, but we need USB device), while the Pi can. The Pi forwards the raw byte stream; the PC remains the "brain".

See the deeper explanation in [Architecture](https://github.com/Kolya080808/DJI-Link/wiki/Architecture) and [Protocol Deep Dive](https://github.com/Kolya080808/DJI-Link/wiki/Protocol-Deep-Dive).

## Hardware

| Part | Requirement |
|---|---|
| Drone | DJI Mavic Mini 1 (WM160), activated once with the official app |
| Remote | Stock Mavic Mini remote controller |
| Bridge | Raspberry Pi Zero 2 W recommended; other Pi boards with USB OTG/peripheral mode may work |
| Storage | 8 GB+ SD card (16 GB recommended), Raspberry Pi OS Bookworm 64-bit |
| Cables | Data USB cable from Pi to the remote + separate Pi power |
| PC | Windows 10/11, Linux, or macOS |

The Pi is intentionally simple: it forwards the accessory stream, so it's just dumb bridge. The reference Zero 2 W has enough RAM for the bridge.

## Download

Use the latest GitHub Release; release packages include a matching `ffmpeg` runtime for live video.

| Platform | Package |
|---|---|
| Windows x64 | [MSI](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-x64.msi) · [ZIP](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-x64.zip) |
| Windows arm64 | [MSI](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-arm64.msi) |
| Windows x86 | [MSI](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-x86.msi) |
| macOS Apple Silicon | [DMG](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-macos-arm64.dmg) |
| macOS Intel | [DMG](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-macos-x86_64.dmg) |
| Linux x86_64 | [DEB](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-x86_64.deb) · [RPM](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-x86_64.rpm) |
| Linux arm64 | [DEB](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-arm64.deb) · [RPM](https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-arm64.rpm) |
| Raspberry Pi | [install-pi.sh](https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh) |

**Full first-time setup:** [Wiki → Getting Started](https://github.com/Kolya080808/DJI-Link/wiki/Getting-Started)

## First run

### PC

After installing:

**WINDOWS:**

Well, you know what to do: double-click the .exe file.

**LINUX/MACOS:**

```bash
dji-link --sim        # inspect the UI without hardware
dji-link              # normal GUI launch
dji-link --console    # headless / DUML console mode
```

### Raspberry Pi

On a freshly prepared Pi:

```bash
curl -fsSL https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh | sudo bash
```

The installer configures the USB gadget path, Wi-Fi access point, discovery service, bridge service, and update timer.

For the actual wiring, first boot, Wi-Fi behavior, rescue procedure, and service logs, use the [Raspberry Pi Wiki page](https://github.com/Kolya080808/DJI-Link/wiki/Raspberry-Pi) rather than duplicating those instructions here.

## Controls

| Input | Action |
|---|---|
| Mouse | Look / yaw + gimbal tilt |
| `W A S D` | Horizontal movement |
| `Space` / `Shift` | Up / down |
| `Enter` | Arm / disarm |
| `T` | Takeoff |
| `L` | Land |
| `H` | Return-to-home |
| `P` | Photo |
| `R` | Start / stop recording |
| `K` | Request video keyframe |
| `Esc` | Settings |
| `F1` | Help overlay |
| `F3` | HUD on/off |
| `F11` | Fullscreen |
| `Tab` | DUML console |

For flight workflow and safety, read [How to Fly](https://github.com/Kolya080808/DJI-Link/wiki/How-to-Fly) before enabling control.

## Roadmap

DJI Link is growing in major steps rather than trying to ship every experimental idea at once.

### 2.0.0 — Media + controllers

The next major release is planned to complete the **media side** of the project and make the desktop experience easier to use with a physical controller. That includes broader media operations and recovery, plus configurable support for **PS/Xbox-style gamepads** alongside keyboard and mouse input.

### 3.0.0 — AI-assisted features

The longer-term direction is to explore **AI-assisted capabilities**: bringing useful features found on newer drones to the Mini where technically possible, and experimenting with capabilities that DJI never shipped for this aircraft. Possible areas include camera/media assistance, scene understanding, higher-level automation, and intelligent diagnostics.

These are roadmap goals, not promises. Anything flight-critical will remain explicit, bounded, and user-controlled; experimental AI features will be clearly separated from the deterministic control stack.

For the detailed contributor-facing roadmap, see [`ROADMAP.md`](ROADMAP.md).

## Reverse engineering

The project documents how it moved from the DJI Fly APK to a working desktop client:

1. Recover the DUML frame format and checksums.
2. Reconstruct command tables and high-level semantics.
3. Follow the Android app from MSDK concepts down to native packers.
4. Build the USB accessory path through a Raspberry Pi.
5. Split the composite stream into DUML and HEVC video.
6. Verify stateful flight-control, camera, telemetry, and parameter behavior on WM160 hardware.
7. Port verified behavior from the experimental Python implementation into C++.

The [Wiki → Reverse Engineering](https://github.com/Kolya080808/DJI-Link/wiki/Reverse-Engineering) page explains the methodology and evidence levels. The complete technical record stays in [`dji_link_beta/reverse_docs/`](dji_link_beta/reverse_docs/).

## Current status & roadmap

The native C++ client intentionally exposes only protocol paths that are sufficiently understood and tested. Media list/download/delete, *some (not all)* GPS parsing, and parts of the parameter/research surface remain incomplete or evolving.

See [`ROADMAP.md`](ROADMAP.md) for the current contributor-facing roadmap and [`UPDATE.md`](UPDATE.md) for release notes.

Good areas for contributions include:

- media research — this is currently the biggest unknown in DJI-Link;
  anything about media/storage protocols, commands, file transfer,
  metadata, camera/media workflows, packet captures, or findings from
  other DJI platforms is especially valuable. The media side is largely
  unexplored right now, so research is much more useful than implementation;
- AI research and ideas — this is a longer-term direction for the 3.x
  roadmap. Ideas, experiments, references, and research into how AI could
  add capabilities missing from the Mavic Mini (but not missing from the Mavic 1 or 2) are very welcome;
- Xbox controller testing — PS controller support can be tested locally,
  but I do not currently have an Xbox controller, so testing and mapping
  on Xbox hardware would be especially useful;
- documentation — the project is growing fast and the documentation is
  becoming difficult to keep consistent. Help reviewing, correcting,
  restructuring, and cross-linking the Wiki and reverse-engineering notes
  is very welcome;
- ideas for future development — suggestions for useful, unusual, or
  technically interesting features are encouraged, especially ideas that
  could bring capabilities found on newer DJI drones to the Mavic Mini;
- verified protocol research — new packet captures, protocol discoveries,
  hardware/firmware observations, and independently reproducible findings
  are valuable even when they do not come with an implementation.

## Development

Native development uses C++20 + CMake:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

Use the simulator for UI work without aircraft hardware:

```bash
./build/dji-link --sim --windowed
```

See [Wiki → Development](https://github.com/Kolya080808/DJI-Link/wiki/Development) and [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Safety & disclaimer

**Use at your own risk.** DJI Link is an unofficial independent project and is not affiliated with, endorsed by, or supported by DJI. Controlling a real aircraft with unofficial software can cause crashes, flyaways, damage, injury, or loss of the aircraft.

- Test new builds with the aircraft secured and propellers removed whenever possible.
- Keep the official remote available as a manual fallback.
- Do not use the project to bypass safety geofencing or other protections.
- Obey all applicable registration, altitude, airspace, and line-of-sight rules.
- Use only hardware you own or are authorized to operate.

"DJI", "Mavic", "Mini", and "DJI Fly" are trademarks of their respective owners and are used for identification only.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

---

<p align="center">
  <strong>Built for people who want to know what their drone can do.</strong><br>
  <a href="https://github.com/Kolya080808/DJI-Link/wiki/Getting-Started">Get started</a> ·
  <a href="https://github.com/Kolya080808/DJI-Link/wiki/Reverse-Engineering">Read the research</a> ·
  <a href="https://github.com/Kolya080808/DJI-Link/issues">Help improve DJI Link</a>
</p>