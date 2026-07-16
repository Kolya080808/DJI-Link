# DOMAIN: Maps & Bluetooth (WM160 / Mavic Mini 1 = UAV59)

**Scope of this doc.** This is the "map SDK + Bluetooth" domain. Both are almost entirely
**client-side / third-party** concerns: the map is a phone-side rendering layer, and Bluetooth is
used **only** for the "QuickConnect / WiFiFast" credential-handoff flow that Wi-Fi drones and FPV
goggles use — **not** for anything on WM160. For your PC-control project neither subsystem is on the
drone-control path: WM160 is reached by RC → radio → drone (AOA/DUML), and the map/home/aircraft/NFZ
overlays are drawn purely from telemetry you already decode plus HTTP calls to DJI/third-party
servers. This doc documents what exists, cites it, and marks what is out-of-scope for WM160.

Evidence base: `unpacked_app_dex/` (16 DEX, disassembled with baksmali), `all_classes.txt`,
`full_table.txt`, `TELEMETRY_TABLE.txt`, `AndroidManifest.xml`. The `decompiled/smali/com/amap` +
`com/autonavi` trees are the **unpacked (unencrypted) AMap SDK**; the real DJI app logic is in the 16
DEX. All string/host/key values below are quoted from `strings -n` over the DEX unless noted.

---

## PART 1 — MAP SDK INTEGRATION

### 1.1 Architecture: `com.uav.mapkit` provider-abstraction layer

DJI does not call a map SDK directly from feature code. It wraps every backend behind its own facade
`com.uav.mapkit.core` ("Mapkit"), whose model/marker types are the `UAVApp*` classes. Feature code
(FPV map, flight-record map, NFZ map) only ever touches these:

| Mapkit core class (`Lcom/uav/mapkit/core/…`) | Role |
|---|---|
| `Mapkit` / `MapkitOptions` / `MapkitOptions$Builder` | init + config entry point |
| `maps/UAVAppMap`, `UAVAppBaseMap`, `UAVAppMapViewInternal`, `UAVAppMapFragment` | the map object/view |
| `maps/UAVAppProjection`, `UAVAppUiSettings`, `UAVAppInfoWindow` | camera / gestures / callouts |
| `camera/UAVAppCameraUpdate(Factory)` | pan/zoom |
| `models/annotations/UAVAppMarker`, `UAVAppPolyline`, `UAVAppPolygon`, `UAVAppCircle`, `UAVAppGroupCircle` | overlays (aircraft, home, path, NFZ) |
| `models/UAV{Circle,Line,Polygon,Symbol,Map}LayerManager` | style-layer managers (MapLibre style spec) |
| `providers/MapProvider` (`providerType:I`, linked-list `nextProvider`) | provider chain/selection |
| `config/MapConfigs` (`maptilerKey=…`), `constants/MapkitConstants` | keys + constants |
| `injector/MapCoreInjector(Manager)` | DI wiring of the concrete backend |

Concrete backends live in sibling packages, each ~30–90 classes:

| Package | Backend | Notes |
|---|---|---|
| `com.uav.mapkit.amap` (85) | **AMap / AutoNavi (Gaode)** | China map. SDK bundled unencrypted under `decompiled/smali/com/amap/**` + `com/autonavi/**`. Manifest key `com.amap.api.v2.apikey = 7eb94d5a03afc792bcabd0319c670bed`. |
| `com.uav.mapkit.maplibre` (78) | **MapLibre GL** (open-source Mapbox GL fork) | Global map. Release suffix seen in obfuscated names: `…$UAV_Mapkit_MapLibre_release`. Renders vector tiles from **MapTiler** (see 1.3). Underlying `com.mapbox.mapboxsdk.**` (709 classes) is the MapLibre native lib. |
| `com.uav.mapkit.google` (29) | **Google Maps** | `com.google.android.gms.maps.**` (262). Global fallback. Geocode via `ditu.google.cn` / `maps.googleapis.com`. |
| `com.uav.mapkit.lbs` (51) | **Location/geocoding abstraction** (`ProviderType` enum) | see 1.4 |
| `com.uav.mapkit.mapbox` (1), `com.uav.mapkit.config` (2) | glue | |
| `com.uavapp.mapkit.osm.**` (90+) | **OSM / MapTiler offline** | offline-region download (`MaptilerOfflineDownloadManager`, `MapTilerSdkManager`) |

`MapProvider` is a **chain-of-responsibility** (`providerType:I` + `nextProvider`): Mapkit tries a
provider and falls through. The enum of provider ids is the obfuscated annotation-interface
`com.uav.mapkit.lbs.constants.ProviderType` with fields `G2..M2 = 0x0..0x6` (names stripped; the
integer ids are not human-labelled in the binary — **resolve live if you need the exact id→backend
map**, e.g. hook `MapProvider.<init>` / `getProviderType`). Region gating is present: a log string
`"Configuration requires not to use AMap provider, abort!"` shows AMap is disabled outside China.

