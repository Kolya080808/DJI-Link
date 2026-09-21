# DOMAIN: keyvalue_sdk — the KeyValue CSDK (`uav.sdk.keyvalue`)

Scope: the value-object / typed-key protocol backbone of DJI Fly v1.21.4. **4537 classes**, all
packed into `unpacked_app_dex/classes_0451d00c.dex` (the JNI shim lives in
`classes_03a5700c.dex`). Everything below was disassembled with `baksmali` from those two dex files;
smali paths are given as `Luav/sdk/keyvalue/...` (the on-device class names).

Filter: **Mavic Mini 1 = WM160 = UAV59, ProductType 0x3b (59)**. See the WM160 section — the SDK
itself is **product-agnostic in Java**; per-product support is decided at runtime inside native code.

---

## 0. TL;DR / most important finding

The KeyValue SDK is DJI's **MSDK-v5 abstraction over DUML**. Java code never names a `cmd_set` /
`cmd_id`. A "key" is an *identity tuple* `(productId, componentType, componentIndex,
subComponentType, subComponentIndex)` **plus a human-readable name string** (e.g. `"VirtualJoyStick"`,
`"SerialNumber"`, `"Velocity"`). The Java layer serializes the value object to a little-endian byte
blob and hands `(the 5 ints, the name string, the value bytes)` to **native** methods in
`libsdk_jni.so`. **The mapping `name → DUML cmd_set/cmd_id → wire framing` happens entirely inside
the native SDK (`libsdk_jni.so` + `libsdk_key_value.so` + `libsdk_base.so` + `libsdk_common.so`) and
is NOT recoverable from the Java/smali.** To obtain the concrete `cmd_set/cmd_id` behind any key you
must reverse those `.so` files or Frida-hook the JNI boundary (exact hook points in §9).

So: this doc fully specifies the **Java side** — the key registry, the identity tuple with wire
values, the value serialization format (byte-exact), and the get/set/action/listen/push dispatch. The
**DUML opcode behind each key is a native black box**; where WM160 DUML opcodes are already known they
live in `DUML_COMMANDS_FULL.md` / `cmdmap.txt`, reached via the app's *other* (legacy) DUML paths, not
via this SDK.

---

## 1. What the domain does

`uav.sdk.keyvalue` is a strongly-typed façade. Instead of "build DUML frame 0x03/0x1c and parse the
reply", app code writes:

```
FCFlightMode m = UAVKeyManager.r( UAVKey.i(UAVFlightControllerKey.<theKey>) );   // sync get
UAVKeyManager.F( UAVKey.i(UAVFlightControllerControlKey...VirtualJoyStick), msg, cb ); // set
UAVKeyManager.m( UAVKey.e(UAVFlightControllerControlKey...StartSimulator), param, cb ); // action
```

The framework provides:
- a **key registry**: ~40 `UAV<Component>Key` container classes holding thousands of `static final`
  `UAVKeyInfo` / `UAVActionKeyInfo` descriptors (FC alone: **774**; Camera: **772**;
  FlightAssistant: **684**; RemoteController: **260**; Gimbal: **188**; Airlink: **135**;
  Product: **73**; Battery: **58**; …).
- a **value framework**: `UAVValue` messages (`value/**`, ~4000 classes) that serialize/deserialize
  themselves via `ByteStreamHelper` (little-endian, length-prefixed).
- a **dispatcher**: `UAVKeyManager` → `JNIKeyValue` → native.
- a **push path**: unsolicited native updates → `PushProcessor` → registered `KeyListener`s.

---

## 2. The key model — identity tuple + name

### 2.1 `UAVKeyInfoBase` (`Luav/sdk/keyvalue/key/UAVKeyInfoBase;`)
The static descriptor. Constructor `<init>(I I Ljava/lang/String; IUAVValueConverter)`:

