# DOMAIN: push_lte_analytics — WM160 (Mavic Mini 1 / UAV59) reference

Scope: DJI Fly v1.21.4 (`DJI-Fly-v1.21.4.apk`). Covers three sub-systems:

1. **Push** — `com.dji.dpush` (DJI's multi-vendor push abstraction) + `com.dji.fcmpack` (Google FCM channel).
2. **LTE / cellular** — `uav.component.lte` (eSIM / 4G-dongle cloud service) + `com.uav.component.bglogic.lte.LteSettingLogic`, DUML **cmd_set 0x18 (24) `cellular4g`**.
3. **Analytics / privacy** — `uav.common.aopanalytics` (native AOP telemetry engine) + `com.uav.privacyconfig` (consent / opt-out).

Evidence base: DEX disassembled with `baksmali` from `unpacked_app_dex/` (dpush+fcmpack = `classes_04e4400c.dex`; lte+aopanalytics+flymodel enums = `classes_03a5700c.dex`/`classes_0855200c.dex`; LteSettingLogic + app analytics glue + privacyconfig = `classes_07a5000c.dex`/`classes_00b9d00c.dex`), plus `cmdmap.txt`, `DUML_COMMANDS_FULL.md`, `isSupport_keys.txt`, `decompiled/AndroidManifest.xml`, `decompiled/lib/arm64-v8a/`.

---

## TL;DR — WM160 applicability matrix

| Sub-system | WM160 (UAV59) status | Note |
|---|---|---|
| Push (dpush / FCM / MI / OPPO / VIVO) | **Supported but aircraft-independent** | Account/phone-scoped notification channel. Not a flight path. Irrelevant to PC-control. |
| LTE / cellular4g (cmd_set 0x18, eSIM, dongle) | **NOT-WM160** | Mavic Mini 1 has no cellular/dongle/eSIM hardware. `isSupport4GDongle` / `isSupportESim` are false for UAV59. Whole stack targets DJI Cellular Dongle aircraft (Mavic 3E/3T, Air 3, Matrice). |
| Analytics (aopanalytics native engine) | **Active for WM160** | App-global, model-agnostic. When a WM160 is connected, `device_type`/`firmware_sign`/`fcuid`/`battery_ver` etc. are filled from the WM160 and uploaded (subject to consent). |
| Privacy opt-out (privacyconfig) | **Applies globally** | `DATA_ACT_ENABLE_STATE_KEY` + EU gating governs whether analytics upload runs at all. |

**Single most important finding:** the app *does* collect and upload WM160 telemetry through a **native, closed** analytics engine (`libuavanalytics.so` / `libuavanalytics-jni.so`) whose upload URL, appID/appKey and payload crypto are compiled into the `.so` and are **not statically recoverable** — they require a live capture / Frida hook. LTE is entirely inapplicable to WM160.

---

# 1. PUSH — `com.dji.dpush` + `com.dji.fcmpack`

## 1.1 What it does
DPush is DJI's in-house abstraction that fans out to whatever vendor push channel the phone supports, plus DJI's own "penetrate" (silent server→app command) channel. It delivers marketing/service notifications and silent config/command messages to the app; it is **not** a drone-control transport. Registration is keyed to device token + user account, so it is fully independent of which aircraft (if any) is connected.

## 1.2 Vendor channels (from `AndroidManifest.xml`)
- Google FCM — `com.dji.fcmpack.FcmMessagingService` (`android:name` service, intent-filter `com.google.firebase.MESSAGING_EVENT`), extends `com.google.firebase.messaging.FirebaseMessagingService`.
- Xiaomi — `MI_PUSH_APP_ID` / `MI_PUSH_APP_KEY` (`@string/push_mi_app_id` / `push_mi_app_key`).
- OPPO — `OPPO_PUSH_APP_KEY` / `OPPO_PUSH_APP_SECRET`.
- VIVO — `com.vivo.push.app_id` / `com.vivo.push.api_key`.
- UI entry points: `com.uav.push.UAVPushMessageActivity`, `com.uav.push.SchemeUrlActivity`.
- **Firebase auto-collection is disabled by default** (privacy-first):
  - `firebase_data_collection_default_enabled = false`
  - `firebase_messaging_auto_init_enabled = false`
  - `firebase_analytics_collection_enabled = false`

## 1.3 Backend endpoints (evidence: `com.dji.dpush.api.DeviceParams`, `DPushParams`, `ReportApi`)
HTTP via `com.android.volley` (`ReportApi.b()` builds a Volley `RequestQueue`).

| Purpose | Production | Staging (debug builds) |
|---|---|---|
| Device-manager (register/token) | `https://api.djiservice.org/api/device-manager/` | `https://stag-dsapi.dbeta.me/api/device-manager/` |
| DS-Pusher | `https://api.djiservice.org/api/ds-pusher/` | `https://flight-staging.aasky.net/api/ds-pusher/` |
| Report / data-sync | `<DeviceParams.g()> + "data-sync/mobile"` → `.../device-manager/data-sync/mobile` | (staging base + same suffix) |

- `DeviceParams.a()` = staging URL, `DeviceParams.f()` = prod URL; `DeviceParams.g()` returns `BaseParams.c()` (base host set at init).
- `DPushParams.f()` hard-returns the prod ds-pusher URL.
- `ReportApi.a()` literally appends `"data-sync/mobile"`.

## 1.4 Request-type enums (wire = enum ordinal, declaration order)
`com.dji.dpush.core.net.DPushRequestType`:
`REPORT_INIT`(0), `REPORT_PENETRATE`(1), `REPORT_UNREGISTER`(2), `REPORT_HIT`(3), `REPORT_DEVICE`(4), `REPORT_LOGIN`(5), `REPORT_LOGOUT`(6), `REPORT_LANG`(7), `REPORT_TAGS`(8).

`com.dji.dpush.api.DataAction`: `SDK_INIT`, `UPDATE`, `LOGOUT`, `LOGIN`.

(The `PushReport$a..i` inner classes map 1:1 to these operations: init, defaultPenetrateChannel, hit, device link, login, ... , lang, tags, unregister — confirmed by their log strings.)

## 1.5 Payload models (fields = smali `.field`)
**`com.dji.dpush.model.DeviceDataModel`** (device registration body):
`action`, `appName`, `appVersion`, `country`, `deviceToken`, `deviceUuid`, `fixedTags:List`, `language`, `platform`, `platformVersion`, `pushPlatform`, `throughPlatform`, `throughToken`, `token`.

**`com.dji.dpush.model.PushMessage`** (inbound notification):
`alias`, `content`, `extraMsg`, `keyValue:Map`, `notifyId:I`, `obj`, `title`, `topic`, `userAccount`.

**`com.dji.dpush.model.PushCommandMessage`** (silent server command):
`alias`, `command:Object`, `regId`, `topic`, `userAccount`.

`com.dji.fcmpack.FcmClient` unpacks FCM data keys: `msg_uuid`, `app_name`, `custom_data`. `FcmMessagingService.q(RemoteMessage)` handles inbound; `.s(String)` handles token refresh.

## 1.6 WM160 verdict
**Supported but aircraft-independent.** Push works whether or not a WM160 is bound. For the PC-control project this channel is **irrelevant** — it carries no flight commands to the aircraft and can be ignored. (It could theoretically deliver a `PushCommandMessage` to the *phone app*, but that never reaches WM160 over DUML.)

---

# 2. LTE / CELLULAR — `uav.component.lte`, `LteSettingLogic`, DUML cmd_set 0x18

## 2.1 Terminology correction
The domain brief says "DUML 0x24". The actual cellular command set is **cmd_set `0x18` = decimal 24** (`cellular4g`). "0x24" (=36) is a *different* set (`perception`/misc). Evidence: `cmdmap.txt` rows `(1, 24, …)` and `DUML_COMMANDS_FULL.md` `## CMD_SET 0x18 (24) — cellular4g`.

## 2.2 DUML cmd_set 0x18 (cellular4g) — commands
From `cmdmap.txt` (`sender=1 PC/APP`, `cmd_set=24`) and `DUML_COMMANDS_FULL.md` builder table:

| cmd_id | dec | app builder class | recv | request payload |
|---|---|---|---|---|
| 0x01 | 1 | `DataModule4GVideoCamera` | DM368 | len=2B `+0`1B b; `+1`1B c |
| 0x15 | 21 | `DataModule4GSetApn` | OSD | `+0`1B const0x0; `+1`?B apn; `+?`1B const0x0 — pairs with `uav_cellular4g_lte_apn_request_info`/`_response_info` |
| 0x18 | 24 | `DataModule4GGetRTT` | OSD | len=2B `+0`1B const0x0; `+1`1B mFixedNum; `+2`1B mAddress |
| 0x19 | 25 | `DataModule4GGetCardInfo` | OSD | len=1B `+0`1B mRequestCmd |
| 0x21 | 33 | `DataModule4GDebugAT` | OSD | AT-command passthrough |
| 0x43 | 67 | *(name-only)* `uav_cellular4g_get_lte_dongle_fw_release_note_req` | — | dongle FW release note |
| 0x45 | 69 | *(name-only)* `uav_cellular4g_get_dongle_subscribe_info_req` | — | dongle subscription info |
| 0x4B | 75 | *(name-only)* `uav_cellular4g_lte_esim_set_info` | — | eSIM provisioning; rsp `uav_cellular4g_lte_esim_response_info` |

Related (other sets): cmd_set `0x06`/`0x4A`(74) `uav_rc_mutil_device_enable_4g_req` (RC-side 4G enable); cmd_set `0x09`/`0x4B`(75) `device_ofdm_sdr_dongle_state_req` (SDR/dongle link state).

"Name-only" = no app packing/builder class exists in this APK, so the byte layout is not asserted — see `DUML_COMMANDS_FULL.md`.

## 2.3 Cloud LTE service — `uav.component.lte.ILteService`
A large RxJava (`Observable`/`Single`) interface with **fully obfuscated method names** (`A0()Z`, `Q0(String,CardType)Single`, `a0(String,DongleTelecomOperatorType)Single`, …). It is an **HTTP cloud service** for eSIM/dongle *bind → subscribe → query* flows, not a DUML surface. Data classes (nested in `ILteService`): `BindInfo`, `BindStateQueryModel`, `RnBindInfo`, `SupportInfo`, `Data`, `DateData`, `SeverTime`, `SimpleResponseModel`, `LteUnavailableReason`.

`uav.component.lte.CloudControlLteSupportModel` (`data:String`, `result:Result`) with nested `LteSupportData.UAVFlyLteFeatureSupportInfo(data:String)` is a **cloud-fetched per-model LTE feature gate** — the server tells the app whether the connected model may use cellular. The fetch call is behind obfuscated Retrofit/RxJava and not statically pinned to a URL here.

## 2.4 Enums (wire values from smali)
`com.uav.flymodel.generated.api.common.CardType`: `NONE`=0, `SIM`=1, `ESIM`=2, `UNKNOWN`=3.

`com.uav.flymodel.generated.api.airlink.DongleTelecomOperatorType`: `NONE`=0, `CHINA_MOBILE`=1, `CHINA_UNICOM`=2, `CHINA_TELECOM`=3, `CHINA_BROADNET`=4, `UNKNOWN`=5. (China-carrier-only — reinforces this is a China-market dongle feature.)

`ILteService$LteUnavailableReason`: integer reason codes `-1` (b) and `0x00`..`0x21` (c..J) — 34 distinct reasons. **Names are obfuscated (fields `a`–`J`); only the numeric codes are recoverable statically.**

## 2.5 `com.uav.component.bglogic.lte.LteSettingLogic`
Background orchestrator (tag `"LteSettingLogic"`). Observed behaviour from strings:
- Link-mode switching LTE ⇄ Wi-Fi/SDR: `"lte service set link mode success"`, `recoveryLinkModeToLte`, `subscribeAppWorkStage`, `notifyAppWorkStateInWifi`.
- Config refresh: `refreshLteCfg v4 … , v6 …`, placeholders `"fake_ipv4_lte_string"` / `"fake_ipv6_lte_string"` (from `R.string`).
- Safety: `"Set sdr lost over time block take off success"`, `"Set sdr lost show 3min dialog success"` — LTE-link-loss take-off gating.
- Dongle bind/subscription: `refreshConnectedDroneLteBindState`, `refreshDongleExpiredDate`, `refreshLteSwitchParamsInCloudControl`, `reportLteBindPhoneNumberEncryption`.
- Uses Android `ConnectivityManager` (`"connectivity"`) + a network-state listener.

## 2.6 Capability gate keys (`isSupport_keys.txt`)
`isSupport4GDongle`, `isSupportESim` (also unrelated `isSupportDownloadFileLargerThan4G` = a file-size flag, not cellular).

## 2.7 WM160 verdict — **NOT-WM160**
Mavic Mini 1 (UAV59 / ProductType 0x3b) has **no cellular modem, no dongle port, no eSIM**. The entire cmd_set-0x18 stack, `ILteService`, `LteSettingLogic` and `CloudControlLteSupportModel` target DJI Cellular Dongle-equipped aircraft. For UAV59, `isSupport4GDongle` and `isSupportESim` resolve false and the cloud `CloudControlLteSupportModel` returns unsupported. **You never send cmd_set 0x18 to a WM160.**

**Undecidable statically / live check:** the `isSupport*` resolver and the `CloudControlLteSupportModel` HTTP fetch sit behind the packer + obfuscated Retrofit. To confirm on-device, Frida-hook the boolean support methods on the `ILteService` implementation (e.g. the obfuscated `A0()Z`/`D0()Z`/`I0()Z` returns) or the `isSupport4GDongle`/`isSupportESim` capability resolver, and observe `false` when WM160 is connected.

---

# 3. ANALYTICS — `uav.common.aopanalytics` + privacy opt-out

## 3.1 Architecture
AOP-instrumented telemetry with a **native C++ engine**:
- Java facade: `uav.common.aopanalytics.AOPAnalyticsManager` (SQLite cache `"/analytics.db"` in app files dir; log tag `"AOPAnalytics"`; `addData`, `fill_field`, `[Report][%s]:%s`).
- JNI bridge: `uav.common.aopanalytics.JNIAOPAnalytics` — `System.loadLibrary("uavanalytics-jni")` (error string `"couldn't load uavanalytics-jni.so"`).
- Native libs present: `decompiled/lib/arm64-v8a/libuavanalytics-jni.so` + `libuavanalytics.so`.
- Upload driver: `uav.common.aopanalytics.JNIAOPAnalyticsUploadHelper` (singleton; interval constants `0x5`/`0x32`=5/50 s passed to `UAVAnalyticsStartUploadWithTimeInterval(int,int)`).
- App glue: `com.uav.analytics.implementation.AppAnalytics` + `.logic.ReportUtil`; upload filter `com.uav.analytics.implementation.AnalyticsUploadFilterHandlerImpl` (root JSON key `"events"`).
- AOP mechanics: annotations `AOPPointAnnotation` / `AOPPointAnnotationsBefore` / `AOPPointAnnotationsAfter`; `AnalyticsInterceptor`; `PlaceholdersUtil` (`eventPlaceholders`, `keyPlaceholders`, `matchReg`, `replaceWithPlaceholders`); `KeyManager` / `MethodsManager` / `TypeManager`.

## 3.2 Native API surface (`JNIAOPAnalytics` native methods)
`UAVAnalyticsInitialize(String,String,String,String,String,String,String)`, `UAVAnalyticsAdd(String event, String json, int)`, `UAVAnalyticsSetDeviceInfo(5×String)`, `UAVAnalyticsSetCountryInfo(String,String)`, `UAVAnalyticsSetUUID`, `UAVAnalyticsSetUserToken`, `UAVAnalyticsSetAppVersion`, `UAVAnalyticsSetAppLanguage`, `UAVAnalyticsSetAppChannel`, `UAVAnalyticsSetFlavor`, `UAVAnalyticsSetDebugEnabled(Z)`, `UAVAnalyticsStartUploadWithTimeInterval(II)`, `UAVAnalyticsStopUpload`, `UAVAnalyticsGetCacheDataCount`, `UAVAnalyticsGet{Init,Upload}Status`, `UAVAnalyticsRegister/Unregister`, `UAVAnalyticsUninitialize`, `UAVgetString(String)`.

`uav.common.aopanalytics.AnalyticsUploadConfig(appID:String, appKey:String)` carries the tenant credentials; the **literal appID/appKey are injected via Koin** (`KoinDefUavCommonAopanalyticsAnalyticsUploadConfig`, `KoinDefComDjiComponentApplicationConfigGetAnalyticsUploadConfig`) and are **not present as literals in this DEX**.

## 3.3 Telemetry fields collected (evidence: `AppAnalytics` + `ReportUtil` const-strings)
Per-event/context fields written into the JSON payload:
`app_ver_build_num`, `firmware_sign`, `fcuid`, `device_type`, `device_version`, `device_subtype`, `device_motor_status`, `battery_ver`, `rc_device_type`, `rc_ver`, `glasses_device_type`, `glasses_ver`, `glass_sn`, `flight_uid`, `new_datetime`, `localtime`, `oaid` (Android OAID advertising id), `area_code`, `area_code_source`, `internet_connection_status`, `internet_connection_type`.

Plus, via native setters: device info (5 fields), country (2), UUID, user token, app version, language, channel, flavor.

Event keys are `&&`-namespaced, e.g. `gimbalzero&&action_type`, `gimbalzero&&calibration_result`, `lowpowermode&&exit_low_power_consumption_result_success_source`, `osd&&tab`, plus report methods `reportHyperlapse`, `reportTakeOffLand`, `cameraOsdMenuMaterialTabClick`.

**When a WM160 is connected, `device_type`/`device_subtype`/`device_version`/`firmware_sign`/`fcuid`/`battery_ver` are populated from the WM160** → the app uploads WM160-identifying telemetry.

## 3.4 Privacy / opt-out — `com.uav.privacyconfig`
- **`PrivacyConfigManager`** — master data-collection switch:
  - Key `DATA_ACT_ENABLE_STATE_KEY` stored via `com.uav.component.persistence.PersistenceStorage` (MMKV; `PersistenceStorage.d(key)` read, `.g(key,bool)` write).
  - Region-gated: reads area code + Europe list (`"listen area code "`, `"listen europe list "`, `" isEurope "`, `" dataActEnabled "`); tag `"PrivacyConfigManager"`, service `"PrivacyService"`, config version key `key_privacy_config_version`. In EU, collection defaults to require explicit consent.
- **`NativePermissionHandler`** — `initPermissionHandler` gates the JNI/native collection by consent (`"privacyService"`).
- **`PrivacyConfig`** — terms bundle: `termOfUseFileName`, `termPrivacyFileName`, `termsNoticeFileName`, `termsProductFileName`, `termsUtmissFileName`, `termsUtmissSupport`.
- **`com.uav.privacyconfig.lbs.UAVLocationConfigurations`** — location-privacy config.
- Reinforced by manifest Firebase `*_collection_*_enabled = false` defaults (§1.2).

## 3.5 WM160 verdict — **Active for WM160**
Analytics is app-global and model-agnostic; it runs regardless of aircraft and, when WM160 is connected, uploads WM160 device/firmware/battery identifiers and flight-event telemetry — **gated only by** `DATA_ACT_ENABLE_STATE_KEY` + region consent. To suppress it on a PC-control rig: leave data-act consent off, or don't call `PrivacyConfigManager`/`NativePermissionHandler` init (if you reuse app modules).

**Undecidable statically / live check:** the upload **URL, request signing, appID/appKey and payload encryption live inside `libuavanalytics.so`** — not in Java. To recover them: Frida-hook `uav.common.aopanalytics.JNIAOPAnalytics.UAVAnalyticsAdd(String,String,int)` and `UAVAnalyticsInitialize(...)` for cleartext event+config, and/or hook native `libuavanalytics.so` exports / do a TLS network capture during upload. The Koin-injected `AnalyticsUploadConfig(appID, appKey)` can be dumped by hooking its `<init>`.

---

## Appendix — key classes → DEX
- Push: `com.dji.dpush.*`, `com.dji.fcmpack.*` → `classes_04e4400c.dex`.
- LTE service/enums: `uav.component.lte.*`, `uav.common.aopanalytics.*` → `classes_03a5700c.dex`; `CardType`/`DongleTelecomOperatorType` → `classes_0855200c.dex`.
- LteSettingLogic + `com.uav.analytics.implementation.*` → `classes_07a5000c.dex`.
- `com.uav.privacyconfig.*` → `classes_00b9d00c.dex`.
- Native analytics: `decompiled/lib/arm64-v8a/libuavanalytics.so`, `libuavanalytics-jni.so`.