### 1.2 What the map draws for WM160, and where the data comes from

The map itself has **no DUML of its own** — every dynamic element is fed from telemetry you already
decode, or from HTTP. WM160-relevant overlays:

**Aircraft marker** — driven by telemetry key `AircraftLocation` (src `uav_fc_osd_push`, i.e. FC
cmd_set **0x03** OSD push; `TELEMETRY_TABLE.txt:223`). Heading for the marker rotation comes from the
same OSD attitude fields. The FPV-map location feed is `com.uav.component.fpv.widget.map.
FpvScopeDroneLocationProvider` (pulls from the `FlyModel`/`FlyScope` telemetry facade).

**Home point marker + return-home circle** — telemetry keys `HomeLocation`, `HomeLocationType`,
`IsHomeLocationSet`, `DynamicHomePointState` (all `core::uav_cmd_rsp`; `TELEMETRY_TABLE.txt:223`
block). Home is *set* via DUML `0x03/0x31 uav_fc_set_homepoint_req/rsp` and dynamically pushed via
`0x22/0xCB uav_fc2_RC_DYN_HOMEPOINT_INFO_push` (`full_table.txt`). The on-map ring is
`com.uav.component.fpv.widget.map.HomeCircleContract`.

**Flight path / RTH trajectory** — `GoHomeTrajectory` (`core::uav_cmd_rsp`), drawn as a
`UAVAppPolyline`.

**NFZ / no-fly / geofence polygons** — see 1.5.

**Map-type toggle** (WM160 UI) — string resources `fpv_basic_flight_map_type_normal_btn` and
`fpv_basic_flight_map_type_mixing_btn` (standard vs. satellite-"mixing"/hybrid); switcher package
`com.uav.component.fpv.mapswitch` (156 classes) with the WM160 gate
`com.uav.component.fpv.mapswitch.UAV59MapSwitchGate`.

### 1.3 MapTiler (MapLibre tile source) — hosts, styles, key

MapLibre pulls vector tiles + styles from **MapTiler**. Hard-coded style URLs (three basemaps):
```
https://api.maptiler.com/maps/277aae2e-33e2-4dce-8283-5710deb055d8/style.json
https://api.maptiler.com/maps/6607a7ed-4424-4cc3-b81c-17dd62410e02/style.json
https://api.maptiler.com/maps/c708bce6-9de4-4f8d-9a1d-3006c7004493/style.json
```
MapTiler API key (query param `key=…`, also `MapConfigs.maptilerKey`): **`8K0f4jrzPb3pV0MhDGcz`**.
Offline regions handled by `com.uavapp.mapkit.osm.offlinemap.dataimpl.maptiler.
MaptilerOfflineDownloadManager` / `MapTilerSdkManager`.

### 1.4 Geocoding / places / elevation (HTTP, third-party + DJI)

| Purpose | Endpoint | Backend |
|---|---|---|
| Reverse-geocode (global) | `https://mydjiflight.dji.com/api/v2/geocoder_service/geoip` | DJI |
| Reverse-geocode (CN) | `http://ditu.google.cn/maps/api/geocode/json?latlng=…` | Google CN |
| Geocode / POI (CN) | `http://restapi.amap.com/v3/geocode/regeo`, `/v3/place/around`, `/v3/place/text`, `/v3/config/district` | AMap REST |
| AMap SDK auth | `https://restapi.amap.com/v3/iasdkauth` | AMap |
| AMap device-locate | `http(s)://apilocate.amap.com/mobile/binary`, `abroad.apilocate.amap.com/mobile/binary` | AMap |
| **Terrain / DEM** (RTH altitude, terrain-follow) | `https://digital-elevation.djigate.com/dem/api/v2/map/resources/search` | DJI |

`com.uav.mapkit.lbs` is the location facade over these (`ProviderType` enum picks CN-AMap vs.
global). The `lbs` location provider is what supplies the phone's own position marker (blue dot).

### 1.5 NFZ / FlySafe / GEO on the map — WM160-relevant