| field | ctor arg | meaning | accessor |
|---|---|---|---|
| `a:I` | arg0 | **componentType** (see §3) | `d()` |
| `b:I` | arg1 | **subComponentType** (see §4) | `g()` |
| `i:Ljava/lang/String;` (final) | arg2 | **key name** — the string the native SDK maps to a DUML cmd | `e()` |
| `j:Ljava/lang/String;` | =arg2 | mutable alias of the name | `f()` |
| `c:IUAVValueConverter` | arg3 | value ⇄ bytes converter (§6) | `h()` |
| `d,e,f,g,h:Z` | — | capability flags: canGet / canSet / canListen / canAction / canPush (default all true except push) | `i() j() k() l() m()` |
| `k:J` | =-1 | listen throttle (ms), set via `c(J)` | `b()` |

Capability flags are toggled by the builder setters on `UAVKeyInfo` (`n/o/p/q/s(Z)` → fields
`d/e/…`) and `UAVActionKeyInfo` (`p/q/r/s/u`). They are **static SDK metadata, not per-product
gating** — e.g. `SerialNumber` is built `.n(true).q(false).o(true).p(false).s(false)`.

The **subclass hierarchy** encodes the allowed operations (used by the "Std" key tree, §5):
`UAVKeyInfoG` (get), `UAVKeyInfoS` (set), `UAVKeyInfoL` (listen), `UAVKeyInfoGS`, `UAVKeyInfoGL`,
`UAVKeyInfoSL`, `UAVKeyInfoGSL`, `UAVKeyInfoA` (action). `UAVActionKeyInfoBase`/`UAVActionKeyInfo`
carry **two** converters — input param and output result.

### 2.2 `UAVKey` (`Luav/sdk/keyvalue/key/UAVKey;`)
The *invocable* key = a `UAVKeyInfoBase` bound to concrete instance indices. Fields (verified against
`toString()` labels):

| field | label | accessor | default (factory `i`) |
|---|---|---|---|
| `a:I` | `mProductId` | `Y()` | `0` |
| `b:I` | `mComponentIndex` | `T()` | `0` |
| `c:I` | `mSubComponentIndex` | `Z()` | `0xFFFE` |
| `d:I` | (accessory sub-index; used when `e==true`) | via `a0()` | — |
| `f:UAVKeyInfoBase` | `mKeyInfo` | `W()` | the descriptor |
| — | `mComponentType` | `U()` → `keyInfo.d()` | from descriptor |
| — | `mSubComponentType` | `a0()` → `e ? d : keyInfo.g()` | from descriptor |
| name | — | `V()`→`keyInfo.e()` (get/set) · `X()`→`keyInfo.f()` (action/listen) | e.g. `"VirtualJoyStick"` |

Factory methods (`UAVKey.i/j/k/l/m`, `.e/f/g/h` for actions, plus the `A..S` std helpers) fill the
indices; `UAVKey.i(keyInfo)` = "all-instances" defaults `(productId=0, compIdx=0, subCompIdx=0xFFFE)`.
`0xFFFE` (= -2 as int16) is the SDK "**IGNORE / any**" sentinel (also `UAVKey.g:I = 0xFFFE`).

**Full runtime identity of a call = `(productId, componentType, componentIndex, subComponentType,
subComponentIndex, name)`.** That tuple + serialized value is exactly what crosses into native.

---

## 3. `ComponentType` enum — wire values

`Luav/sdk/keyvalue/key/ComponentType;`, field `value:I` = 3rd ctor arg. **Evidence:** `<clinit>` of
`ComponentType.smali`.

