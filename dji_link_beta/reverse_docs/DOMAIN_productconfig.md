# DOMAIN: productconfig — Device capabilities / gating for WM160 (Mavic Mini 1 / UAV59)

Scope: the app-side *static product-capability table*. What the app statically "knows" a
connected aircraft can do, keyed by the aircraft's device-type id. Filtered to **WM160 = UAV59 =
device id 59 (0x3b) = "Mavic Mini"**.

Evidence base: the whole-app dex set under `reverse_docs/unpacked_app_dex/` (baksmali'd), the
decompiled resources under `decompiled/res/`, and `all_classes.txt`. All class/field/method/enum
references below are cited to concrete smali. Where a value cannot be resolved statically (it comes
from the packed native `flymodel` layer) it is called out with the exact Frida hook.

---

## 0. Two important corrections up front

**(a) Package naming.** The brief calls the domain `com.dji.productconfig`. In this (rebranded)
build there are **two** cooperating packages:

- `com.uav.productconfig.**` — the **framework** (interfaces, the sealed `BaseDrone` model, the
  per-feature `I<Feature>ProductConfig` interfaces + their `DefaultImpls`, the config-list
  containers, and the `FlyConfigKey` push-keys). Lives in dex `classes_00b9d00c.dex`.
- `com.dji.productconfig.<feature>.generate.**` — the **generated concrete per-model configs**
  (e.g. `UAV59PlaybackProductConfig`) and the `Fly<Feature>ProductConfigs` registries that list
  them. Lives in dex `classes_04e4400c.dex`.

There is *no* separate `com.dji.productconfig` capability engine beyond this; both packages are the
domain.

**(b) There is NO per-model `BaseDrone` subclass carrying `isSupport*` fields.** The brief's mental
model ("the WM160 BaseDrone subclass — every isSupport flag and its value") is the *stock-DJI-SDK*
model and does **not** match this app. Here:

- `BaseDrone` is a **sealed class with exactly two subclasses**: `DroneInfo(droneId, subProductId)`
  and `FlyConfigKey$UnknownDrone`. WM160 is simply `DroneInfo(59, null)` — a plain data object, no
  capability fields. (`com/uav/productconfig/BaseDrone.smali` — sealed marker ``
  → subclasses `DroneInfo`, `UnknownDrone`.)
- Capabilities are **not** stored on the drone object. They are **methods on per-feature config
  classes** that take a `BaseDrone` and answer for it. WM160's answers come from the seven
  `UAV59*ProductConfig` classes plus the app-level `FlyAppProductConfig` lists.
- The huge `reverse_docs/isSupport_keys.txt` list (≈250 `isSupport*` names) is **app-wide**
  (camera, flight, media, upgrade, …) and is **not** this domain. Within productconfig there is
  exactly **one** boolean `isSupport*` flag: `isSupportLTMByMediaMeta` (see §5).

So "what the aircraft advertises as supported" is really: *the aircraft reports its device-type id
(59) over the link; the app then looks up a compile-time table.* The table is the subject of this
doc.

---

## 1. How WM160 gets identified (the DUML linkage)

The productconfig "current drone" is derived from one observable:

`com/uav/productconfig/FlyConfigKey$keyDroneInfo$2.smali`:
```
FlyModel.getAircraft()
        .getAircraftDeviceInfo()          // AircraftDeviceInfoModel
        .getDeviceId()                    // FlyObservable<Long>, default 0
```
mapped by `FlyConfigKey$keyDroneInfo$2$1.smali`:
```
a(long droneType) -> new DroneInfo( (int)droneType, null )
```

So **`deviceId == 59` ⇒ `DroneInfo(59, null)` ⇒ every `UAV59*ProductConfig.matches()` returns
true** for this aircraft. `deviceId` is a plain `Long` device-type number; **59** is DJI's internal
product type for Mavic Mini (WM160).

`getDeviceId()` is produced inside the packed/native `com.uav.flymodel` generated layer from the
link (DUML version/type handshake). **Statically undecidable past `flymodel`** — the byte-level DUML
that carries the type is not in Java.

- **Frida hooks to confirm 59 on a live link:**
  - `com.uav.flymodel.generated.api.aircraft.AircraftDeviceInfoModel.getDeviceId()` → observe the
    emitted `Long`.
  - `com.uav.productconfig.FlyConfigKey$keyDroneInfo$2$1.a(J)` → confirm the `DroneInfo` id.
  - `com.uav.productconfig.BaseDrone.d(I)` (`matches(int droneId)`; `ProductInfo.a()==droneId`) →
    watch which config id matches.

---

## 2. The dispatch / delegation semantics (how a value is resolved)

Each feature domain has:

- an interface `I<Feature>ProductConfig` with methods `(BaseDrone) -> value`;
- an `I<Feature>ProductConfig$DefaultImpls` holding the **default answer** (usually
  `false` / `null` / `emptyList`);
- one concrete `UAV<id><Feature>ProductConfig` per model, each carrying only `id` (default `0x3b`
  for the UAV59 classes) and overriding the methods it wants to specialise;
- a `Drone<Feature>ProductConfigList` wrapping `List<I<Feature>ProductConfig>` (built by
  `Fly<Feature>ProductConfigs` from `FlyDroneProductConfigListKt.a()`).

Two default helpers appear everywhere and encode the whole scheme
(`IDronePlaybackProductConfig$DefaultImpls.smali` is the canonical example):
```
DefaultImpls.a(cfg, aircraft) = false            // "not supported" default
DefaultImpls.b(cfg, aircraft) = aircraft.matches(cfg.getId())   // "true iff this is my model"
```
A concrete config **enables** a boolean capability by overriding it to return the `matches` helper
(true for its own model), and **provides a resource** by returning it guarded by `if (matches)`.
Consumers iterate the list; for a given aircraft **exactly one** config matches (or none → all
defaults). Practical upshot: *WM160 supports feature X iff a `UAV59` config for domain X exists and
overrides X.*

---

## 3. WM160 identity table (all resolved from UAV59 configs + res/)

| Attribute | Value | Source |
|---|---|---|
| Device-type id | **59 / 0x3b** | default ctor `const/16 p1, 0x3b` in every `UAV59*ProductConfig` |
| Official product name | **"Mavic Mini"** | `R.string.product_official_name_UAV59` (`UAV59ProductNamesConfig.d()`); resolved in `res/values/strings.xml` |
| Series name | **"DJI Mini"** | `R.string.connect_drone_guide_step_series_ABC_mini` (`UAV59ConnectGuideProductConfig.w()`) |
| Gallery/media-meta name | **"Mavic Mini/DJI Mini SE"** (shared UAV96+160) | `R.string.general_playback_product_official_name_UAV96_160` (`UAV59ProductNamesConfig.f()`) |
| Product font | `R.font.fly_uav59_wm160` | `UAV59ProductNamesConfig.c()` — **filename literally confirms UAV59 ⇔ WM160** |
| Release date shown in guide | **"2019/10/30 21:00"** | `UAV59ConnectGuideProductConfig.A()` |
| Product image asset | `productimage_img_modelsectct_160` | `UAV59ConnectGuideProductConfig.f()` |
| HD picture asset | `producthdpicture_uav59` | `UAV59ProductNamesConfig.e()` |
| Paired RC id | **99 / 0x63** | `UAV59ConnectGuideProductConfig.y()` → `FlyProductsKt.d().get(99)` |
| Supported goggles | **none** (`emptyList`) | `UAV59ConnectGuideProductConfig.s()` |

---

## 4. Per-feature UAV59 config matrix (which domains WM160 has a config in)

Enumerated from `all_classes.txt` (`com/dji/productconfig/*/generate/UAV59*`). "Present" = a
`UAV59` class exists in that domain's `FlyDroneProductConfigListKt`.

| Feature domain | UAV59 config present? | What it supplies for WM160 |
|---|---|---|
| `productnames` | ✅ `UAV59ProductNamesConfig` | names, font, HD picture (§3) |
| `connectguide` | ✅ `UAV59ConnectGuideProductConfig` | pairing/turn-on tips, videos, product image, RC list, series (§3, below) |
| `compasscalibration` | ✅ `UAV59CompassCalibrationProductConfig` | 3 calibration illustrations |
| `gimbalcalibration` | ✅ `UAV59GimbalCalibrationProductConfig` | 1 gimbal-calibration illustration |
| `imucalibration` | ✅ `UAV59IMUCalibrationProductConfig` | 5 IMU-orientation illustrations |
| `newbieguide` | ✅ `UAV59NewbieGuideProductConfig` | 7 tutorial images (RC-based) |
| `playback` | ✅ `UAV59PlaybackProductConfig` | **`isSupportLTMByMediaMeta = true`** (§5) |
| `takeoffandland` | ❌ **absent** | falls back to `DefaultImpls` → all takeoff/land illustrations `null` |
| `wififast` | ❌ **absent** | no WiFi-fast/QuickConnect config → default |
| `portraitnewbieguide` | ❌ absent (only UAV158/182/183) | no portrait newbie guide |
| `fpv` | ❌ n/a (RC-keyed: OPR* only) | not a drone-keyed domain |
| `rcbutton` / `rccalibration` / `rcnewbieguide` | ❌ n/a (RC-keyed: OPR* only) | not drone-keyed |

### 4.1 Resource assets referenced by the UAV59 configs (exact literal keys)

`compasscalibration` (`UAV59CompassCalibrationProductConfig`):
- vertical: `vertical_img_fpv_compass_calibration_ver_uav59`
- horizontal: `horizontal_img_fpv_compass_calibration_hor_uav59`
- normal: `normal_img_fpv_compass_calibration_uav59_normal`

`gimbalcalibration` (`UAV59GimbalCalibrationProductConfig`):
- `gimbalcalibrationimage_ic_fpv_gimbal_calibration_uav59_normal`

`imucalibration` (`UAV59IMUCalibrationProductConfig`) — 5 orientations:
- `leftdown_setting_ui_uav59_imucali_left_down`, `rightdown_setting_ui_uav59_imucali_right_down`,
  `topdown_setting_ui_uav59_imucali_top_down`, `bottomdown_setting_ui_uav59_imucali_bottom_down`,
  `taildown_setting_ui_uav59_imucali_tail_down`

`newbieguide` (`UAV59NewbieGuideProductConfig`):
- entrance: `uav59_entranceimage_fpv_newbie_guide_entrance_uav59`
- checkPropeller: `uav59_checkpropellerimage_fpv_newbie_guide_hardware_prepare_1`
- checkTail: `uav59_checktailimage_fpv_newbie_guide_hardware_prepare_2`
- checkGimbal: `checkgimbalimage_img_teaching_2`
- checkRc: `uav59_checkrc_img_teaching_4`  ← RC-based prep (WM160 uses a physical RC)
- brakeReturnHome: `uav59_brakereturnhome_genenal_rc_160`  ← "genenal_rc" = standard RC drone
- noGps: `nogpsimage_img_teaching_160_3x`

`connectguide` (`UAV59ConnectGuideProductConfig`) additional literals:
- turn-on video (step2): `turnonvideourl_user_guide_movie_step2_uav59`
- pair video (step3): `pairvideourl_user_guide_movie_step3_uav59`
- turn-on tips: `R.string.connect_drone_guide_UAV59_second_step_start_drone_sub_one_title`
  ("Unfold the front and rear aircraft arms…")
- pair tips: `R.string.connect_drone_guide_UAV59_third_step_frequency_connect_sub_one_title`
  ("Press and hold the power button on the underside of the aircraft for 4 seconds…")
- name-in-font: `R.string.UAV59_name_font`

All methods not listed above **delegate to `DefaultImpls`** (i.e. WM160 uses the generic default:
no BCT/beacon list, no goggles, default pair image, etc.).

---

## 5. The ONE boolean capability flag in productconfig

`com/dji/productconfig/playback/generate/UAV59PlaybackProductConfig.smali` implements
`IDronePlaybackProductConfig`, whose sole flag is **`isSupportLTMByMediaMeta`**:
```
b(aircraft) = isSupportLTMByMediaMeta -> a(aircraft) -> DefaultImpls.b(this, aircraft)
            = aircraft.matches(59)
```
⇒ **WM160: `isSupportLTMByMediaMeta = true`.** (LTM = local-tone-mapping metadata in playback.)
Models present in this list = {UAV59, UAV76, UAV96, UAV112, UAV113, UAV127}; every other model
returns the `false` default. This is the only aircraft-capability boolean the productconfig domain
answers; all other UAV59 methods return UI resources.

---

## 6. App-level product lists (`FlyAppProductConfig`) — WM160 classification

`com/dji/productconfig/app/generate/FlyAppProductConfig.smali` implements `IAppAppProductConfig`;
the seven list-methods are surfaced as keys in `AppProductConfigKey`. Method→key mapping verified
via `AppProductConfigKey$key*$2$1` (`invoke-interface … IAppAppProductConfig;->X()`):

| Method | Key | Members (device ids, decimal) | Contains **59**? |
|---|---|---|---|
| `a()` | `keySupportedGogglesList` | 135, 153, 114, 78, 79 (GLS goggles) | — |
| `b()` | `keySupportWifiDroneList` | 202,165,159,158,157,139,137,182,183,185,152,126,111,110,112,120,77,103,76 (19) | **NO** |
| `c()` | `keySupportBeaconList` | (empty) | no |
| `d()` | `keyProductSeriesOrderlist` | series ids (String/order objects) | n/a |
| `e()` | `keySupportRcList` | 175,186,136,155,154,147,146,171,81,56,**99**,21,32,94,406,82,144 (17 RCs) | RC **99** ✓ |
| `f()` | `keySupportWifiGlassList` | 153 | — |
| `g()` | `keySupportedDroneList` | 202,165,159,158,139,137,182,183,185,127,152,126,111,121,110,112,113,120,77,75,67,73,103,76,**59**,96 (26) | **YES** (index 24) |

**Key finding:** WM160 (59) is in **`supportedDroneList`** (the app supports it) but is **NOT in
`supportWifiDroneList`**, and has **no `wififast` config** (§4). In this app "WiFi drone" means a
drone the *phone connects to directly over Wi‑Fi* (Tello/Mini-SE-in-WiFi class). WM160 instead
connects **through its physical remote controller (RC id 99)** — corroborated three ways: absent
from the wifi list, `connectguide.supportedRcList = [RC 99]`, and the newbie-guide assets keyed
`genenal_rc` / `checkrc`. For the PC-control project this means the WM160 link is RC-mediated
(AOA→RC→DUML→aircraft), not phone-direct-Wi‑Fi.

---

## 7. WM160 supported-vs-NOT summary

**Supported / configured for WM160 (id 59):**
- App-level: in `supportedDroneList`; paired RC id 99 in `supportRcList`.
- Full connect-guide, product-names, compass/gimbal/IMU calibration UI, newbie guide.
- Playback capability `isSupportLTMByMediaMeta = true`.

**NOT supported / not configured for WM160:**
- Not a direct-Wi‑Fi drone (absent from `supportWifiDroneList`; no `wififast` config).
- No goggles (`supportedGogglesList` for WM160 = empty).
- No `takeoffandland` illustrations (falls back to `null`).
- No portrait newbie guide, no beacon/BCT config.

**Not answered by this domain at all** (look elsewhere): every other `isSupport*` in
`isSupport_keys.txt` — camera modes (HDR/HEVC/4K/ND/pano/quickshot…), flight capabilities (RTH,
GEO, propeller mode, VirtualJoyStick…), OTA/upgrade, media transfer. Those are decided by the
camera/flycontroller/media subsystems and by DUML capability pushes, **not** by productconfig.
Flight gating specifically is covered in `FLIGHT_GATING.md`.

---

## 8. NOT-WM160 / adjacent systems (disambiguation)

- `META-INF/services/com.lct.base.product.core.ProductConfigProvider` lists
  `AC206/HG214/OM307/OM308/OM507/OM508/OQ101/OW001/WA530 ConfigProvider`. This is a **different**
  product-config SPI for DJI's **handheld/gimbal** lines (OM=Osmo Mobile, OW/OQ/HG/WA/AC families).
  **NOT-WM160**, unrelated to the aircraft `com.uav/com.dji.productconfig` system above.
- All `OPR*` configs (fpv, rcbutton, rccalibration, rcnewbieguide, connectguide) are **remote-
  controller** configs, not aircraft. `GLS*` = goggles. WM160 references only RC id 99 from these.

---

## 9. Frida cheat-sheet (to verify on a live WM160 link)

- Confirm device id: hook `AircraftDeviceInfoModel.getDeviceId()` (expect `59`).
- Confirm drone object: hook `FlyConfigKey$keyDroneInfo$2$1.a(J)` → `DroneInfo{d=59}`.
- Confirm a capability answer: hook
  `com.dji.productconfig.playback.generate.UAV59PlaybackProductConfig.b(BaseDrone)` (expect `true`
  when the live aircraft matches).
- Enumerate the resolved list live: hook
  `com.dji.productconfig.app.generate.FlyAppProductConfig.g()` / `.b()` and check for `59`.
- Everything below `getDeviceId()` (the actual DUML type handshake) is in the packed native
  `flymodel` layer and must be captured on the wire / via native hooks, not in Java.
