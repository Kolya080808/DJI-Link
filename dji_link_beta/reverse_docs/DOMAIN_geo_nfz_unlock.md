# DOMAIN: geo_nfz_unlock — Geo / NFZ / Flight-restriction / Unlock (WM160 / Mavic Mini 1 / UAV59)

Scope: everything under `uav.pilot.flyforbid`, `uav.pilot.flyunlimit`,
`uav.component.flightrestrict`, the `DataFlycGetPushForbidStatus` FC push, and the
license/unlock server flow. Filtered to **WM160 (Mavic Mini 1, sub-250 g)**.

Evidence sources (all paths absolute):
- Disassembled DEX (baksmali of `/mnt/c/users/nikolay/Downloads/reversing/dji_link_beta/reverse_docs/unpacked_app_dex/*.dex`).
  Class → DEX map is given inline; I cite `class → method → smali fact`.
- `/mnt/c/users/nikolay/Downloads/reversing/dji_link_beta/reverse_docs/DUML_COMMANDS_FULL.md` (builder-verified DUML layouts).
- `/mnt/c/users/nikolay/Downloads/reversing/dji_link_beta/reverse_docs/TELEMETRY_TABLE.txt` (native push field map, VA= = native flymodel lib address).
- `/mnt/c/users/nikolay/Downloads/reversing/dji_link_beta/reverse_docs/cmdmap.txt`, `full_table.txt`, `isSupport_keys.txt`, `FLIGHT_GATING.md`.

Everything below is static evidence. Anything crypto / signature / firmware-side is behind the
native lib **`FRCorkscrew`** (`System.loadLibrary("FRCorkscrew")`, class
`uav/component/flightrestrict/...` JNI callbacks) and is flagged as needing a live capture / Frida.

---

## 0. TL;DR for a WM160 PC ground-station

- WM160 is sub-250 g → the vast majority of geo zones are **advisory ("WARNING", wire 0)**: the FC
  pushes a state, the app shows a toast, **nothing is physically blocked**. Motors still start,
  takeoff still happens.
- The genuinely enforced states come from the **onboard NFZ database inside the FC firmware**, not
  from the app: **`CAN_NOT_UNLIMIT` (wire 2)** = hard no-fly (airport cores / national TFRs) and the
  height limits. These are enforced by the FC regardless of app.
- **`CAN_UNLIMIT` (wire 1)** = "authorization zone": flyable only after a **DJI-signed unlock license**
  is uploaded to the FC (DUML `0x03/0x41` then enabled with `0x03/0x47`).
- In **this app build the online GEO map layer is compiled OFF**:
  `uav/midware/data/forbid/util/FlyfrbSupportUtil;->isSupportGeoFlyforbid()Z` **returns a hard-coded
  `false`** (the only impl in the app). The onboard NFZ DB and the FC limit pushes still work — they
  are firmware, not app.
- There is **no pure app-side override**: enforcement lives in FC firmware + `FRCorkscrew` signature
  check. The app can only (a) push a DJI-signed license, or (b) push a signed NFZ DB. Forging either
  needs DJI's private key (undecidable statically — inside `FRCorkscrew`).

---

## 1. The two independent subsystems (do not conflate)

**(A) App-side GEO map / online flyforbid + unlock UI** — packages `uav.pilot.flyforbid`,
`uav.component.flightrestrict`, `uav.pilot.flyunlimit`. Draws zones on a map, downloads the dynamic
NFZ DB, runs the licensed-unlock flow. **Gated off in this build** (`isSupportGeoFlyforbid()==false`).

**(B) FC-side flight restriction** — the FC firmware carries an **onboard NFZ DB** and continuously
pushes the aircraft's current limit state to the app via `DataFlycGetPushForbidStatus` + the native
`uav_fc_no_fly_area_push` / `uav_fc_fc_osd_lowfreq_push` topics. This runs **independently of (A)**
and is what actually gates the aircraft. A PC ground-station must understand (B); (A) is optional UI.

---

## 2. The FC → app status push: `DataFlycGetPushForbidStatus`

Class: `uav/midware/data/model/P3/DataFlycGetPushForbidStatus`
(DEX `classes_0451d00c`). Singleton, extends `uav/midware/data/manager/P3/DataBase`; all fields are
read via `DataBase.get(offset, size, Class)`. `DYNAMIC_DATA_START_INDEX = 0x7`.

This is the **LimitAreaLevel / FlyForbidStatus push** the domain asks about. Confirmed byte layout
(from the getter offsets):