The no-fly-zone overlay is a full subsystem. On the map it is drawn by the FPV widget package
`com.uav.component.fpv.widget.map.flyforbid.*`:
`FlyForbidController`, `FlyForbidVM`, `FlyforbidCircle`, `FlyforbidPolygon`, `FlyfrbGsEventHandler`,
`FlyfrbPointAreaCaculator`, `WarnAreaAlertController`, plus the lower-level painters
`uav.gs.manager.FlyForbid.{FlyForbidBasePainter,FlyForbidController,FlyForbidDrawParam,
FlyfrbAreaClickUtil}` and `uav.component.flightrestrict.{FlyForbidBasePainter,FlyForbidProtocol,
model.FlyForbidElement,model.FlyForbidDrawParam}`. Data model + local DB:
`uav.midware.data.forbid.{UAVFlyForbidController,UAVSetFlyForbidAreaModel,model.FlyForbidElementAirMap,
db.FlyforbidDbManager,util.FlyforbidUtils,NewNfzDesc}`. NFZ type enums:
`com.uav.proto.flightrestrict.NFZType`, `com.uav.proto.flyrecord.NFZArrType` (**exact wire values
live in those enum classes — resolve with baksmali of `com/uav/proto/flightrestrict/NFZType.smali`
if you need them; not decoded here**).

**Where NFZ geometry comes from (HTTP):**
```
https://flysafe-api.dji.com/           (primary NFZ/GEO area + unlock-license API)
https://flysafe.dji.com , https://www.dji.com/cn/flysafe
https://flysafe-7qgvdlsy.aasky.net     (CDN/mirror)
https://api.airmap.io/data/v1/error-report , https://cdn.airmap.io/airmap.js/1.0.3/verify.html
                                       (AirMap — third-party US/global geofence data + web verify)
```
The `NFZUnlockAreaMapActivity` (`com.uav.unlocklicenselist.map.*`, manifest-registered) is the
unlock-license map screen; unlock is licence-based, tied to the drone SN, requires login (see
`FLIGHT_GATING.md:61`).

**NFZ that involves the aircraft over DUML (WM160):** the FC carries its own NFZ database and reports
its state — DUML cmd_set 0x03:
| cmd | name |
|---|---|
| `0x03/0xBB` | `uav_fc_get_nfzdb_upgrade_status_query_req/rsp` |
| `0x03/0xBC` | `uav_fc_get_nfzdb_upgrade_result_query_req/rsp` |
| `0x03/0xBD` | `uav_fc_nfz_upgrade_exit_req/rsp` |
(`full_table.txt`.) Actual per-zone enforcement / max-radius geofence gating is documented in
`FLIGHT_GATING.md` (e.g. geofence radius `0x03/0x2D`, `advanced_function.radius_limit_enabled[207]`),
**not repeated here** — that is the flight-gating domain. For PC-control you can ignore the phone-side
NFZ overlay entirely; the FC enforces limits on its own.

### 1.6 Other map consumers (context, mostly NOT-WM160-specific)

- `com.uav.component.fpv.widget.map.airsense` (71) — **ADS-B AirSense** traffic overlay. WM160 has
  no AirSense receiver → **NOT-WM160** (empty on Mini 1).
- `…/mastershot`, `…/hyperlapse` (`WayPointMarkerBitmapFactory`) — MasterShots / Hyperlapse route
  overlays. WM160 does not support these intelligent-flight modes → **NOT-WM160**.
- `com.uav.flightrecordui.map` (88) — post-flight track map in flight records (all models).
- `com.uav.flymodel.generated.map.*` — note: these are **NOT map-SDK** classes; "map" here is
  Kotlin/DataStore key-mapping codegen for the FlyModel telemetry layer. Ignore for this domain.

---

## PART 2 — BLUETOOTH

### 2.1 What Bluetooth is used for: WiFiFast / QuickConnect ONLY

There is **no generic Bluetooth control path**. Every Bluetooth reference in the app lives under the
Wi-Fi "QuickConnect" service `com.uav.service.wififast.*`:
`WifiFastConnectCore` (methods `connectBluetooth`, `connectBluetoothOnly`, `bluetoothAuthenticate`,
`authBluetoothStream`, `wakeupBluetoothDevice`, `observeBluetoothDisconnect`,
`connectBluetoothAndGetWifiInfo`) and `WifiFastAssistant$bluetoothDisconnectStream`. The flow (from
class/method names + backlog strings `Backlog.WifiConnectBacklog.Bluetooth.{getWifiSsid,verifyDevice}`,
`v1WifiConnectBacklogGetBleConnectStatusErrorCode`):

1. **BLE scan/wake** the drone or goggles (`wakeupBluetoothDevice`).
2. **BLE authenticate/verify** the device (`bluetoothAuthenticate` / `Bluetooth.verifyDevice`).
3. **Read Wi-Fi credentials over BLE** (`Bluetooth.getWifiSsid` → SSID/PW).
4. **Hand off to Wi-Fi** and connect the video/control socket (`startSwConnectRequest`); BLE is then
   dropped (`bluetoothDisconnectStream`). Error string `"skipble wifi connect fail clean crc8"`.

So Bluetooth = *a way to bootstrap a Wi-Fi link*, not a data link itself.

### 2.2 Is this used on WM160? — NO

