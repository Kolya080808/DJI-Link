# dji_link_beta — the DJI Link application

The PC-side app and the code that runs on the Raspberry Pi bridge. For setup, hardware,
and usage see the [top-level README](../README.md); this file documents the modules for
anyone working on the code.

## Run
```bash
python pc_client.py          # connect and fly (auto-discovers the Pi)
python pc_client.py --sim    # no hardware, exercise the UI
python full_test.py          # scripted checks + motor-won't-start diagnostics
```

## Layers

```
input:        control.py     keyboard/mouse -> stick axes
app:          pc_client.py   window: video + telemetry HUD + control + settings + console
drone API:    drone.py       Drone: takeoff/land/rth, sticks, gimbal, camera, limits, home
telemetry:    telemetry.py   OSD/state push -> OsdState;  diag_codes*.py  fault-code text
protocol:     duml.py        DUML codec (CRC8 seed 0x77, CRC16 seed 0x3692)
mux:          composite.py   AOA composite stream <-> DUML / video units
video:        (in pc_client) HEVC payloads -> ffmpeg -> RGB frames in the window
transport:    transport.py   NetTransport (to the Pi), CompositeTransport, SerialTransport, LogTransport
discovery:    netfind.py     find the Pi on the LAN or join its access point
```

## Modules

| file | purpose |
|------|---------|
| `pc_client.py` | the application — everything below wired into one window |
| `drone.py` | high-level command API (all reversed DUML commands) |
| `duml.py` | DUML frame encode/decode, verified against real frames |
| `composite.py` | AOA composite mux demux/wrap |
| `telemetry.py` | decode the FC state push into readable fields |
| `diag_codes.py` / `diag_codes_full.py` | fault-code names and 743 diagnostic-code texts |
| `control.py` | map held keys to stick axes |
| `transport.py` | swappable transports |
| `netfind.py` | PC-side Pi discovery |
| `flyc_param_infos.json` | 687 flight-controller parameters (limits, gains) |
| `pi/` | code that runs on the Pi (see `pi/README.md`) |
| `reverse_docs/` | the reverse-engineering write-ups |

The other scripts (`probe_serial.py`, `read_sticks.py`, `monitor_serial.py`, `checks.py`,
`gimbal_demo.py`, `video_liveview.py`, etc.) are standalone diagnostics from bring-up over
the serial/USB paths; the AOA path through the Pi supersedes them for normal use.

## Video note

WM160 liveview is **H.265/HEVC** (not H.264): the composite video units are plain Annex-B
with no extra header. The stream has no periodic keyframe of its own, so the client sends
an I-frame request (`0x02/0xB3`) on connect and re-injects cached VPS/SPS/PPS.

## Reverse-engineering docs

`reverse_docs/` — `MASTER_REPORT.md` (overview), `FLIGHT_GATING.md` (what's needed to fly:
login, calibration, modes, home point, limits, virtual stick), `ERROR_CODES.md`,
`TELEMETRY_TABLE.txt`, and the command tables.