| enum | value | | enum | value |
|---|---|---|---|---|
| CAMERA | 1 | | RTKMOBILESTATION | 0x1A (26) |
| REMOTECONTROLLER | 3 | | RTKBASESTATION | 0x1B (27) |
| FLIGHTCONTROLLER | 4 | | OCUSYNC | 0x1C (28) |
| GIMBAL | 5 | | RADAR | 0x1D (29) |
| BATTERY | 6 | | PAYLOAD | 0x1E (30) |
| WIFI | 7 | | MOBILENETWORK | 0x1F (31) |
| AIRLINK | 8 | | BATTERYBOX | 0x20 (32) |
| FLIGHTASSISTANT | 0x0B (11) | | ONBOARD | 0x21 (33) |
| BLE | 0x12 (18) | | LIDAR | 0x22 (34) |
| NETWORK | 0x18 (24) | | HMS | 0x23 (35) |
| ACCESSORY | 0x19 (25) | | GLASS | 0x24 (36) |
| | | | RCBEACON | 0x25 (37) |
| MISSION | 0xED (237) | | APP | 0xEE (238) |
| PRODUCT | 0xEE (238)* | | | |

*PRODUCT reuses value 0xEE in `<clinit>` (both APP and PRODUCT are app-side pseudo-components).
`ComponentType.z` is the fallback used by `UAVKey.U()` when `keyInfo==null`. Note these are the DJI
device/component identifiers, **not** DUML `cmd_set` numbers — the native SDK translates them.

WM160-relevant components: **CAMERA(1), FLIGHTCONTROLLER(4), GIMBAL(5), BATTERY(6),
FLIGHTASSISTANT(11), REMOTECONTROLLER(3), AIRLINK(8), OCUSYNC(28)** map onto the WM160 DUML device
addresses documented in `MASTER_REPORT.md` §2.2 (camera=0x01, FC=0x03, gimbal=0x04, battery=0x0d,
RC=0x06). RTK*, LIDAR, RADAR, PAYLOAD, BATTERYBOX, MOBILENETWORK, GLASS, SPEAKER, SPOTLIGHT, BEACON
have no WM160 hardware → **NOT-WM160** (keys exist but native will reject / never populate).

---

## 4. `SubComponentType` enum — wire values

`Luav/sdk/keyvalue/key/SubComponentType;`, field `value:I`. **Evidence:** `<clinit>`.

| enum (field) | ordinal | value |
|---|---|---|
| UNKNOWN (`a`) | 0 | 0 |
| SPOTLIGHT (`b`) | 1 | 1 |
| BEACON (`c`) | 2 | 2 |
| SPEAKER (`d`) | 3 | 3 |
| MOBILENETWORKLINKRC (`e`) | 4 | 1 |
| BATTERYBOXBIGBATTERY (`f`) | 5 | 4 |
| **IGNORE (`g`)** | 6 | **0xFFFE (65534)** |

`SubComponentType.g = IGNORE = 0xFFFE` is the default for all "normal" keys (FC, camera, gimbal …).
Sub-components are only non-IGNORE for accessories (spotlight/speaker/beacon) and dual-battery — **all
NOT-WM160**. For WM160, `subComponentType` is effectively always `0xFFFE`.

---

## 5. The key registry — two generations

### 5.1 Legacy flat keys — `UAV<Component>Key`
Classes like `UAVFlightControllerKey`, `UAVCameraKey`, `UAVGimbalKey`, `UAVFlightAssistantKey`,
`UAVBatteryKey`, `UAVRemoteControllerKey`, `UAVAirlinkKey`, `UAVOcuSyncKey`, `UAVProductKey`,
`UAVWiFiKey`, `UAVBleKey`, `UAVHmsKey`, `UAVAccessoryKey`, … Each is a bag of `static final UAVKeyInfo`
fields built in `<clinit>`. Example (FC, verified):

```
UAVKeyInfo("SerialNumber")  : component=ComponentType.c.value(4), sub=SubComponentType.g.value(0xFFFE),
                              converter=SingleValueConverter.StringConverter, canGet=1 canSet=0 …
UAVKeyInfo("FirmwareVersion"): component=4, sub=0xFFFE, StringConverter
```

