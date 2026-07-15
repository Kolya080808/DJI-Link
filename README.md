# DJI Link - DJI Mavic Mini 1 (WM160) — PC Control

Reverse-engineering the DJI Fly app to **fly a DJI Mavic Mini 1 from a computer** —
keyboard (WASD) now, a neural net later — replacing both the app and the physical
sticks, with live video and telemetry. **The PC is the brain**; a Raspberry Pi Zero 2 W
is a thin bridge to the remote controller.

There is no official SDK for the Mini 1 (WM160), so everything here is built on top of
the reverse-engineered **DUML** protocol.


## Disclaimer

**Use at your own risk.** This is an unofficial, independent project, **not affiliated
with, endorsed by, or supported by DJI** in any way. It is provided "as is", without any
warranty (see the LICENSE).

By using this software or following anything in this repository, you accept that:

- **You do it entirely at your own risk.** Controlling a drone with unofficial software
  can cause crashes, flyaways, property damage, injury, or loss of the aircraft. The
  authors are **not liable** for any damage or loss of any kind.
- **You will likely void your warranty.** Bypassing the official app/remote, sending raw
  DUML commands, or altering flight-controller parameters may void the manufacturer's
  warranty and could damage the drone.
- **You are responsible for obeying the law.** Drone flight, radio use, and airspace
  rules vary by country. It is **your** responsibility to comply with all local laws and
  regulations (registration, no-fly zones, altitude/line-of-sight limits, etc.). Do not
  use this to bypass safety geofencing or fly where flight is prohibited.
- **Authorized hardware only.** Use this only on hardware you own or are explicitly
  authorized to operate. The reverse engineering here was done for interoperability with
  the owner's own device.

All product names, trademarks, and registered trademarks (including "DJI", "Mavic",
"Mini", "DJI Fly") are property of their respective owners and are used for
identification only.

## Why
DJI ships "Ground Station" (waypoint/automation) only for its industrial drones. The
consumer app and the drone talk to each other over DUML, and the app itself carries the
full DUML command table — so the same commands can be driven from a PC.

## How it works
```
[PC: brain, Python] --Wi-Fi/composite--> [Pi Zero 2 W: dumb jump-host] --USB/AOA--> [remote] ))) [drone]
   video + telemetry + WASD                    (just pumps bytes)
```
Channels (all verified on hardware):
- **Remote over serial (COM4)** — commands to the drone, one-way, no replies/video.
- **Drone over USB (COM5)** — two-way telemetry, but tethered by cable, no video.
- **Pi posing as the phone (AOA)** — everything at once: video + telemetry + untethered flight.

The catch that forced the Pi: replies, live camera view, and the flight-restriction
reasons all travel over **AOA (Android Open Accessory)**, where the phone is a USB
*device*, not a host. A PC's USB is always a host, so it cannot pose as the accessory —
hence a small board (the Pi) that can. The Pi does no parsing; it only forwards raw
bytes both ways. All protocol work happens on the PC.

## Layout
- **`dji_link_beta/`** — the PC brain plus tools and tests:
  - `duml.py` protocol · `composite.py` AOA mux · `liveview.py` H.264 video
  - `telemetry.py` + `diag_codes.py` telemetry/diagnostics · `drone.py` all commands
  - `control.py` WASD→sticks · `transport.py` (Serial/Net/AOA/Composite)
  - **`pc_client.py`** — one client: video + telemetry + WASD + console (any DUML command)
  - **`full_test.py`** — exercises every function + "find the culprit" diagnostics (why motors won't start)
  - helpers: `probe_serial/read_sticks/monitor_serial/checks/test_all/gimbal_demo/video_liveview/video_probe`
  - `flyc_param_infos.json` — 687 flight-controller parameters
  - **`pi/`** — code for the Pi (jump-host): `aoa_device.py`, `bridge.py`, `raw_gadget.py`, `setup_gadget.sh`
  - **`reverse_docs/`** — the full write-up: `MASTER_REPORT.md`, `TELEMETRY_TABLE.txt` (371 fields), command maps (436 cmds), the unpacked app
- **`decompiled/`** — the DJI Fly APK unpacked and reconstructed (kept for analysis, see its own README)
- **`scratch/`** — throwaway working files (not part of the project)

## Install (PC, Windows)
```
py -m pip install pyserial pygame
winget install ffmpeg        # for video (ffplay)
```

## Run
```
py -3 pc_client.py --sim              # no hardware (check UI/controls)
py -3 pc_client.py --serial COM5      # drone over USB (telemetry)
py -3 pc_client.py --pi <ip> --live   # via the Pi (video + telemetry + flight)
py -3 full_test.py                    # all tests + diagnostics
```
Controls: WASD pitch/roll · Space/Shift throttle · Q/E yaw · hotkeys (T takeoff,
L land, H RTH, P photo, …) · Tab opens a console (`raw <set> <id> <hex>` = any command).
Flight only in `--live` with ARM, and **with the props off**.

## Status
- ✅ DUML protocol (436 commands), telemetry (371 fields), liveview video (H.264),
  control (takeoff/land/RTH/sticks/gimbal/camera), AOA mux, PC client — decoded and coded.
- ✅ App fully unpacked (128k classes). Confirmed: **virtual stick works on the Mini 1** (MSDK 4.13+).
- ⏳ Left: Pi bring-up (once it arrives), first flight, lifting limits (needs Frida for the param-name hash).

## Limits (honest)
- **Writing FC parameters** (limits/speed/weak-GPS override) is keyed by a hash of the
  parameter name; the hash is behind the app's packer, so it needs a **Frida** runtime
  (capture one name→hash at runtime, then brute-force the rest).
- **DJI walls** (not doable offline): NFZ/geo unlock, activation of a factory-reset unit,
  motor-lock / anti-theft binding — all need DJI's server + account + signature. An
  already-activated drone flies without any of this.
- Autonomous follow is possible in principle (sticks + gimbal from the PC) but there are
  **no obstacle sensors**, so it is risky.

Full decode: **`dji_link_beta/reverse_docs/MASTER_REPORT.md`**.


## License

Licensed under the **Apache License 2.0** — see [LICENSE](LICENSE). The license includes
an explicit "AS IS", no-warranty and limitation-of-liability clause.
