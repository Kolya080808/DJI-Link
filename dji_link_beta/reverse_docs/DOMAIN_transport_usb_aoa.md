# DOMAIN: transport_usb_aoa — AOA/USB transport to the RC (WM160 / Mavic Mini 1)

Scope: how DJI Fly opens the USB accessory (AOA), the composite/DUSS mux that carries DUML +
video + other channels over the two AOA bulk pipes, the channel `type` values, the keepalive /
health-check, and reconnect. Everything here is filtered to **WM160 = Mavic Mini 1 = UAV59**.
This is the exact channel our Pi bridge replaces (Pi pretends to be the phone).

All evidence is cited to smali. Classes live in `unpacked_app_dex/classes_0451d00c.dex`
(baksmali'd for this doc). The app is packed, so the **runtime** read/write of the AOA pipe is in
native code; the Java classes below are the readable, byte-for-byte mirror of that native path
(see §11 for the Frida targets).

---

## 0. TL;DR / most important findings

- WM160 telemetry+video+control ride a **single USB pipe in Android Accessory (AOA) mode** between
  the phone (→ our Pi) and the RC. There is **no VID/PID match** — AOA matches on the accessory's
  **manufacturer/model strings**. For WM160 those strings are **`manufacturer="DJI"`,
  `model="WM160"`** (also `com.dji.link`, `com.dji.logiclink`). Source:
  `res/xml/accessory_filter.xml`.
- Over that pipe runs a **composite mux ("DUSS")**: each unit is
  `55 CC | type(u16 LE) | length(u32 LE) | payload[length]` — an **8-byte header** then the body.
  Confirmed in Java on both RX and TX (magic bytes `Pack.s=0x55`, `Pack.t=0xCC`; header field-len
  `= 6` after the 2 magic bytes). `type` and `length` are **little-endian**.
- **11 channel `type` values exist** (the report/`composite.py` only listed 6). Full table in §5.
  `0x5749` = DUML (commands+telemetry), `0x574A/574D/574B/574E/5750/5758/5759` route to video,
  `0x7530` = DUML (extended/aux), etc.
- The link is kept alive by polling **`native_IsDataLinkAvailable(fd)` every 2000 ms**; **>5**
  consecutive failures ⇒ tear down + reconnect. There is no application heartbeat frame at the
  transport layer — health is measured on the pipe itself.
- Needs a live capture (§11): exact native symbols behind `libAppGuard`/JNI, and confirmation of
  which of the two code paths (legacy Java P3 vs V5 native datalink bridge) actually services
  WM160 at runtime. Static evidence says **V5 native datalink** is the live one; the Java P3 stack
  is the spec.

---

## 1. Physical layer: Android Open Accessory (AOA)

WM160 does **not** appear as a CDC/serial device to the phone. The RC enumerates the phone into
**AOA accessory mode**, and the app talks to it through `UsbManager.openAccessory()`.

Manifest wiring (`decompiled/AndroidManifest.xml`):
```
<uses-feature android:name="android.hardware.usb.accessory" android:required="true"/>
<uses-library android:name="com.android.future.usb.accessory"/>
Activity com.dji.component.application.activity.DJIAoaActivity
   intent-filter action android.hardware.usb.action.USB_ACCESSORY_ATTACHED
   meta-data  @xml/accessory_filter
```

`res/xml/accessory_filter.xml` — the accessory identity strings the app will bind to:
```
<usb-accessory manufacturer="DJI" model="com.dji.logiclink" />
<usb-accessory manufacturer="DJI" model="WM160" />          <-- Mavic Mini 1
<usb-accessory manufacturer="DJI" model="com.dji.link" />
```
Interpretation: the RC (as USB host / AOA initiator) sends AOA identity strings; the phone
re-enumerates in accessory mode and the app matches these strings. **For our Pi to be accepted the
Pi (acting as the phone-side USB gadget) must present/negotiate the AOA session for
`manufacturer="DJI"`, and the RC advertises `model="WM160"` (or `com.dji.link`).** No numeric
VID/PID gating anywhere in Java — `ProductUsbInfo` holds only two Strings (manufacturer, model),
not IDs.

### 1.1 Opening the pipe → a raw fd → two streams
`uav/sdk/datalink/usb/UAVUsbAccessoryReceiver` (V5 path, 3581 lines):
- `g()` validates `UsbAccessory.getModel()/getManufacturer()` (lines 316/324/580) and requests the
  runtime permission with action string **`com.uav.v4.accessory.USB`** (lines 734/767).
- `t()` (line 1920) does the actual open:
  `UsbManager.openAccessory(acc) → ParcelFileDescriptor` (line 2009), stored in field `e`.
- `j()` (line 951) takes `ParcelFileDescriptor.getFd()` (line 969) and hands the **raw fd + model
  string** to native: `JNIUsbAccessory.native_OnUsbConnected(int fd, String model)` (line 1170).
  It also registers the model with `DataLinkBridgeServerManager.m(model)` (line 1115).

So in the **V5 datalink path the AOA read/write + composite demux happen in native code**, keyed by
the fd. The Java parser stack in §3–§7 is the **legacy "P3" implementation of the identical wire
format** and is what we read to recover the bytes.

### 1.2 Legacy P3 path (same bytes, in Java)
`uav/midware/usb/P3/UsbAccessoryService` wraps the same accessory fd in
`mAoaInputStream:Ljava/io/InputStream` / `mAoaOutputStream:Ljava/io/OutputStream`
(set via `access$002/$102`, fields at top of class). Everything in §3–§8 is this class and its
helpers. Two ways to read the same pipe; identical framing.

---

## 2. The two transports in `uav.midware`, and which is WM160

| stack | package | role for WM160 |
|---|---|---|
| **AOA composite (this doc)** | `uav.midware.usb.P3` + `uav.sdk.datalink.usb` | **YES** — the phone↔RC pipe carrying DUML+video |
| socket transport | `uav.midware.sockets.*` (TCP/UDT/UDP/SwUDP) | mostly **NOT** the WM160 air link; see §10 |

`uav.midware.sockets` is a **network** transport (TCP/`UAVUdtSocket`/`UAVSwUdpSocket`/UDP) used for
WiFi-connected products and for the internal loopback bridge. It is *not* how WM160 reaches the RC —
WM160 is AOA. `IpPortConfig$ConnectType` enum = `{DRONE, RC, UNKNOWN}` (lines 49/69/95). Treat the
socket classes as **NOT-WM160 air transport** (adjacent plumbing only). Details §10.

---

## 3. Composite / DUSS mux — receive framing (the core)

Assembled in `uav/midware/usb/P3/UsbAccessoryService.<init>` (lines 168–214):

```
UAVRingBufferModel model;
model.a = [0x55, 0xCC]     // sync/magic pattern            (fill-array-data :array_58, bytes 0x55, -0x34=0xCC)
model.b = 6                // header length AFTER the magic  (const/4 v1,0x6 ; iput ...->b:I)
parser = new UAVPluginRingBufferAsyncParser(0x19000, model, AoaRawChannelHandler)  // 100 KB ring buffer
```

So a receive unit on the wire is:

```
offset  size  field
0       1     0x55                      magic[0]   (Pack.s)
1       1     0xCC                      magic[1]   (Pack.t)
2       2     type   (u16, LITTLE-endian)          <- header field-len = 6 bytes total (2+4)
4       4     length (u32, LITTLE-endian)
8       length  payload                            (routed by type)
```

Endianness proof: `AoaRawChannelHandler.b([BII)I` (line 1017) reads the header:
- `type = BytesUtil.Y(buf, off, 2)` → stored in field `m:S`
- `length = BytesUtil.O(buf, off+2, 4)` → stored in field `n:I`
`BytesUtil.Y` (and `O`) loop from the **highest byte index down to the start**, `v = (v<<8) | b`, so
byte[start] ends up the LSB ⇒ **little-endian**. (Verified by reading the loop body of `BytesUtil.Y`.)

`b()` also validates:
- `e(type)` — type must be one of the 11 known channels (§5), else return `-1` (resync).
- `d(length,type)` — `length > 0x200000` (2 MiB) is rejected as `"link invalid data length"`
  (`AoaRawChannelHandler.d(II)Z`, `const/high16 0x200000`), logged to tag `"ConnectDebug"`,
  returns `-1`.
On a valid header `b()` returns `length` = number of body bytes the parser must collect before
calling `a()`.

**Resync:** the ring-buffer parser (`UAVPluginRingBufferParser`, method `c(II)I`) scans byte-by-byte
for the 2-byte sync pattern `model.a = {55 CC}` (compare loop lines ~250–394) before every header;
a partial unit split across USB reads is buffered (ring buffer, capacity grows via
`"Try to expand capacity:"`). This is exactly the `55 CC` resync the report's `composite.py`
already does.

---

## 4. Composite mux — transmit framing (how WE build a frame)

`uav/midware/data/packages/P3/SendPack` (extends `Pack`). Default outbound channel
`x = 0x5749` (constructor, line 55). `SendPack.c()` (line 423) serializes into `PackBufferObject`:
when the AOA link is up (`SendPack.d()` → `UAVServiceInterface.a()` true) it prepends an **8-byte
composite header** (lines 543–602):

```
buf[0] = Pack.s = 0x55
buf[1] = Pack.t = 0xCC
buf[2] = x & 0xFF          // 0x49   (type low  byte, LE)
buf[3] = (x>>8) & 0xFF     // 0x57   (type high byte, LE)
buf[4..7] = length (u32 LE)
buf[8..] = DUML frame
```
`Pack.s/Pack.t` static init (`Pack.<clinit>`): `0x55`, `0xCC`. Same magic, same LE type/length as RX.

Transmit itself: `UsbAccessoryService.sendmessage(SendPack)` (line 1207) writes the whole
pre-built `Pack.r:[B` buffer to `mAoaOutputStream.write(buf,0,len)` then `flush()` (lines 1231–1242).
Gated by `ENABLE_SEND_DATA_FOR_INNER` (default true). `sendmessage([B)` (line 1340) is the raw
variant. Send errors increment `sendIoError`.

**Pi mapping:** to send a DUML command, wrap it as `55 CC 49 57 <len u32 LE> <DUML>` and push it out
the AOA bulk-OUT endpoint. To send on the aux DUML channel, use type `0x7530` (`30 75`)
(`SendPack.g()` sets `x = 0x7530`, line 1163).

---

## 5. Channel `type` table — ALL 11 values + routing

Declared in `AoaRawChannelHandler` static fields (lines 14–36); routing in `a([BII)V`
(the `onGetBody` callback, switch on field `m:S`) and validity in `e(I)Z` (lines 221–354).

| type (hex) | field | routed in `a()` to | meaning / notes |
|---|---|---|---|
| **0x5749** | `o` (public) | `UAVPackManager.parse()` (DUML) | **DUML** — commands to RC/FC, telemetry/ACK back. The channel we care about most. |
| 0x574A | `p` | `VideoDataTransferor` (Camera/Fpv per LB2/SDR mode) | **video** (primary). Gated by `AoaRawChannelHandler.A` (drop if false). |
| 0x574B | `q` | `VideoDataTransferor` (SDR/LB2 branch) | video (secondary/aux). |
| 0x574C | `r` | (validated by `e()`; handled with video group) | video-class channel. |
| 0x574D | `s` | video group | video. |
| 0x574E | `v` | same branch as 0x574B | video (aux). |
| 0x574F | `t` | validated by `e()` | accepted channel (no explicit body branch → effectively ignored/other). |
| 0x5750 | `w` | `VideoDataTransferor` (SDR camera/fpv, sets `SdrLteVideoController.m(true)`) | video (SDR/LTE). |
| 0x5758 | `x` | `VideoDataTransferor(..., Camera)` | video. |
| 0x5759 | `y` | `ByteObject.obtain(len,"LbChannelHandler")` → `UAVPayloadUsbDataManager.c()` | **payload/LB channel** (non-video control payload). |
| **0x7530** | `u`/`z` (public) | `UAVPackManager.parse()` (DUML) | **DUML (extended/aux)** — same parser as 0x5749. `0x7530`=30000 decimal. |

Notes:
- `0x5749`='WI', `0x574A`='WJ' … `0x5750`='WP', `0x5758`='WX', `0x5759`='WY' (ASCII), `0x7530`=30000.
- The report's `composite.py` mapping (`0x574A/574D`→video, `0x574B/574C/7530`→other) is correct but
  incomplete: **`0x7530` is DUML, not "other"**, and there are 5 more video-class types.
- Video routing depends on runtime state of `LB2VideoController` (EncodeMode/SingleType) and
  `SdrLteVideoController` — WM160 uses the LB2/OcuSync-lite video controller path. Precise
  camera-vs-fpv split is a video-domain concern; here just note **video ⇒ `VideoDataTransferor`**.
- `StreamDataObserver$ObservingPoint` strings corroborate the split:
  `"UsbAccessoryService.onGetBody(dataType==22346)"` (=0x574A),
  `"...dataType==22347||22350"` (=0x574B / 0x574E).

---

## 6. DUML frame = payload of a `0x5749` (or `0x7530`) unit

Recap (full detail in `MASTER_REPORT.md` §2.2 / `DUML_COMMANDS_FULL.md`), because the composite
`type=0x5749` body is exactly one standard DUML frame:

```
[0] 0x55 magic
[1..2] len(10b)+ver(6b)
[3] CRC8 (seed 0x77) over bytes 0..2
[4] sender dev_type
[5] receiver dev_type
[6..7] seq (LE)
[8] cmd_type/attr
[9] cmd_set
[10] cmd_id
[11..] payload
[..] CRC16 (seed 0x3692) over whole frame
```
dev_type: PC=0x0a, RC=0x06, FC=0x03, gimbal=0x04, camera=0x01, dm368=0x08, battery=0x0d.
`UAVPackManager.parse([BII)` (called from `AoaRawChannelHandler.a()` at `:cond_154`) hands the DUML
frame to the DUML codec/dispatcher. Note the composite `0x55` and DUML `0x55` are *different* `0x55`
bytes at different nesting levels — do not confuse the mux magic with the DUML magic.

---

## 7. Receive pipeline — classes & flow (P3/Java)

```
AOA bulk-IN (fd) ──► InputStream (mAoaInputStream)
   │
   ▼  thread "recvBufferThread", priority 9
AoaRawDataReceiver           (reads InputStream in a loop → parser.d(buf,off,len))
   │   field a: UAVPluginRingBufferParser (async, 0x19000 ring buffer)
   │   optional debug dump to "/sdcard/aoa_recv.bin"  (field j; off by default)
   ▼
UAVPluginRingBufferAsyncParser  (LMAX-Disruptor-backed; sync={55 CC}, hdrlen=6)
   │   scans for 55 CC, calls listener.b() to get body length, buffers body
   ▼
AoaRawChannelHandler  (implements UAVRingBufferParserListener)
   ├─ b([BII)I : parse header → type(m:S), length(n:I), validate, return len   [§3]
   └─ a([BII)V : route body by type:                                          [§5]
        ├─ 0x5749 / 0x7530 → UAVPackManager.parse()  → DUML dispatch          [§6]
        ├─ 0x5759          → UAVPayloadUsbDataManager.c(ByteObject)
        └─ video types     → VideoDataTransferor.a()/b(..., VideoStreamSource)
```

Supporting classes:
- `AoaRawDataReceiver` — owns the read thread (`s(InputStream)` starts thread `recvBufferThread`,
  `t()` stops). Feeds bytes via `p([BII)` → `parser.d()`.
- `RecvBufferEvent` / `RecvBufferEventFactory` — Disruptor event holder (only when
  `USE_DISRUPTOR_RINGBUFFER`; note `UsbAccessoryService.USE_DISRUPTOR_RINGBUFFER=false`, so the
  **async parser** path is the default).
- `VideoDataTransferor` + `VideoRawBufferReceiver` — hand decoded video units up (16-byte liveview
  header starting `0x6d` per report; video domain).
- `UsbAccessoryService$VideoStreamSource` enum = `{Camera, Fpv, SecondaryCamera, Unknown}`.
- `AoaReportHelper` — **stats only** (logs `"cmd rate %.2f KB\n"`); NOT a keepalive.
- `AoaLogUtil` — logging.

---

## 8. Transmit pipeline (P3/Java)

```
caller builds SendPack (cmd_set/cmd_id/payload) → SendPack.c() prepends 55 CC + type + len  [§4]
   ▼
UsbAccessoryService.sendmessage(SendPack)  → mAoaOutputStream.write(Pack.r) ; flush()
   ▼
AOA bulk-OUT (fd)
```
`startStream()/stopStream()/startThreads()` manage the receiver thread and stream lifecycle;
`pauseService/pauseParseThread/pauseRecvThread` gate it. `isConnected()/isOK()/isRemoteOK()` report
link state; `isConnectedToProduct()` is the static probe used elsewhere.

---

## 9. Keepalive / health-check / reconnect

There is **no transport-level heartbeat frame**. Liveness is measured on the pipe:

- `UAVUsbAccessoryReceiver.j()` starts a poll timer with period **`0x7d0` = 2000 ms**
  (`k(0x7d0)`, line 1136; `sendEmptyMessageDelayed`, lines 1394/1411).
- Each tick calls `p()` → `JNIUsbAccessory.native_IsDataLinkAvailable(fd)` (line ~1620).
- Handler `q(Message)`:
  - available → log `"AOA check now, datalink available"` / `"...back to normal, invalid count="`.
  - unavailable → increment an invalid counter; when **`> 5`** (`const/4 v1,0x5`, line 1734):
    `"AOA check by timer,invalid count > 5"` ⇒ treat link dead ⇒ disconnect/reconnect.
- Connect/disconnect callbacks to native:
  `native_OnUsbConnected(fd, model)`, `native_OnUsbDisconnected(fd, model)`
  (`JNIUsbAccessory`, in `classes_03a5700c.dex`).
- USB detach → `onReceive()` handles `USB_ACCESSORY_ATTACHED`/detach intents, `o()` logs
  `"disconnected"` + `native_onUsbDisconnected`, tears down `ParcelFileDescriptor`.

**Pi mapping:** the RC does not require us to send periodic pings to stay attached — keep the bulk
pipe healthy and keep reading. But the *aircraft* still expects the normal DUML app traffic
(stick/RC pings, subscriptions) at the DUML layer (see FLIGHT_GATING.md); that is application-level,
not this transport.

---

## 10. `uav.midware.sockets` — the socket transport (adjacent, mostly NOT-WM160 air link)

A parallel transport used for WiFi products and for the **internal loopback bridge**, not the WM160
RC pipe. Key classes (all `classes_0451d00c.dex`):
- `pub/SocketClient` (+ `SocketTcpClient`, `SocketUdtClient`, `SocketSwUdpClient`) — TCP / UDT /
  software-UDP clients.
- `pub/UAVSocket`, `UAVUdtSocket`, `UAVSwUdpSocket`, `UAVUdpServerSocket` — socket wrappers.
- `pub/IpPortConfig` + `IpPortConfig$ConnectType {DRONE, RC, UNKNOWN}`.
- `P3/P3CCameraService`, `P3CRemoteService`, `P3CGroundService`, `SwUdpService` (port `0x232b`=9003),
  `WifiService` — WiFi/UDP product services (Tello-class, Spark-WiFi, etc.). **NOT-WM160.**
- `dpad/DPadCmdService`, `DPadWifiService`, `DpadStreamNewService` — DPad/goggles path. **NOT-WM160.**

### V5 native datalink bridge (the live path)
`uav/sdk/datalink/bridge/DataLinkBridgeServerManager` fronts the native datalink:
- `i(BridgeDataLinkInfo, byte[])` → `JNIDataLinkBridgeServer.native_bridge_send_raw_data(String
  linkId, byte[] data, int type)` — send raw framed data on a named link.
- `m(model)` registers the AOA link (called from `UAVUsbAccessoryReceiver.j()`), so the native side
  knows a `model="WM160"` AOA link exists and exposes it to the SDK by link-id.
This is the modern equivalent of `UsbAccessoryService.sendmessage` — same wire bytes, native impl.

---

## 11. WM160 support matrix + what needs a live capture / Frida

**Supported / confirmed for WM160:**
- AOA accessory binding by `manufacturer="DJI", model="WM160"` (accessory_filter.xml). ✅
- Composite mux `55 CC | type LE | len LE | payload`, 8-byte header, 2 MiB cap. ✅ (RX §3, TX §4)
- DUML on `0x5749` and `0x7530`; payload/LB on `0x5759`; video on the `0x574x/0x575x` group. ✅
- 2000 ms `native_IsDataLinkAvailable` health poll, >5-fail reconnect. ✅

**NOT-WM160 (present but for other products):** `uav.midware.sockets.P3.*` WiFi/UDP services,
`sockets.dpad.*` goggles, `sockets.Mammoth.*`. Video sub-routing via `SdrLteVideoController` (SDR/LTE
paths) — WM160 uses the `LB2VideoController` branch, not SDR/LTE.

**Undecidable statically (behind the packer) — needs Frida / live capture:**
1. **Which stack services WM160 at runtime.** Static evidence points to the **V5 native datalink**
   (`UAVUsbAccessoryReceiver.j()` → `native_OnUsbConnected` + `DataLinkBridgeServerManager.m()`),
   with the Java `UsbAccessoryService`/`AoaRawChannelHandler` as the readable spec. Confirm by
   hooking, on-device, whether `UsbAccessoryService.sendmessage`/`mAoaOutputStream.write` fires or
   whether traffic goes through native bridge only.
2. **Native composite parser/serializer.** The runtime demux is native
   (`duss_parse_composite_data @0x491a070`, task `mb_route_usb_data_recv_task` per MASTER_REPORT);
   JNI symbols (`native_OnUsbConnected`, `native_IsDataLinkAvailable`,
   `native_bridge_send_raw_data`) are registered dynamically (likely under `libAppGuard.so` packing)
   — not found by `strings` on the `.so`. Resolve at runtime via `RegisterNatives` hook.

**Frida hook list (exact):**
- Java: `uav.midware.usb.P3.UsbAccessoryService.sendmessage([B)` and `.sendmessage(SendPack)` —
  dump every outbound composite unit.
- Java: `uav.midware.usb.P3.AoaRawChannelHandler.b([BII)` (header: type+len) and `.a([BII)`
  (routed body) — dump every inbound unit with its `type`.
- Java: `uav.sdk.datalink.usb.UAVUsbAccessoryReceiver.j()` / `t()` — confirm fd, model, open.
- Java native stubs: `uav.jni.JNIUsbAccessory.native_OnUsbConnected/native_IsDataLinkAvailable`,
  `uav.sdk.datalink.bridge.jni.JNIDataLinkBridgeServer.native_bridge_send_raw_data` — args = the fd,
  model string, and raw framed bytes.
- Native: hook `art::JNI RegisterNatives` to recover the real addresses of the four JNI symbols
  above inside the packed lib, then hook the resolved `send`/`recv` for the ground truth pipe bytes.

---

## 12. Pi bridge cheat-sheet (this maps 1:1 to our Pi)

- Present the phone side of AOA to the RC; RC identifies as `DJI` / `WM160` (or `com.dji.link`).
- Two bulk endpoints: IN (RC→us) and OUT (us→RC).
- **Demux IN:** find `55 CC`, read `type`=u16 LE, `len`=u32 LE, take `len` payload bytes; if
  `len>0x200000` or `type` not in §5 table → resync on next `55 CC`.
  - `type 0x5749` or `0x7530` → DUML frame → parse/telemetry.
  - video types (`0x574A/4B/4C/4D/4E/50/58/59` group) → H.264/liveview.
  - `0x5759` → LB payload channel.
- **Mux OUT:** `55 CC | <type LE> | <len u32 LE> | <DUML frame>`; use `0x5749` (`49 57`) for normal
  DUML, `0x7530` (`30 75`) for the aux DUML channel.
- Keep the pipe alive by reading continuously; there is no transport ping to emit (the app's
  liveness check is a local `IsDataLinkAvailable(fd)` on its own side). Emit normal DUML app traffic
  at the DUML layer as per FLIGHT_GATING.md.

---

### Source index (smali, `classes_0451d00c.dex` unless noted)
- `uav/midware/usb/P3/UsbAccessoryService` — streams, mux config ({55 CC}, hdrlen 6, ring 0x19000), send.
- `uav/midware/usb/P3/AoaRawChannelHandler` — channel `type` constants (§5), header parse `b()`, route `a()`.
- `uav/midware/usb/P3/AoaRawDataReceiver` — read thread, ring-buffer feed, `/sdcard/aoa_recv.bin` debug.
- `uav/midware/parser/plugins/UAVPluginRingBufferParser` / `UAVPluginRingBufferAsyncParser` / `UAVRingBufferModel` — resync + framing engine.
- `uav/midware/data/packages/P3/SendPack` + `Pack` — TX composite header + `0x55/0xCC` magic (`Pack.s/Pack.t`).
- `uav/midware/util/BytesUtil` (`Y`,`O`) — LE decode proof.
- `uav/sdk/datalink/usb/UAVUsbAccessoryReceiver` / `ProductUsbInfo` / `UsbControllerRecognizer` — openAccessory, permission `com.uav.v4.accessory.USB`, 2000 ms health poll, native handoff.
- `uav/sdk/datalink/bridge/DataLinkBridgeServerManager` + `JNIDataLinkBridgeServer` — V5 native send bridge.
- `uav/jni/JNIUsbAccessory` (`classes_03a5700c.dex`) — native connect/disconnect/health JNI.
- `decompiled/AndroidManifest.xml`, `decompiled/res/xml/accessory_filter.xml` — AOA identity strings.
