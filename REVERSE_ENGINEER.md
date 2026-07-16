# Reverse-engineering the DJI Mavic Mini 1

How DJI Link was built — from an APK to full PC control of a WM160. This is the readable
overview; the exhaustive tables live in
[`dji_link_beta/reverse_docs/`](dji_link_beta/reverse_docs/).

## The idea

DJI sells a "Ground Station" (waypoints, automation, PC control) only for its industrial
drones. But the consumer app and the drone speak the **same protocol** their industrial
gear uses — **DUML** — and the app carries the whole command table. So in principle a PC
can send the same commands. The whole project is turning "in principle" into a working
app for the smallest drone DJI makes.

## Step 1 — the protocol (DUML)

DUML frames have a fixed shape:

```
55 | len(10b)+ver(6b) | crc8 | sender | receiver | seq | type | cmd_set | cmd_id | payload | crc16
```

The two CRCs use non-standard seeds (CRC-8 seed `0x77`, CRC-16 seed `0x3692`), recovered
from `libsdk_jni.so` and confirmed byte-for-byte against real frames (the `GetVersion`
frame `55 0d 04 33 …`). Every device has an address: PC `0x0a`, remote `0x06`, flight
controller `0x03`, gimbal `0x04`, camera `0x01`, battery `0x0d`.

## Step 2 — the command table

The command definitions were not buried in obfuscated code — they sat in a **standalone,
un-obfuscated library** inside the app, listing every command plainly. That is what made
the project feasible: 436 commands across 29 command sets, each with its payload layout.

The app's own Java, however, was packed (AppGuard/Bangcle). The loader carried a 165 MB
file posing as a single native library that was actually **16 concatenated DEX files**
with wiped magic bytes and scrambled string tables. Repairing the headers reconstructed
all 16 — decompiling the entire app (≈128k classes) and exposing the app-flow logic:
activation, calibration, flight gating, and the diagnostic system.

## Step 3 — reaching the drone

This was the hard part. Three physical paths were tested:

- **Remote controller over USB serial** — the remote exposes a serial port, but it turned
  out to forward our commands only one way and never route the drone's replies back.
- **Drone's own USB port** — gives two-way telemetry and even makes the gimbal physically
  move (the first proof the reversed commands were real), but it is tethered and carries
  no video.
- **Posing as the phone (AOA)** — the phone connects to the remote as a USB *accessory*
  (Android Open Accessory). This is the only path that carries **everything**: video,
  telemetry, and untethered control.

A PC's USB is always a host and cannot pose as an accessory, so a **Raspberry Pi Zero 2 W**
does it instead — it presents itself to the remote as the phone and forwards the raw byte
stream to the PC. Bringing this up meant emulating a USB device from userspace with the
Linux `raw_gadget` driver, completing the AOA handshake (`GET_PROTOCOL` / `SEND_STRING` /
`START`), and re-enumerating as the accessory. When it worked, the remote introduced
itself: `DJI / WM160 / MR1SD25 / fw v4.2.2.60`.

## Step 4 — the composite stream

The raw AOA pipe is not plain DUML — it's a **composite mux**: a stream of units, each
`55 CC | type(u16) | length(u32) | payload`. Type `0x5749` is DUML, `0x574A` is video, and
`0x574B` (found by accident) is the remote's own ASCII debug log. Demultiplexing this on
the PC is what turns one byte stream back into telemetry, video, and command replies.

## Step 5 — the video codec

The video units were assumed to be H.264, and nothing decoded. Measuring the stream told a
different story: every packet started `00 00 01 02 01 …`, there were no H.264 keyframes
anywhere, and the entropy was near-maximum. Read as **H.265/HEVC** instead, it parsed
cleanly — the leading bytes are a valid HEVC NAL header, six slices per frame. The stream
also carries no keyframe of its own (intra-refresh), so the client asks for one explicitly
(`0x02/0xB3`) and re-injects the cached parameter sets. Feeding it to ffmpeg as HEVC
produces a picture.

## Step 6 — flight and its gates

Reversing the flight logic answered the questions that decide whether a PC can actually
fly the drone:

- **Login is not required per flight.** The only account gate is a one-time cloud
  *activation*, persisted in the flight controller. An already-activated drone flies with
  no account. (Account login is only needed for no-fly-zone unlocks.)
- **Takeoff, land, RTH, arm/disarm, calibration, and set-home** are all one multiplexed
  command (`0x03/0x2A`) with a one-byte function code; the full enum is resolved.
- **Max altitude and distance** can be set directly (`0x03/0x2D`), up to DJI's own 500 m
  ceiling, with no unlock. **Speed** and other tuning parameters are addressed by a hash
  of the parameter name that is computed inside the packer and can't be recovered
  statically — so those need a one-time runtime capture.
- **Virtual-stick** control is officially supported on the Mini since DJI's mobile SDK
  4.13; the app has three candidate stick encodings, and which the WM160 firmware accepts
  is settled on real hardware.

## Step 7 — diagnostics

The app shows human-readable reasons for failures (e.g. "unable to take off — no
satellite positioning"). Those texts turned out to be **fully local**, not fetched from a
server: an alarm-id maps through a bundled config to a diagnostic code, and the code maps
to a string resource. Recovering that chain yielded **743 diagnostic codes with English
text** — so the PC app can explain exactly why the motors won't start, just like the phone.

## What can't be done offline

- Writing arbitrary flight-controller parameters (speed, gains) needs the name→hash, which
  requires a runtime capture.
- DJI's server walls — no-fly-zone/geo unlock, first activation of a factory-reset unit,
  anti-theft binding — need DJI's servers and can't be replicated offline.
- The Mini 1 has no obstacle sensors, so any automated flight is blind.

---

*Reverse engineered for interoperability with the owner's own hardware. See the
[Disclaimer](README.md#disclaimer).*