| Getter | `get(off,size)` + mask | Field |
|---|---|---|
| `getFlightLimitAreaState()` | `get(0,1)` | byte 0 = `UAVFlightLimitAreaState` |
| `getVersion()` | `get(1,1) & 0x0f` | byte 1 low nibble = protocol version |
| `getUAVFlightLimitActionEvent()` | `get(1,1)` (high nibble) | byte 1 high nibble = action event |
| `getLandingCountdown()` | `get(3,1) & 0x7f` | byte 3 = forced-landing countdown (s) |
| `getLicenseUnlockVer()` | `get(4,1) & 0x03` | byte 4 bits0-1 |
| `getGohomeFrbAreaState()` | `get(4,1)` bits (`&0x03` then shift) | byte 4 = go-home forbid area state |
| `isSupportLicenseUnlock()` | `get(4,1) & 0x20` | **byte 4 bit5 = FC advertises licensed unlock** |
| `getLimitMaxHeight()` | `get(5,2)` u16 LE | bytes 5-6 = max allowed height (m) |
| `getNfzNum()` / `getNewState()` / `getNewNfzDescs()` | from `mCurNewStartIndex` (≥ 7) | byte 7+ = dynamic NFZ descriptor list |

> The exact **cmd_set/cmd_id** that transports this push is NOT in `cmdmap.txt` (that file only lists
> req/rsp pairs, not the P3 subscription pushes). `doPack()` in the class is empty (receive-only).
> It is cmd_set `0x03` (FLYC) but the cmd_id is assigned by the `DataBase` receiver registry, which is
> not statically dumpable here. **Live capture / Frida needed** — hook
> `DataFlycGetPushForbidStatus->onData(...)` or the DUML dispatcher to read the real cmd_id and raw bytes.

### 2.1 Enums with wire values (all from the `<clinit>` of the nested classes)

`DataFlycGetPushForbidStatus$UAVFlightLimitAreaState` (byte 0):
| name | wire |
|---|---|
| None | 0 |
| NearLimit | 1 |
| InHalfLimit | 2 |
| InSlowDownArea | 3 |
| InnerLimit | 4 |
| InnerUnLimit | 5 |
| OTHER | 100 (0x64) |

`DataFlycGetPushForbidStatus$UAVFlightLimitActionEvent` (byte 1 high nibble) — **what the FC is doing**:
| name | wire | meaning |
|---|---|---|
| None | 0 | no action |
| ExitLanding | 1 | leaving forced-landing |
| Collision | 2 | boundary collision |
| StartLanding | 3 | forced landing started |
| StopMotor | 4 | **motors force-stopped** |
| OTHER | 100 | |

`DataFlycGetPushForbidStatus$NewFlyfrbState` (dynamic NFZ descriptor state, byte 7+):
| name | wire |
|---|---|
| OUTSIDE_LIMIT | 0 |
| LOCATION_UNKNOWN | 1 |
| SEEM_IN_LIMIT | 2 |
| PHONE_IN_LIMIT | 3 |
| UAV_IN_LIMIT | 4 |
| SEEM_IN_LIMIT_HEIGHT | 5 |
| PHONE_IN_LIMIT_HEIGHT | 6 |
| UAV_IN_LIMIT_HEIGHT | 7 |
| IN_WHITE_AREA | 8 |
| OTHER | 100 |

`DataFlycGetPushForbidStatus$GohomeFrbAreaState` (byte 4):
| name | wire |
|---|---|
| NORMAL | 0 |
| TANGENT_AREA | 1 |
| CROSS_AREA | 2 (subType 3) |
| OTHER | 255 (0xff) |

### 2.2 The zone-level enum: `FlyForbidProtocol$LevelType` — **this is "LimitAreaLevel"**

Class `uav/component/flightrestrict/FlyForbidProtocol$LevelType` (DEX `classes_03a5700c`).
Constructor `<init>(String name, int ordinal, int _data, [int _subType])`; field `data:I` = **wire value**:

| name | wire (`data`) | subType | WM160 meaning |
|---|---|---|---|
| WARNING | 0 | – | advisory only, **not blocked** |
| CAN_UNLIMIT | 1 | – | authorization zone → needs signed unlock license |
| CAN_NOT_UNLIMIT | 2 | 4 | **hard no-fly**, enforced by FC, cannot be self-unlocked |
| STRONG_WARNING | 3 | – | strong advisory (confirm dialog), not blocked |
| UTMISS_REGULATION | 7 | – | regulatory (region-specific) |
| UTMISS_LAW_ALLOW | 8 | – | legally-allowed marker |
| FAMOUS_AREA | 10 (0xa) | – | landmark advisory |