### 5.2 "Std" structured keys — `UAVStd<Component>Key` + `key/<component>/<subsystem>/…`
The newer MSDK-v5 tree. `UAVStdFlightControllerKey` holds instances of
`key/flightcontroller/control/UAVFlightControllerControlKey`, `.../flightsafety/…`, `.../state/…`,
`.../setting/…`, `.../desc/…`. These build `UAVKeyInfoGS/GSL/A/…` in `<init>` (not `<clinit>`), same
`(componentType, subComponentType, name, converter[, converter2])` ctor. The subsystem
(control/state/setting/desc) is *encoded only in the name grouping* — it does **not** change the
identity ints; still `component=FLIGHTCONTROLLER(4), sub=IGNORE(0xFFFE)`.

**Keys directly useful to the PC-control goal** (all on FLIGHTCONTROLLER, found in
`UAVFlightControllerControlKey`):
- `"VirtualJoyStick"` — GetSetListen (`UAVKeyInfoGSL`) → value `VirtualJoyStickMsg` (§7.3). **This is
  the keyvalue stick-injection key.**
- `"IsSupportVirtualJoyStick"` — capability probe.
- `"StartSimulator"` / `"StopSimulator"` — actions.
- `"RebootDevice"`, `"FCDeleteBlackBox"`, `"TurnOnRGBLed"` — actions.

Whether WM160's FC firmware honours `VirtualJoyStick` over the KeyValue path is **native/firmware
decided → needs live capture** (§9). The project's confirmed-working stick path is the raw DUML one in
`FLIGHT_GATING.md`, not this SDK.

---

## 6. Value ⇄ bytes: the serialization framework

### 6.1 `UAVValue` (`Luav/sdk/keyvalue/value/base/UAVValue;`) — the message interface
```
int  toBytes(byte[] buf, int off)   // write self at off, return new off
byte[] toBytes()                    // allocate + write
int  fromBytes(byte[] buf, int off) // parse self, return new off
JSONObject toJson()
```
Every concrete `value/**` message (Parcelable, has a `$a` builder) implements these by chaining
`ByteStreamHelper` calls, one per field, in declaration order.

### 6.2 `ByteStreamHelper` (`Luav/sdk/keyvalue/value/ByteStreamHelper;`) — the codec (49 methods)
**All multi-byte integers/floats are LITTLE-ENDIAN** (`ByteBuffer.order(LITTLE_ENDIAN)`, verified in
method `H`). Primitive widths (static fields): `a,b,c,d = 4` (int / uint / float / count),
`e,f = 8` (long / double). Pattern per type = a *size* fn, a *write* fn `(byte[],val,off)→off`, and a
*read* fn `(byte[],off)→ByteResult`. `ByteResult{Object a; int b/*newOffset*/}`.

| type | size | write | read | encoding |
|---|---|---|---|---|
| Boolean | `h` = **1 byte** | `i` | `j` | 0/1 |
| Byte | 1 | `s` | `t` | raw |
| Integer (int32) | `G` = 4 | `H` | `I` | LE |
| Long (int64) | `M` = 8 | `N` | `O` | LE |
| Double | `r`/`x` = 8 | `y` | `w` | LE IEEE-754 |
| String | `S` = 4+len | `T` | `U`* | **[u32 LE length][UTF-8 bytes]** (`getBytes()`) |
| List\<Int\> | `J` | `K` | `L` | **[u32 LE count]** + elems |
| List\<Long\> | `P` | `Q` | `R` | u32 count + elems |
| List\<Bool\> | `d` | `f` | `g` | u32 count + elems |
| List\<Byte\> | `k`/`p` | `l` | `q`/`m` | u32 count + bytes |
| List\<Double\> | `n`/`u` | `o` | `v` | u32 count + elems |
| nested struct | `A(stream,cls)` | `U(buf,stream,off,cls)` | `z(buf,off,cls)` / `a(...)` | recurse into `ByteStream` |
| raw len | — | `B(buf,int,off)` writes a u32 LE length | — | — |

`ByteStream` (`value/ByteStream.smali`) is the base for nested/repeated struct fields;
`BytesOffset` is a mutable cursor used by the converter read path.

