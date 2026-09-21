# Guard bypass and native discovery checkpoint

Updated: 2026-09-21. Research emulator only; no aircraft connected. The mode command remains unconfirmed on the wire.

## Working instrumentation

The old extracted DEX set was insufficient for the research APK: its final DEX had an invalid class-table reference and did not expose a usable `kotlin.jvm.internal.Lambda` definition. A new memory dump from the original process contained that definition. Sixteen application DEX files were rebuilt with corrected SHA-1/Adler-32 headers and the existing research manifest transformation (ordinary application/component factory; AppGuard provider removed).

Working local APK: `scratch/android_runtime/DJI-Fly-live-fixed.apk`. It uses fresh DEX from `scratch/android_runtime/live_dex/`, the original resources/native libraries, and the research signing key. Keep `resources.arsc` uncompressed and four-byte aligned: a compressed copy produced a misleading `INSTALL_FAILED_INSUFFICIENT_STORAGE` result, while logcat identified the resource-format violation. This is a research build, not a distributable application change.

Removing the AppGuard bootstrap alone did not permit sustained attachment. Blocking Java `Runtime.loadLibrary0`/`load0` calls whose library argument contains `msaoaidsec` did. The trace records `blocked_library=msaoaidsec`; subsequent SDK/socket-pair probes ran through the observation window. This establishes a working combination, not that every earlier exit was caused by that library. ARM-realm instrumentation still had intermittent process/connection failures.

Research helpers, all under ignored scratch:

- `live_probe.py`: spawn/attach, Java bridge, bounded observation, trace output.
- `block_msa.js`: suppress the `msaoaidsec` library load.
- `usb_mode_probe.js`: initialize SDK, create a socket pair, notify native USB with model `WM160`, capture the peer, request modes.
- `usb-discovery-baseline.log`: preserved successful baseline, PID 3423.

Do not overlap runs writing the same log. Stop the prior runner and use a separate output name before repeating. The user requires shutting down the emulator and research processes after work.

## Captured outgoing discovery bytes

PID 3423 reported `initialize=true`, `sdk=true`, `products=[]`, and descriptors 343/344. Later `native_IsDataLinkAvailable(343)` returned true. These bytes were read from the socket peer after `JNIUsbAccessory.native_OnUsbConnected(343, "WM160")`:

```text
550d0433020e39a44000016088
550d0433021f3aa440000137d3
550d043302a23ba4400001de2f
550d043302003ca44000019697
551204c702283da44000b70101000c00d5f5
```

The first four are APP `02` requests to addresses `0e`, `1f`, `a2`, and `00`, flags `40`, command `00/01`, empty payload. The fifth targets `28`, command `00/B7` (static capability request), payload `01 01 00 0c 00`. All five passed the repository's `DumlPacket.decode` length, CRC8, and CRC16 checks. These are discovery requests, **not mode commands**. At this boundary they appeared as raw DUML without a `55 cc` composite prefix; that does not establish framing after successful product discovery.

Mock `00/01` replies with hardware text `wm160` (raw) or `wm160_rc` (composite-wrapped) did not create a product. JNI mode values 0/1/2 still returned callback `-1`. No claim is made that those mock version responses were complete or correct.

## Native product identification

Reference library SHA-256: `017d65e3daacf290405339fde62d2ae49bbd5a4c6c3a2c29ee2338a5177a34c0`.

`ProductTypeHandler::TryUpdateInfo` starts at `0x4631e58`. The dispatch at `0x4631ff4` reads field `+0x0c`, subtracts 44, and uses the halfword jump table at `0x1685ebc`. The alternate dispatch reads field `+0x14`, subtracts 53, and uses the table at `0x1685f9e`. Both matching entries target `0x463201c`, setting category 0 and internal product type `0x3b` (59).

Field identity is established by the input methods:

- `OnReceiveCameraType`, `0x4632820`: stores the camera type in `+0x0c` for camera index 0. **Camera type 44 maps to UAV59.**
- `OnReceiveFCType`, `0x4632808`: stores the FC type in `+0x14`. **FC type 53 maps to UAV59.**

The earlier live mock used 44 as an FC type and did not discover a product. That experiment used the wrong enum domain. Initial FC=53 attempts encountered ARM attachment/process failures; the later resumed tests below successfully delivered the corrected value.

Frida enumerated the real `OPR59RCAbstraction::SetSoftSwitchMode` export at offset `0x27a986c`, matching the offline harness. Export availability does not establish execution: no successful hit with an outgoing mode frame was recorded in this checkpoint.

## Remaining work

Trace `JNIKeyValue.native_set` to the native key registry and RC abstraction for product 0. A connected callback and a ProductTypeHandler containing 59 are insufficient evidence that OPR59RCAbstraction was instantiated or registered. Correlate the flight-menu SoftSwitch request with actual setter execution before claiming an outgoing mode frame. The existing `06/59`, receiver FLYC, payload `00/01/02` candidate remains supported by the isolated serializer only. No beta source changes or commits were made.

## Resumed FC=53 and RC-component checks

The emulator was restarted after the user stopped QEMU. Delaying the emulated ARM attachment until five seconds after resume, installing its hooks 500 ms later, and initializing SDK/USB at nine seconds allowed these bounded checks to complete:

1. `usb_fc53_probe.js` + `native_fc53_probe.js`: delivered `OnReceiveFCType(handler, 53)` successfully. Three mode requests still returned `-1`; no mode-setter hook hit. Evidence: `scratch/android_runtime/fc53-checkpoint.log`.
2. `usb_connected_probe.js` + `native_connected_probe.js`: registered a real `UAVSDKManager$ProductConnectionCallback` before SDK initialization. It received **`connected(0)` before the FC mock**, while `sdk.g()` remained `[]`. After FC=53 the selected native handler already held connected byte 1, category 0, internal product type `0x3b`, and FC field `0x35`. Calling `UpdateInfo(true, 0, 59)` and `NotifyInfoChange` did not change those fields. JNI requests using the reported product ID 0 still returned `-1`.
3. `usb_rc59_probe.js` + `native_rc59_probe.js`: captured two `ProductManagerImpl::Setup` instances. The real `ConvertRCProductTypeToRCType(59)` returned 59; both managers received `UpdateRCType(manager, 0, 59)`. This was an explicit native mock, not a wire-derived RC identification. The three requests still returned `-1`, with no `SetSoftSwitchMode` entry hit.

These tests correct an earlier assumption: **an empty Java product list does not prove that no connected product exists**. They also narrow the remaining failure to a stage before the observed mode setter; the exact meaning/source of callback `-1` remains unresolved. No aircraft acknowledgement or flight-menu state change was tested.

Full traces are `usb_connected_probe.runtime.log` and `usb_rc59_probe.runtime.log` under `scratch/android_runtime/`. Do not treat mock connectivity/type notifications as proof of a complete WM160 handshake.

Session cleanup: DJI Fly was force-stopped, the emulator accepted `emu kill`, and the ADB server was stopped. Remaining research AVD launcher processes were checked and terminated where present. Restart the AVD, ADB, and Frida server before the next run.