Also `FlyForbidProtocol$UAVWarningAreaState`: `None=0`, `NearLimit=1`, `InnerLimit=2` (subType 4).
`FlyForbidProtocol$UAVCEApproachMode` and `$ShowLimitCircleSUEvent` exist (EU CE handling — see §6).

### 2.3 Native (firmware-parsed) limit telemetry — `TELEMETRY_TABLE.txt`

These come from native flymodel pushes (VA = address in the native lib); no Java offset:
- `uav_fc_no_fly_area_push` → `FlightLimitHeight`, `IsFlightLimitUsedOnBoardDB` (i.e. FC uses its
  onboard DB, confirming subsystem B).
- `uav_fc_fc_osd_lowfreq_push` → `HeightLimitReason`, `IsNearHeightLimit`, `IsNearDistanceLimit`,
  `LimitMaxFlightHeightInMeter`.
- `uav_fc_flylimit_version_push` → `StaticDBVersion`, `FlyLimitDBVersion`, `FlightrestrictDBType`,
  `DynamicDBFileMaxSizeMB`.
- `uav_adsb_flightrestrict_config_push` → `IsSupportReleaseLimitHeight`.
- `uav_general_ce_info_show_push` → EU CE compliance banner.
- `core::uav_cmd_rsp` → `FCWhiteListUnlimitEnable`, `TouchDownConfirmLimitHeight`.

---

## 3. App → FC DUML commands (cmd_set 0x03 = FLYC)

From `DUML_COMMANDS_FULL.md` (builder-verified layouts). These are the levers a PC replacement uses.

| cmd_id | app class | payload (request) |
|---|---|---|
| `0x08` | `DataFlycSetFlyForbidArea` | 1B type; 4B const0; 4B lat; 4B lng; 2B radius; 2B countryCode; 2B countryCode; 4B revers *(conditional size)* |
| `0x3F` | `DataFlycSetFlyForbidAreaData` | 1B type; 1B fragNum; 3×1B const0; 4B lat; 4B lng; 2B radius; 2B countryCode×2; 4B id |
| `0x41` | **`DataFlycUploadUnlimitAreas`** | len=133B: 1B const0; 1B packetIndex; …; 1B data — **uploads an unlock license/area blob to the FC in 128-byte-payload packets** |
| `0x47` | **`DataFlycEnableUnlimitAreas`** | 1B `enableUnlimitAreas` — **arms/disarms the uploaded unlock areas** |
| `0xCC` | `DataFlycGetSetWarningAreaEnable` | 1B const0; 4B mAreaId; 1B const1 |
| `0xCD` | **`DataFlycUpdateFlyforbidArea`** (recv SINGLE) | 1B mType; 2B mPkgSeq; 4B mPkgTotalSize; 1B const3; 3B mData — **streams a new NFZ DB into the FC** |
| `0xE9` | `DataFlycSetFlyforbidData` | 1B mDataType |

Name-only (layout not asserted; from `cmdmap.txt` / `full_table.txt`) — the **onboard NFZ DB upgrade**
handshake, cmd_set `0x03`:
- `0xBB` `uav_fc_get_nfzdb_upgrade_status_query` (187)
- `0xBC` `uav_fc_get_nfzdb_upgrade_result_query` (188)
- `0xBD` `uav_fc_nfz_upgrade_exit` (189)

> The unlock-license lifecycle end-to-end is therefore: fetch/verify license on the server (§4) →
> `0x41` upload → `0x47` enable → FC reflects it back in `DataFlycGetPushForbidStatus`
> (`isSupportLicenseUnlock` bit, `getLicenseUnlockVer`).

---

## 4. The unlock / license flow (app-side)

Manager: `uav/pilot/flyunlimit/UAVFlyUnlimitManager` (DEX `classes_0451d00c`), singleton via
`getInstance(Context)`. Public surface (obfuscated method letters):
- `k/n/j(...GetUnlockListCallback)` — get unlock/license list (server).
- `x(ArrayList, UAVUnlockConfirmCallback)` / `y(UAVUnlockConfirmCallback)` — confirm/apply unlock.
- `w(D,D,String,I,CommonHttpCallback)` — query by lat/lng (area lookup).
- `l():Single`, `c(...AccountStateBeforeUnlock)` — account-state gating before unlock.