**Enums serialize as int32 LE.** Every enum message (e.g. `FCFlightModeMsg.toBytes`) does
`enum.value()` → `Integer` → `ByteStreamHelper.H`, and decodes with `Enum.find(int)`.

### 6.3 Converters — `converter/`
- `IUAVValueConverter`: `fromBytes(byte[],BytesOffset)→Object`, `toUAVValue(Object)→UAVValue`,
  `getClassType()`, `getSdkValueType()`, `fromStr(String)`.
- `SingleValueConverter` (enum of 5) wraps Java primitives ⇄ common wrapper messages:
  `BooleanConverter`↔`common/BoolMsg`, `IntegerConverter`↔`common/IntMsg`,
  `DoubleConverter`↔`common/DoubleMsg`, `StringConverter`↔`common/StringMsg`,
  `LongConverter`↔`common/Int64Msg`.
- `UAVValueConverter` — generic converter for arbitrary `UAVValue` subclasses (structs/enums).
- `BufferConverter` — raw `byte[]` passthrough. `EmptyValueConverter` — void (actions with no
  param/result).

`BoolMsg/IntMsg/…` are trivial one-field messages: e.g. `IntMsg.toBytes` = `H(buf, this.value, off)`.

---

## 7. Concrete WM160-relevant value objects (byte-exact)

### 7.1 `common/Attitude` — 24 bytes
Fields `pitch, roll, yaw : Double`. `toBytes` = three LE doubles in that order. → **`double pitch |
double roll | double yaw`, 24 bytes**.

### 7.2 `flightcontroller/FCFlightMode` — enum, int32 LE
`FCFlightModeMsg` value = one int32. Values (from `<clinit>`):

| name | val | | name | val | | name | val |
|---|---|---|---|---|---|---|---|
| MANUAL | 0 | | GPS_ATTI | 6 | | AUTO_LANDING | 12 |
| ATTI | 1 | | GPS_CL | 7 | | ATTI_LANDING | 13 |
| ATTI_CL | 2 | | GPS_HOMELOCK | 8 | | NAVI_GO | 14 |
| ATTI_HOVER | 3 | | GPS_HOTPOINT | 9 | | GO_HOME | 15 |
| HOVER | 4 | | ASSISTED_TAKE_OFF | 10 | | CLICK_GO | 16 |
| GPS_BRAKE | 5 | | AUTO_TAKE_OFF | 11 | | JOYSTICK | 17 |
| GPS_ATTI_WRISTBAND | 18 | | CINEMATIC | 19 | | … (ACTIVE_TRACK, APAS, ADSB_ACTION, …) | |

These match the DUML FC-flight-mode enum used elsewhere in `TELEMETRY_TABLE.txt`.

### 7.3 `flightcontroller/VirtualJoyStickMsg` — 16 bytes (the stick key's value)
Fields `Channel_0, Channel_1, Channel_2, Channel_3 : Integer`. `toBytes` = four int32 LE in order →
**`int32 ch0 | int32 ch1 | int32 ch2 | int32 ch3`, 16 bytes** (raw stick channel values). Bound to
the `"VirtualJoyStick"` GSL key on FLIGHTCONTROLLER.

### 7.4 `flightcontroller/VirtualStickFlightControlParam` — richer control struct
Fields: `pitch, roll, yaw, verticalThrottle : Double`; `advancedModeEnabled : Boolean`;
`rollPitchControlMode : RollPitchControlMode`; `verticalControlMode : VerticalControlMode`;
`yawControlMode : YawControlMode`; `rollPitchCoordinateSystem : FlightCoordinateSystem` (each mode
enum = int32 LE). This is the higher-level MSDK virtual-stick param (velocity/angle modes).

---

## 8. Dispatch flow — Java → native (exact ABI)

