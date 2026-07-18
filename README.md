# DJI Link

**Fly a DJI Mavic Mini 1 (WM160) from your computer** — live video, telemetry, and full
flight control from the keyboard and mouse, without the phone app. A Raspberry Pi acts as
a small bridge to the remote controller; the PC does everything else.

There is no official desktop SDK for the Mini 1, so DJI Link is built directly on the
drone's own **DUML** protocol, reverse-engineered from the DJI Fly app.

> ⚠️ Unofficial project, not affiliated with DJI. Use at your own risk — see
> [Disclaimer](#disclaimer). For your own hardware only.

<!-- Demo: replace with a real screenshot/GIF once recorded -->
<!-- ![DJI Link in flight](docs/demo.gif) -->

---

## Features

- **Live video** from the drone (H.265/HEVC), decoded into the app window.
- **Telemetry** — attitude, altitude, speed, battery, GPS/satellites, flight mode, and
  plain-language reasons when the motors refuse to start (743 decoded diagnostic codes).
- **Flight control** — takeoff, land, return-to-home, arm/disarm, and continuous **virtual-stick
  flight** (hardware-verified: `0x03/0x8E` DataFlycJoystick; spectator-style — mouse to look/turn,
  WASD to move, Space/Shift for throttle). Control auto-enables once the takeoff settles, and hands
  back to the remote on release. Flight modes Cine/Normal/Sport.
- **Gimbal & camera** — tilt with the mouse, photo, record (R toggles), zoom, ISO/shutter/EV.
- **Settings panel** (Esc) — max altitude and distance (up to the drone's 500 m ceiling,
  no unlock needed), home point (current or explicit GPS), exposure, camera mode.
- **Console** — send any raw DUML command; the entire reversed command surface is reachable.
- **Zero-config launch** — `python pc_client.py` finds the Pi, connects, and walks you
  through powering on the link.

---

## How it works

```
[ PC — the brain, Python ]  --Wi-Fi-->  [ Pi — dumb bridge ]  --USB-->  [ remote ]  )))  [ drone ]
   video · telemetry · control                (forwards bytes)
```

The drone and remote talk over DUML, and the phone app connects to the remote as a USB
**accessory** (Android Open Accessory). A normal PC can only be a USB *host*, so it can't
pose as that accessory — a Raspberry Pi can. The Pi presents itself to the remote as the
phone and forwards the raw byte stream to the PC over Wi-Fi. All protocol handling —
DUML framing, the composite AOA mux, video reassembly, control — happens on the PC.

Full protocol write-up: **[`dji_link_beta/reverse_docs/`](dji_link_beta/reverse_docs/)**
(`MASTER_REPORT.md`, `FLIGHT_GATING.md`, `ERROR_CODES.md`, telemetry and command tables).

---

## Hardware

| Part | Requirement |
|------|-------------|
| Drone | DJI Mavic Mini 1 (WM160), already activated once with the official app |
| Remote | The Mavic Mini remote controller |
| Bridge | Raspberry Pi **Zero 2 W** (any Pi with USB OTG / `dwc2` peripheral mode works) |
| SD card | ≥ 8 GB (16 GB recommended), Raspberry Pi OS (Bookworm, 64-bit) |
| Cables | A **data** USB cable from the Pi's USB port to the remote (the phone cable); separate power for the Pi |
| PC | Windows/Linux/macOS with Python 3.9+ and ffmpeg |

The Pi only forwards bytes, so 512 MB RAM (the Zero 2 W) is plenty.

---

## Install

### PC
```bash
pip install pyserial pygame          # or: py -m pip install pyserial pygame
# ffmpeg (for video):
#   Windows:  winget install ffmpeg
#   macOS:    brew install ffmpeg
#   Linux:    sudo apt install ffmpeg
```

### Raspberry Pi
Flash Raspberry Pi OS, enable SSH, then on the Pi:
```bash
git clone https://github.com/Kolya080808/DJI-Link.git
cd DJI-Link/dji_link_beta/pi
sudo bash setup_pi.sh https://github.com/Kolya080808/DJI-Link.git --service
sudo reboot
```
`setup_pi.sh` installs the build tools, enables the USB gadget (`dwc2`), builds the
`raw_gadget` kernel module (the Pi kernel ships it disabled), and installs a service so
the bridge starts on boot. It must be re-run after a kernel upgrade.

---

## Usage

On the PC, with the Pi powered and the drone + remote on:
```bash
python pc_client.py           # finds the Pi, connects, starts video + control
python pc_client.py -v        # same, with verbose logging
python pc_client.py --sim     # no hardware — try the interface
```

### Controls
| Input | Action |
|-------|--------|
| Mouse | Look / turn (yaw) and tilt the gimbal — spectator style |
| W A S D | Forward / left / back / right |
| Space / Shift | Up / down (throttle) |
| Enter | Arm / disarm |
| T / L / H | Takeoff / land / return-to-home |
| P / R | Photo / record |
| Esc | Settings panel |
| Tab | Console (any DUML command) |

Motors will not start until you **arm** (Enter). Keep the propellers off until you trust
the setup.

---

## Project layout

- **`dji_link_beta/`** — the PC application and tools
  - `pc_client.py` — the app (video, telemetry, control, settings, console)
  - `drone.py` · `duml.py` · `composite.py` · `telemetry.py` · `diag_codes.py` · `control.py` · `transport.py`
  - `pi/` — code that runs on the Raspberry Pi (`bridge.py`, `aoa_device.py`, `raw_gadget.py`, `netctl.py`, `setup_pi.sh`)
  - `reverse_docs/` — the reverse-engineering documentation
- **`decompiled/`** — the DJI Fly app, unpacked (source material for the research)

---

## Limitations

- **Speed and other flight-controller parameters** are addressed by a hash of the
  parameter name, computed inside the app's packer and not recoverable from static
  analysis — so setting max speed needs a one-time runtime capture of the hash. Max
  altitude and distance do **not** need this and work directly.
- **DJI account walls** (offline-unreachable): no-fly-zone / geo unlock, first-time
  activation of a factory-reset unit, and anti-theft binding all require DJI's servers. An
  already-activated drone flies without any of them.
- The Mini 1 has **no obstacle sensors**, so any automated flight is inherently blind.

---

## Disclaimer

**Use at your own risk.** This is an unofficial, independent project, **not affiliated
with, endorsed by, or supported by DJI**. It is provided "as is", without warranty.

- Controlling a drone with unofficial software can cause crashes, flyaways, damage, injury
  or loss of the aircraft. The authors are **not liable** for any damage or loss.
- Bypassing the official app/remote or changing flight-controller settings **may void your
  warranty**.
- **You are responsible for obeying the law** — registration, no-fly zones, altitude and
  line-of-sight rules vary by country. Do not use this to bypass safety geofencing.
- Use only on hardware you own or are authorized to operate.

"DJI", "Mavic", "Mini", and "DJI Fly" are trademarks of their respective owners, used here
for identification only.

## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE).