**WM160 does not use Bluetooth.** Mavic Mini 1 has no BLE radio and no direct-Wi-Fi bind flow; it
binds to its dedicated RC (**MR1SD25**, fw v4.2.2.60 per project memory) and the app reaches the RC
over USB/radio — the exact path your project already drives (PC → Pi → USB-AOA → RC → radio → drone).
Evidence the QuickConnect/WiFiFast config is product-gated and WM160 is a plain RC drone:
- Product-config split by device family: `com.uav.productconfig.wififast.generate.
  {DroneWifiFastProductConfigList, GoggleWifiFastProductConfigList, BCTWifiFastProductConfigList}` and
  keys like `keySupportWifiCapabilityListByDrone`, `keySupportWifiGlassListByGoggle` — i.e.
  QuickConnect applies to Wi-Fi-capable drones and FPV goggles, enumerated per-product.
- WM160 identity string in resources is `fly_uav59_wm160` (confirming **WM160 = UAV59**; sibling
  `fly_uav96_wm1605` = Mini SE = UAV96). WM160's control strings are RC-based, e.g.
  `uav59_brakereturnhome_genenal_rc_160`.
- The WM160 FPV map is wired through `UAV59MapShellGate` / `UAV59MapSwitchGate` — normal RC-drone
  map, no Wi-Fi/BLE connection widget.

**Undecidable-statically caveat:** the exact per-product membership list inside
`keySupportWifiCapabilityListByDrone` is built by an obfuscated lambda (`WifiFastProductConfigKey$…$2`)
and I could not fully resolve the concrete product-id array from bytecode alone. This does not change
the conclusion (WM160 has no BLE hardware), but if you want positive proof that UAV59 is absent from
the Wi-Fi/BLE product list, hook it live: **Frida on
`Lcom/uav/service/wififast/WifiFastConnectCore;->connectBluetooth*`** (should never fire for WM160) or
dump the return of the config getter in `com.uav.productconfig.wififast.generate.*`.

### 2.3 Manifest Bluetooth permissions (present, for QuickConnect)

`AndroidManifest.xml` declares the full BLE set — required by QuickConnect, irrelevant to WM160:
`BLUETOOTH`, `BLUETOOTH_ADMIN`, `BLUETOOTH_ADVERTISE`, `BLUETOOTH_CONNECT`, `BLUETOOTH_SCAN`, plus
`ACCESS_FINE/COARSE/BACKGROUND_LOCATION` (BLE-scan requirement + map "my location").

---

## PART 3 — SUMMARY TABLE: WM160 support

| Item | WM160? | Source of truth |
|---|---|---|
| FPV map view (aircraft/home/path) | **YES** | `UAV59MapShellGate`, telemetry `AircraftLocation`/`HomeLocation` (0x03 OSD / `uav_cmd_rsp`) |
| Map-type toggle (normal/hybrid) | **YES** | `UAV59MapSwitchGate`, res `fpv_basic_flight_map_type_*` |
| Home-point set / RTH ring | **YES** | DUML `0x03/0x31`, `0x22/0xCB`; `HomeCircleContract` |
| NFZ/FlySafe overlay + FC NFZ-DB | **YES** | `flyforbid.*` painters; DUML `0x03/0xBB–0xBD`; `flysafe-api.dji.com` |
| Map SDK backend | **third-party** | AMap (CN) / MapLibre+MapTiler (global) / Google — all phone-side |
| Terrain/DEM, geocoding | **HTTP** | `digital-elevation.djigate.com`, `mydjiflight.dji.com`, AMap/Google |
| AirSense (ADS-B) map layer | **NO** (NOT-WM160) | `…/map/airsense` — no receiver on Mini 1 |
| MasterShots / Hyperlapse route | **NO** (NOT-WM160) | `…/map/mastershot`, `…/hyperlapse` |
| **Bluetooth (any)** | **NO** (NOT-WM160) | BLE only in `com.uav.service.wififast.*` QuickConnect; WM160 is RC-bound (MR1SD25) |

**Bottom line for PC-control:** ignore both subsystems on the control path. Reuse only the telemetry
you already decode to render your own aircraft/home markers; if you want an offline basemap, MapTiler
key `8K0f4jrzPb3pV0MhDGcz` + the three style URLs above work with any MapLibre renderer. Bluetooth is
dead code for WM160.

---

### Live-capture / Frida TODO (things undecidable behind the packer)
- `ProviderType` int→backend mapping: hook `com.uav.mapkit.core.providers.MapProvider` ctor / getters.
- `NFZType` / `NFZArrType` wire values: baksmali `com/uav/proto/flightrestrict/NFZType.smali`.
- Positive proof UAV59 ∉ Wi-Fi/BLE product list: hook `WifiFastConnectCore->connectBluetooth*` (never fires) and dump `com.uav.productconfig.wififast.generate.*` config getters.