### 8.1 `UAVKeyManager` (`Luav/sdk/keyvalue/UAVKeyManager;`) — public API
- `r(UAVKey) : Object` — **sync get**: calls `JNIKeyValue.get(key)→byte[]`, then
  `keyInfo.h().fromBytes(bytes, new BytesOffset())` to decode. Returns `null` on null/exception.
- `t(UAVKey, IGetCallback)` — **async get** → `JNIKeyValue.get(key, JNIGetCallback)`.
- `F(UAVKey, Object, ISetCallback)` — **set** (converts value → `UAVValue` via the key's converter →
  `JNIKeyValue.set`).
- `m(UAVKey$ActionKey, Object, IActionCallback)` / `n(…, cb)` — **action** (with/without param);
  `o/p(UAVActionKey, …)` for the Std action keys.
- `C(UAVKey, IListenCallback) : KeyListener` / `D/E(UAVKey, Object, IListenCallback[, boolean])` —
  **listen / register push** → `JNIKeyValue.listen`.
- `k(UAVKey)` cancel-listen, `i()` reset. Callbacks are marshalled through small `Lm3/*` lambda
  classes into the `IGet/ISet/IAction/IListen` interfaces (`callback/`).

`UAVStdKeyManager` is the analogous entry point for the Std key tree.

### 8.2 `JNIKeyValue` (`Luav/jni/JNIKeyValue;`) — the native boundary
Native lib loaded via `Luav/sdk/jni/LibraryLoader;->b()` → `System.loadLibrary("sdk_jni")` (also
`cross_playback`, `panorama_kit`). Native declarations:

```
native void  native_get      (int productId, int componentType, int componentIndex,
                              int subComponentType, int subComponentIndex,
                              String name, JNIGetCallback cb)
native byte[] native_get_sync (int,int,int,int,int, String name)
native void  native_set      (int×5, String name, byte[] value, JNISetCallback cb)
native void  native_do_action(int×5, String name, byte[] param, JNIActionCallback cb)
native int   native_listen   (int×5, String name, JNIListenCallback cb)
native void  native_cancel_listen(int×6)
```

**The 5 ints, in call order, are** (verified in `JNIKeyValue.set/get/doAction/listen`):
`( Y()=productId, U()=componentType, T()=componentIndex, a0()=subComponentType, Z()=subComponentIndex )`.
Then the **name string** and (for set/action) the **value byte[]** = `UAVValue.toBytes()`.
For a default WM160 FC key that is `(0, 4, 0, 0xFFFE, 0xFFFE, "<Name>", <bytes>)`.

> Note the ABI carries **only the name string**, not any numeric key id — confirming the
> `name→cmd_set/cmd_id` table is inside the native `.so`. `native_get`/`set`/`do_action`/`listen` in
> `libsdk_jni.so` call into `libsdk_key_value.so` which owns that table and the DUML framing (the same
> `0x55` frames documented in `MASTER_REPORT.md` §2.2 / `duml.py`).

### 8.3 Push path — `push/PushProcessor`
Native pushes an updated value for a key → `PushProcessor.d(UAVKey, Object)` wraps it in
`PushProcessor$a` and fans out to the `Map` of registered listeners (`push/a,b,c`, `PushRecorder`
caches last value per key so late subscribers get the current state). This is how telemetry keys
("Velocity", "Attitude", battery %, flight mode) stream without polling — but again the *DUML push
opcode → key name* demux is native.

---

## 9. WM160 support & what needs a live capture / Frida