### 4.1 License types — `unlock/model/LicenseType` (DEX `classes_03a5700c`)
`<init>(name, ordinal, data, Class)`; `data == ordinal` here:
| name | wire | model class |
|---|---|---|
| GEO_UNLOCK | 0 | `GeoUnlockLicense` |
| CIRCLE_UNLOCK_AREA | 1 | `CircleUnlockAreaLicense` |
| COUNTRY_UNLOCK | 2 | `CountryUnlockLicense` |
| PARAMETER_CONFIGURATION | 3 | — |
| PENTAGON_UNLOCK_AREA | 4 | `PentagonUnlockAreaLicense` |
| UNKNOWN | 255 (0xff) | — |

Other license models in `uav/component/flightrestrict/unlock/model/`: `HeightUnlockLicense`,
`WhiteListLicense` (+`$Builder`), `FlyfrbLicenseV3Info`, `FlyfrbLicenseV3GroupData`,
`AccountStateBeforeUnlock`.

### 4.2 Self-unlock vs custom (licensed) unlock
- **Self-unlock (authorization zones, level `CAN_UNLIMIT`=1):** user with a logged-in DJI account
  self-authorizes. Evidence: analytics event string `reportAuthorizedZoneSelfUnlockFinishPage`
  (flyunlimit). For WM160 this is the common case for the few authorization zones that apply.
- **Custom / licensed unlock (`CAN_NOT_UNLIMIT` hard zones):** requires a DJI-issued **custom unlock
  license** bound to the aircraft SN and signed. Fetched from server, then uploaded via `0x41`/`0x47`.
- **White-list unlock:** `WhiteListLicense` + FC-side `FCWhiteListUnlimitEnable` /
  `NewFlyfrbState.IN_WHITE_AREA(8)`.
- **Offline unlock:** `UAVFlyUnlimitManager$OfflineUnlockDataType`, `OfflineLicenseListResult`
  (`$LicensesData`) — cached licenses usable without live network.

### 4.3 Server-side (HTTP)
HTTP helpers: `uav/pilot/flyunlimit/util/FlyfrbHttpHelper` (okhttp + `com.uav.thirdparty.badon.FinalHttp`),
`FrbStaticDBHttpHelper` (static DB download). Requests are **signed** (methods `b([B,[B,String)Z` =
signature verify; log `"getLicenseAreaList result signature wrong"`). **Login is required** to pull
the license list: log strings `"getLicenseAreaList app not log"`, `"checkUavLicenseState error"`.

License-list JSON (`jsonbean/UAVLicenseUnlockListResult$ListData`) fields bind the license to the
aircraft and area:
`sn`, `account`, `areas_id`, `areas_type`, `country`, `city`, `location`, `places`,
`begin_at`/`begin_time`, `end_at`/`end_time`, `status`, `disable`, `type`, `signature`, `timezone`, `os`.
`SDKUnlockListItem` adds `area_id`, `flycsn`, `latitude`, `level`, `height`, `license_id`.

