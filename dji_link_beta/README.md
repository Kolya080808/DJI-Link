# DJI Mavic Mini 1 (WM160) — PC control. BETA

Project goal: **from the PC, do everything the phone (DJI Fly) and the remote controller do**,
and pilot the drone from the keyboard like Minecraft spectator mode (WASD/Space/Shift),
with room to scale — hooking up a neural network (person tracking, obstacle avoidance)
as another command source.

Target — **DJI Mavic Mini 1 = model `WM160`** (one of the strings in `accessory_filter.xml`).

## Architecture (layers are replaceable)
```
command source:  keyboard  │  neural net  │  mission script
                       └─────────┼──────────┘
Drone API:            drone.py   set_sticks / takeoff / land / photo / telemetry
protocol:             duml.py    DUML codec (CRC8/CRC16, encode/decode, stream)
transport:            transport.py   AOA-USB │ MITM proxy │ loopback log
input handling:       control.py  WASD/Space/Shift -> virtual sticks
AOA handshake:        aoa.py      pretend to be DJI/com.dji.logiclink
```

## Files
| file | layer |
|------|------|
| `duml.py` | DUML codec (checked against the real DJI header `55 0d 04 33`) |
| `transport.py` | channel abstraction: `LogTransport`, `AoaTransport` |
| `aoa.py` | Android Open Accessory handshake on the host side |
| `control.py` | keyboard → sticks, game loop (pygame) |
| `drone.py` | high-level `Drone` API (remote controller + app functions) |
| `dji_accessory.py` | CLI: `--selftest` / `--scan` / `--keyboard` / full run |

## What you can check RIGHT NOW (without a drone)
```bash
python3 dji_accessory.py --selftest
```
Checks the whole stack offline: CRC tables, that the GetVersion frame is byte-for-byte equal
to the real DJI one (`55 0d 04 33 ...`), codec roundtrip, streaming parse, and the chain
**WASD → sticks → DUML → transport**.

```bash
pip install pygame
python3 dji_accessory.py --keyboard        # control window, frames go to the loopback log
```
A window opens: press WASD/Space/Shift/Q,E — at the bottom you can see the axes and the DUML frame being built.
Hotkeys: T takeoff, L land, H RTH, P photo, R/F record, X emergency stop.

## What is already confirmed by APK reversing
- `res/xml/accessory_filter.xml`: `DJI / com.dji.logiclink`, `WM160`, `com.dji.link`.
- Transport is **AOA** (phone=USB device, DJI hardware=USB host). Strings in
  `lib/arm64-v8a/libsdk_jni.so`: `AoaServicePort`, `UsbDatalinkMgr`,
  `JNI_LoadUsbAccessory`, `[AOA]onUsbConnected fd = `.
- On top of the channel — **DUML** (`0x55`, header-CRC8 seed `0x77`, frame-CRC16 seed
  `0x3692`), verification string: `package crc verify fail, cmdset %d, cmdid 0x%X`.

## HONEST take on the beta's boundaries (what stands between "demo works" and "drone flies")
Done and verifiable: layered architecture, DUML codec (real frame format),
AOA handshake, keyboard→sticks, `Drone` API with methods for all functions.

Still NOT done (these are the next steps, requiring traffic capture/further reversing):
1. **Exact DUML commands for WM160.** `cmd_set/cmd_id/payload` for sticks, takeoff,
   camera etc. are currently structural STUBS. They need to be captured from real traffic
   (MITM phone↔remote controller) or reversed further out of `libsdk_jni.so`. The frame structure is correct —
   only the codes/field layout change, one command at a time.
2. **How the PC physically reaches the drone.** Mavic Mini 1: phone↔remote controller over USB (AOA),
   remote controller↔drone over "enhanced Wi-Fi" (radio). To replace the remote controller TOO, you need a way to reach
   the drone: options — (a) MITM on the USB between phone and remote controller and
   inject commands (the remote controller stays a radio bridge); (b) PC directly over Wi-Fi to the drone;
   (c) SDR/your own radio part. This determines what is even feasible with this APK alone.
3. Video/telemetry (H.264 stream, parsing of state payloads) — a separate layer.