**Java-side product gating: none in this SDK.** Grepping the whole `uav/sdk/keyvalue` tree yields no
WM160 / UAV59 / ProductType-59 constants and no per-product key filter. Every key exists for every
product in Java; support is resolved **at runtime by native** (the connected product's capability set)
and surfaced through:
- `IsSupport*` capability keys (`isSupport_keys.txt` lists ~hundreds: `isSupportVirtualJoyStick`,
  `isSupport4K`, `isSupportAeLockUnlock`, …) — you `get`/`listen` these and native answers per the
  connected WM160.
- `native_get_sync` returning `null` / callbacks firing an error code (`ERROR_CODES.md`) for
  unsupported keys.

Therefore, for WM160 the *only* reliable way to learn which keys work and what DUML they emit is a
**live capture** against a connected Mini 1. Frida hook points (all in the packed dex, load after
`libsdk_jni.so`):

- **Java, cleanest** — hook `Luav/jni/JNIKeyValue;->native_set`, `->native_get`,
  `->native_get_sync`, `->native_do_action`, `->native_listen`. You get `(productId, componentType,
  componentIndex, subComponentType, subComponentIndex, name, value[])` for every call — a complete
  key-usage trace with payloads, without touching native.
- **Value bytes** — hook `Luav/sdk/keyvalue/value/base/UAVValue;->toBytes()[B` (per-message) or the
  `ByteStreamHelper` writers to confirm field layouts.
- **Decode replies** — hook `Luav/sdk/keyvalue/UAVKeyManager;->r(...)` return, or the converter
  `fromBytes([B,BytesOffset)`.
- **Push demux** — hook `Luav/sdk/keyvalue/push/PushProcessor;->d(UAVKey,Object)` to see which key
  each unsolicited native update belongs to.
- **The actual DUML opcodes** — hook are **not** visible in Java; to bridge `name → cmd_set/cmd_id`
  you must either (a) hook the native side of `libsdk_key_value.so` (the function that consumes the
  name string and builds the `0x55` frame — find it by xref'ing the JNI `native_set` impl in
  `libsdk_jni.so`), or (b) correlate the Frida key trace against the on-wire DUML captured on
  COM4/COM5/AOA (per `MASTER_REPORT.md`). Option (b) is the pragmatic route for this project.

**Bottom line for PC control:** the KeyValue SDK gives you the exhaustive *catalogue* of what the app
can command on WM160 (key names + value layouts, e.g. `VirtualJoyStick` = 4×int32, `FCFlightMode`
enum, `Attitude` = 3×double) and byte-exact serialization, but the WM160 DUML `cmd_set/cmd_id` for
each is native and must be captured live. The already-verified, working WM160 stick/flight commands
are the raw-DUML ones in `FLIGHT_GATING.md` / `DUML_COMMANDS_FULL.md`, which bypass this SDK.

---

## 10. Class-path quick index (evidence)

- Dispatch: `Luav/sdk/keyvalue/UAVKeyManager;`, `Luav/sdk/keyvalue/UAVStdKeyManager;`,
  `Luav/jni/JNIKeyValue;` (in `classes_03a5700c.dex`), `Luav/sdk/jni/LibraryLoader;`.
- Key model: `key/UAVKey;`, `key/UAVKeyInfoBase;`, `key/UAVKeyInfo;`, `key/UAVActionKeyInfo(Base);`,
  `key/ComponentType;`, `key/SubComponentType;`, `key/std/**` (getset/get/set/listen/action variants).
- Registry: `key/UAV<Component>Key;`, `key/UAVStd<Component>Key;`, `key/<component>/<subsystem>/…`.
- Value/codec: `value/base/UAVValue;`, `value/base/UAVEnum;`, `value/ByteStreamHelper;`,
  `value/ByteStream;`, `value/ByteResult;`, `value/BytesOffset;`, `value/common/*Msg;`,
  `value/flightcontroller/*`, `value/camera/*`, `value/gimbal/*`, … (~4000 msg classes).
- Converters: `converter/IUAVValueConverter;`, `converter/SingleValueConverter;`,
  `converter/UAVValueConverter;`, `converter/BufferConverter;`, `converter/EmptyValueConverter;`.
- Push: `push/PushProcessor;`, `push/PushRecorder;`, `push/a|b|c;`.
- Native libs (in `decompiled/lib/arm64-v8a/`): `libsdk_jni.so`, `libsdk_key_value.so`,
  `libsdk_base.so`, `libsdk_common.so`, `libsdk_file_system.so`.