Hosts seen in the geo/unlock code paths (base URL is assembled at runtime via CloudControl host
config, so treat these as endpoints, not the full base):
- `https://account-api.dji.com` — account/auth.
- `https://api.airmap.io/data/v1/error-report`, `https://cdn.airmap.io/airmap.js/1.0.3/verify.html`
  — AirMap GEO verify webview + error reporting (DJI's GEO provider).
- `https://api.uavservice.org/` — present in `CloudControl/config/CloudControlNameSpacesConfig`
  (this OEM build's service base).

> The precise license-fetch URL and request body are built at runtime and best captured live
> (hook `FlyfrbHttpHelper` / okhttp `Request`). **Frida needed** for exact endpoint + payload.

### 4.4 Native crypto — `FRCorkscrew`
`System.loadLibrary("FRCorkscrew")` is invoked from the flightrestrict path. Signature verification,
license decode, and NFZ DB decode are native. NFZ DB assets: `uav.nfzdb2.confumix`, `uav.nfzdb2.sig`.
JNI bridges: `listener/JNIUnlockCommonCallbacks` (`$JNIUnlockCallback`, `$JNIUnlockError`,
`$JNICheckWillApplyTFRSCallback`), `JNICommonCallbacks`, `JNIUpgradeDatabaseCallbacks`,
`util/ProtobufHelper`. **Everything cryptographic is here and is undecidable statically.**

---

## 5. What is actually blocked vs warned on a WM160

| FC state / level | Blocked? | Notes |
|---|---|---|
| `WARNING` (0), `STRONG_WARNING` (3), `FAMOUS_AREA` (10) | **No** | advisory toast/dialog only |
| `CAN_UNLIMIT` (1) authorization zone | Yes until unlocked | self-unlock (logged-in account) or license |
| `CAN_NOT_UNLIMIT` (2) | **Yes (FC-enforced)** | airport cores / TFR; needs DJI **custom** license |
| Height limit (`LimitMaxFlightHeight`, `FlightLimitHeight`) | Yes (FC clamps) | `getLimitMaxHeight` bytes 5-6; also `uav_fc_no_fly_area_push` |
| Forced landing (`ActionEvent StartLanding/StopMotor`) | Yes | FC lands / stops motors inside a hard NFZ |

Because WM160 is sub-250 g, DJI classifies most zones as advisory; the enforced set is dominated by
airport no-fly cores and explicit TFRs carried in the onboard DB.

---

## 6. WM160 support matrix

`isSupport_keys.txt` capability keys relevant here: `isSupportGeoFlyforbid`, `isSupportLicenseUnlock`.

- **`isSupportGeoFlyforbid()` → hard-coded `false`** in the only implementation
  (`uav/midware/data/forbid/util/FlyfrbSupportUtil`). The sibling `notSpprtGeoOnlineCheck()` /
  `isUseSlaveMode()` route through `UAVUSBWifiSwitchManager.g(ProductType)`. **Interpretation:** the
  full **online GEO map + online DB-check UI is disabled in this app build** (it is an OEM/beta
  `dji_link` build). This is a *build-wide* flag, not WM160-specific, but the net effect for WM160 is
  the same: no in-app GEO map layer. The onboard NFZ DB and FC pushes are unaffected.
- **`isSupportLicenseUnlock`** is **not a static constant** — it is read live from
  `DataFlycGetPushForbidStatus` byte 4 bit 0x20, i.e. the **FC advertises it at runtime**. So whether
  licensed unlock is usable on a given WM160 unit must be read from the push. **Live read needed.**
- EU-only paths (`FlightRestrictEuropeanUnionComplianceHttpApi`, `UAVCEApproachMode`,
  `CEDatabaseType`, `uav_general_ce_info_show_push`) apply only in CE regions — mark **region-gated,
  not WM160-specific**.
- `MainFlightRestrictMapRepository` / `MainFlightRestrictView` (`com.uav.mainpageui`) is the map UI;
  inert when `isSupportGeoFlyforbid()==false`. **NOT the enforcement path.**

---

## 7. Local override — feasibility for a PC ground-station

- **No app-only override exists.** Enforcement is FC firmware + `FRCorkscrew` signature check. The
  app's role is limited to: pushing a **DJI-signed** license (`0x41`+`0x47`), pushing a **signed** NFZ
  DB (`0xCD`, `0xBB/0xBC/0xBD`), or toggling warning-area enable (`0xCC`).
- To *fly a WARNING zone*: nothing needed — advisory.
- To *fly a `CAN_UNLIMIT` authorization zone*: a valid self-unlock/license must reach the FC. Without
  DJI's signing key you cannot mint one; the FC rejects unsigned uploads (signature checked in
  `FRCorkscrew`). **Undecidable statically whether any bypass exists** — it lives in the native lib.
- To *fly a hard `CAN_NOT_UNLIMIT` zone*: requires a DJI **custom** license — same signing barrier.
- Practical PC-side actions that do NOT need the server: read `DataFlycGetPushForbidStatus` to know
  current level/height/countdown, avoid hard zones, and rely on the sub-250 g advisory classification.

### Frida / live-capture targets (the static gaps)
1. **Real cmd_id of the forbid-status push** — hook the DUML receive dispatcher or
   `uav.midware.data.model.P3.DataFlycGetPushForbidStatus->onData/setRecData` and log raw bytes.
2. **`FlyfrbSupportUtil.isSupportGeoFlyforbid`** — confirm it is genuinely `false` at runtime (not
   patched by the packer) and observe `UAVUSBWifiSwitchManager.g(ProductType)`.
3. **`DataFlycGetPushForbidStatus.isSupportLicenseUnlock()` / `getFlightLimitAreaState()` /
   `getNewState()`** on the live WM160 to read the enforced state.
4. **License upload path** — hook `DataFlycUploadUnlimitAreas` (`0x41`) and
   `DataFlycEnableUnlimitAreas` (`0x47`) builders to capture the exact 133-byte packet contents.
5. **Server call + signature** — hook `uav/pilot/flyunlimit/util/FlyfrbHttpHelper` (okhttp `Request`,
   methods `a/b/f/g/h`) for the real license-fetch URL/body, and the `FRCorkscrew` JNI exports
   (`JNIUnlockCommonCallbacks$JNIUnlockCallback`) for the signature/verify logic.
