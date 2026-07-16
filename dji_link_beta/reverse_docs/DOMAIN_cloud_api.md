# DOMAIN: cloud_api — DJI cloud & servers (WM160 / Mavic Mini 1)

Scope: every HTTP(S) endpoint the app talks to (retrofit2/okhttp3 interfaces), the CloudControl
remote‑config service, the LiveShare / live‑streaming path (RTMP/RTSP, native JNI), what data is sent,
which auth headers ride along, and which of these are **required vs optional for flying a WM160
(Mavic Mini 1 = UAV59 / ProductType 0x3b)**.

Evidence base: 16 DEX baksmali'd in full (128,105 classes), string pool of all DEX, and the DUML
command tables (`cmdmap.txt`, `cmds.json`, `full_table.txt`). Every class path below is a real
`.smali` file. Where a value only resolves at runtime behind the packer, it is called out with the
exact Frida hook / class to break on.

> **Important framing — this APK is a rebranded DJI Fly.** Package root is `com.uav.*` (not
> `com.dji.*`), and DJI’s own hosts are partly rewritten: e.g. the account host appears both as
> `account-api.dji.com` **and** `account-api.uav.com`, and the CloudControl service lives at
> `api.uavservice.org` (a rename of `*.djiservice.org`). HTTP headers are `X-UAV-locale` /
> `X-UAV-SDK-Version` (renamed from `X-DJI-*`). The DUML/flight stack is unchanged DJI. Treat any
> `uav.com` / `uavservice.org` host as the rebrand of the corresponding `dji.com` / `djiservice.org`
> host. Both string sets are physically present in the binary.

---

## 0. TL;DR for the PC‑control project

**None of the cloud APIs are needed to fly a WM160 that has already been activated once.** The only
hard cloud dependency is the **one‑time firmware activation** (see `FLIGHT_GATING.md §A`), which is a
DUML handshake gated by a logged‑in DJI account + internet, done once per airframe and then persisted
on the FC. Everything else in this document (SkyPixel sharing, LiveShare re‑streaming, DJI Care,
questionnaires, media‑editor SDK, remote config) is **optional / app‑convenience** and can be dropped
entirely when the PC replaces the app.

Two things you *may* want to keep:
1. **GEO/FlySafe NFZ‑unlock** (`flysafe-api.dji.com`) — only if you need to unlock an authorization
   zone; the unlock is a DJI‑signed licence tied to the drone SN and cannot be forged offline.
2. **LiveShare** (native RTMP/RTSP push) — if you want the PC to re‑broadcast the drone video. This
   runs entirely PC/app‑side on the *decoded* liveview; the WM160 itself does **not** stream to the
   internet.

---

## 1. HTTP transport & client stack

### 1.1 OkHttp base client — `com/uav/http/OkHttpUtil`
Central `OkHttpClient` factory. Methods: `d()` returns the shared client, `c()` builds an
`okhttp3.logging.HttpLoggingInterceptor` (debug logging), `b(Z)` toggles logging, `g()` reads the
logging flag. All retrofit services below are built on top of this client (e.g. CloudControl calls
`OkHttpUtil->d()` in `initDefaultOkHttpClient`).

### 1.2 Header interceptors
- `com/uav/accountui/accountdependency/HeaderInterceptor` — adds **`X-UAV-locale`** and
  **`X-UAV-SDK-Version`** to every account‑center request (these are the renamed `X-DJI-*` headers).
- `com/uav/accountui/accountdependency/AccountCenterClient` — implements `okhttp3.Interceptor`;
  attaches **`appId`** and the session **`token`** to account calls.
- `com/uav/playback/ui/share/selfbuilt/interceptor/UploadInterceptor` — SkyPixel media upload signing.
- `com/uav/waypoint/network/a` — waypoint‑sync auth interceptor.
- `com/uav/service/cloudcontrol/CloudControlRequestService` — builds its own signed headers (see §3).

There is no single global `Authorization: Bearer` scheme; each service family signs differently
(account uses `appId`+`token`; CloudControl uses `X-Wk-*` HMAC; SkyPixel uses upload STS tokens;
FlySafe unlock uses DJI‑cert‑signed licences).

---

## 2. Full HTTP endpoint catalog

Format: **service class → HTTP verb + path**. Base host is resolved from the host map in §5 unless a
literal is shown. Retrofit interfaces with `@Url` build the full URL at the call site (host + path
concatenated in code), so the path constants are listed from the owning `*HttpApi` class.

### 2.A — FLIGHT‑RELEVANT (may matter for WM160)

**GEO / FlySafe / NFZ — `com/uav/api/protocol/IFlyForbidHttpApi`**  (host `flysafe-api.dji.com`, key `flightForbidHttpApi` / `flightForbidUnlockHttpApi`)
```
api/v3/geofence/sdk_static_data              # static NFZ polygon DB for the SDK
api/v3/geofence/app_static_data              # static NFZ DB for the app map
api/v3/geofence/onboard_static_data          # NFZ DB pushed to aircraft
api/geo/v3/geofence/query_update_for_onboard_static_data_v2
api/v3/geofence/tfrs_around                  # temporary flight restrictions near a point
api/v3/circle/static_data                    # circular zones
api/v3/circle/tfrs
api/v3/geo/query_page                         # paged zone query for a lat/lon
api/v1/geo_fence/get_geo_file                 # downloadable geo DB blob
api/v1/geo_fence/list_unlimited_areas
api/v3/geofence_unlock/list_unlimited_areas   # zones the account may self-unlock
api/v3/geofence_unlock/mobile_unlock_areas    # perform a self-unlock
api/v3/geofence_unlock/whitelist_license
api/v3/geofence_unlock/disable_unlock_license
api/v4/mobile/unlock_license_groups           # licence-based (custom/enterprise) unlock
api/v4/mobile/unlock_license_groups/areas
api/unlock/v1/sms                             # SMS-verified unlock
api/unlimit_license  /  api/unlimit_license_list
api/v4/mobile/user                            # account's unlock entitlements
```
- **Required for WM160?** *Only if you enter an Authorization Zone.* Motor‑start on an already‑activated
  Mini does not call these. The unlock result is a DJI‑signed licence bound to the drone SN
  (see `FLIGHT_GATING.md §C`, `libFRCorkscrew`); it cannot be fabricated offline. Login required to
  fetch/apply an unlock.
- Data sent: drone SN, account/user id, target lat/lon, zone id, device locale.

**Activation & FlySafe terms — `com/dji/component/application/config/FlyHttpApi`** (test twin: `FlyHttpTestApi`)
```
api/v3/eagle/activation                       # cloud device activation record  (host active.dji.com)
api/v3/flysafe_terms/geo                       # GEO ToS acceptance
api/v3/flysafe_feedback/nfz_error_report       # user "wrong NFZ" report
```
- **Activation is the one hard cloud gate** for a factory‑fresh WM160. The HTTP side records the
  activation; the FC handshake that actually un‑gates motors is DUML, not HTTP — see §6 and
  `FLIGHT_GATING.md §A` (`0x00/0x32` activate req + `0x03/0x62 DataFlycSetActiveResult`).

**Flight‑log / telemetry upload — `com/uav/api/protocol/IFlightHttpApi` (`com/uav/api/FlightHttpApi`)** (key `flightHttpApi` / `flightRecorderHttpDomain`)
```
api/v2/flight_log/zipupload                    # zipped flight records upload
api/gpsLocation                                # coarse GPS report
```
- Purely telemetry back‑haul. **Optional**; drop for PC control.

**Flight‑record sync — `uav/fr/sync/IFlightRecordApiService`**
```
GET  flight/zipdownload
GET/POST flight/record_info_list
GET  flight/user_info
```
Cloud backup/restore of flight records. **Optional.**

**Digital‑elevation / terrain — literal in `FlyHttpApi`:**
`https://digital-elevation.djigate.com/dem/api/v2/map/resources/search` — DEM tiles for map terrain
shading. **Optional** (map cosmetic).

**Waypoint mission cloud sync — `com/uav/waypoint/network/api/WaypointMissionSyncApi`**
```
POST /api/waypoint/mission/check
POST /api/waypoint/mission/compare
PUT  /api/waypoint/mission/{mission_uuid}
POST /api/waypoint/file/upload/sts/init
```
- **NOT‑WM160 relevant for autonomy:** Mini 1 has no onboard waypoint engine; this is cloud
  storage of mission files. Even if kept, it does not fly the drone.

### 2.B — ACCOUNT / IDENTITY (optional; only needed to log in)

**`com/uav/accountui/accountdependency/ApiService`** (host `account-api.dji.com` / `account-api.uav.com`, key `accountCenterHttpApi`)
```
POST apis/apprest/v1/user_login
POST apis/apprest/v1/login_or_register_with_sms_code
POST apis/apprest/v1/email_register_with_login
POST apis/apprest/v1/phone_register_with_login
POST apis/apprest/v1/phone_reset
POST apis/apprest/v1/send_email  /  send_reset_email  /  send_code (v2)
POST apis/apprest/v1/vcode  /  v1/check_code  /  v1/validate_captcha
POST apis/apprest/v1/check_account_exist
POST apis/apprest/v1/init_data
POST apis/apprest/v1/tokenv2/generate/authurl
POST /apis/apprest/v2/one-click-login
POST apis/apprest/v2/phone/binding
GET/POST apis/apprest/v2/email/validation (+ /send)
```
**`com/lct/accountlib/api/acinterface/RequestInterface`** (all `@Url`, same account host) — the lower
level login/register/reset/sms/logout calls (`accountCenterLoginByPhoneRequest`,
`accountCenterLoginByEmailRequest`, `accountCenterCheckTokenRequest`, `accountCenterLogoutRequest`,
`accountCenterSendSmsRequest`, `accountCenterPhoneRegisterRequest`, …). `@Field password` /
`confirmPassword` are form fields. Returns a session **token** carried thereafter via the
`AccountCenterClient` interceptor (`appId` + `token`).
**`com/dpad/service/api/service/UserService`** — a parallel `apis/apprest/v1/*` login surface
(`user_login`, `email_login`, `phone_login`, `token`, `validate_token`, `logout`, …).
**`com/lct/accountlib/api/ReportSensorApi`** → `POST /api/app/reports/sensors`, `POST /api/user-politics`
(consent/telemetry).

Account host cancel/misc literals (from `FlyHttpApi`): `https://account.dji.com/account/userCancel`,
`member-api.dji.com`, `account.dbeta.me` / `r-account-api.dbeta.me` (staging).

### 2.C — SKYPIXEL / MEDIA SHARING (optional)

**`com/uav/playback/ui/share/selfbuilt/api/MediaEditShareApi`** and **`com/lct/network/api/*`**
(hosts `www.skypixel.com`, `light-*.skypixel.com`, `picasso*.skypixel.com`)
```
POST api/v2/uploads/videos            PUT api/v2/uploads/videos/{token}
POST api/v2/uploads/photos            PUT api/v2/uploads/photos/{token}
POST api/v2/uploads/photo-360s        PUT api/v2/uploads/photo-360s/{token}
POST api/v2/uploads/videos/{token}/cover
GET  api/v2/videos/{slug}  /  api/v2/photos/{slug}
POST api/v2/videos/{slug}/views  /  visibility
PUT/DELETE api/v2/likes|dislikes/videos/{slug}
POST api/v2/reports|shares/videos/{slug}
GET  api/v2/mobile/feeds/videos
GET  api/v2/upgrades/check            # SkyPixel app-content upgrade check (NOT firmware)
GET  api/user  /  api/v2/users/{slug}  /  api/v2/user/stats  /  api/v2/user/server_location
```
Upload uses an STS token flow (`FileUploadGetTokenApi` / `FileUploadConfirmApi` → `@Url`, signed by
`UploadInterceptor`). **All optional.**

### 2.D — MEDIA‑EDITOR SDK (`lct.lomo.*`, `com.lct.*`) — **NOT‑WM160 / not flight**
A large embedded CapCut‑style video‑editor SDK (templates, stickers, music, AI effects, beauty).
Hosts include `cdp.djiservice.org`, `api.djiservice.org`, ByteDance/Tencent CDNs. ~40 retrofit
interfaces: `TemplateApiService`, `IResourceApi`, `MusicApi`, `AiApiService`
(`edit/terra-rescon-be/v2/jobs`), `StickerApi`, `BeautyControlApi`, etc. **None touch the aircraft.**
Full list captured but omitted for brevity — all `NOT-WM160`.

### 2.E — DJI CARE / REPAIR / SUPPORT (optional)
From `FlyHttpApi` (hosts `repair.dji.com`, `service.dji.com`, `support.dji.com`, care H5):
```
api/v1/djicare/gen_task  /  result  /  query  /  v2/query
/api/v1/djicare/selfcare/encrypt  /  sn_product_center/query
/api/device-manager/care/{bind-account|unbind-account|bind-sn-list|device-detail}
https://repair.dji.com/api-support/v1/case/syncUploadResult
https://support.dji.com/care/active
```
Warranty binding by SN. **Optional.**

### 2.F — QUESTIONNAIRE / STATISTICS / MISC (optional)
`api.djiservice.org/api/questionnaire/quiz_url`, `.../webcontent/login_record`;
`statistical-report.djiservice.org/api/report/{clientContext,clientUUID}`;
`mydjiflight.dji.com/api/v2/geocoder_service/geoip` (geo‑IP → area code);
`apigateway.djiservice.org/ds-path-ns`; calibration URL
`api.djiservice.org/calibration/v1/calibration/geturl?sn=%s`. **All optional/analytics.**

---

## 3. CloudControl — remote‑config / feature‑flag service

This is what the task calls “CloudControl.” It is **not** drone control — it is DJI’s server‑driven
**operation‑config / feature‑flag** system (namespaces of key/values fetched at startup). Classes in
`com/uav/service/cloudcontrol/`.

**Retrofit interface — `ICloudControlApi`:**
```
POST postDataFromCC(@HeaderMap Map<String,String>, @Body RequestBody)   # Content-Type: application/json
POST postDataFromCCV2(@HeaderMap Map<String,String>, @Body RequestBody)
```
The concrete endpoints (from the sibling API interfaces):
```
com/uav/service/cloudcontrol/UAVCloudControlApi:
    POST api/cloudcontrol/config
    POST api/cloudcontrol/config/condition_data
com/uav/service/cloudcontrol/USCloudControlApi:
    POST api/operation/config          # US-region variant
```
**Base domain:** `https://api.uavservice.org/` — literal in
`uav/component/CloudControl/config/CloudControlNameSpacesConfig` (release domain via `E0()`; debug
domain via `N0()`; overridable at runtime through `CloudControlHttpApi.c(debugDomain)`).

**Auth / signing — `CloudControlRequestService.createNewHeaderMap()`** builds an HMAC‑signed header set:
```
X-Wk-SecretId
X-Wk-Timestamp
X-Wk-Nonce
X-Wk-Signature-Method
X-Wk-Sign
User-Agent:  "%s/%s/operation-config (%s-%s; Android %s; Scale/%.2f)"
```
Region gating: `CloudControlRequestService` keeps a `cloudControlUSNameSpaceWhiteList` and refuses
non‑whitelisted namespaces in the US (`"Namespace [%s] is not allowed to request in US"`), region
decided by `isUS()` from `getAreaCode()` (`AreaCodeService`, default `"FF"`). Env `DEV`/`PROD` via
`is_dev_open`. Data getters: `CloudControlServiceDataGetter` / `…V2`.

**Required for WM160?** **No.** It only tunes app feature flags. The `X-Wk-*` signature is computed by
Java, not the packer, so if you ever need to replicate a request the values are visible at
`CloudControlRequestService->createNewHeaderMap`. Nothing here reaches the aircraft.

---

## 4. LiveShare / live‑streaming

Two independent stacks. Neither makes the **aircraft** stream to the internet — both operate on the
already‑decoded liveview inside the phone/app (and would run on the PC in a PC‑control setup).

### 4.1 Native LiveShare (RTMP/RTSP push) — `uav/liveshare/generate/*` + `uav/liveshare/UAVLiveShareManager`
JNI module (`LiveShare$CppProxy`, native lib). The Java layer feeds decoded frames to native, which
encodes and pushes to a user‑supplied URL. Native methods:
```
Initialize(String logDir) : Z            UnInitialize()
StartLiveShare(LiveshareInfo, LiveshareResultCallback)
StopLiveShare(LiveshareId, LiveshareErrorCallback)
SendVideoFrameData(LiveshareId, byte[], int, long, boolean)
SendAudioFrameData(LiveshareId, byte[], int, long)
FetchAdaptiveVideoParam(LiveshareId, VideoParamInfoCallback, LiveshareErrorCallback)
RegisterStatusEventObserver / RegisterStreamingInfoObserver (+ UnRegister)
DumpVideoData(String path, boolean)
```
**`LiveshareInfo`** (what you configure): `mUrl:String`, `mUrlType:LiveshareUrlType`,
`mCameraIndex:byte`, `mRetryConnectDuration:int`, `mOtherInfo:RtmpConfigInfo`.
**`RtmpConfigInfo`**: `mResolutionWidth:int`, `mResolutionHeight:int`,
`mVideoCodecType:LiveshareCodecType`.

**Enums (wire values):**
| Enum | Value | ordinal |
|---|---|---|
| `LiveshareUrlType.Rtmp` | 0x0 | 0 |
| `LiveshareUrlType.Rtsp` | 0x1 | 1 |
| `LiveshareCodecType.H264` | 0x0 | 0 |
| `LiveshareCodecType.H265` | 0x1 | 1 |
(Also present: `LiveshareStatus`, `LiveshareErrorType`, `LiveshareVideoParamInfo{mBps,…}`,
`LiveshareId{mUuid,…}`, callbacks `StatusEventCallback` / `StreamingInfoCallback` /
`VideoParamInfoCallback`.)

**The stream URL is arbitrary user input** (RTMP or RTSP) — YouTube/Facebook/custom RTMP or an RTSP
sink. No DJI server involved for the generic path. **Optional; WM160‑compatible** because it only
needs the decoded liveview (which the Mini delivers over the AOA channel, per `MASTER_REPORT §1`).

### 4.2 Social live‑stream integrations — `com/uav/livestream/*` (China platforms)
`LiveStreamSession`, `DouyinLiveStreamConfig`, `IWeChatLiveStreamProvider` /
`DefaultWeChatLiveStreamProvider`, `LiveStreamVideoEncoder` (MediaCodec H264/H265),
`LiveStreamAudioEncoder`. Streams to **Douyin** (`amemv`/`snssdk` hosts) and **WeChat Channels**
(`channels.weixin.qq.com/web/pages/extLive`, `long.open.weixin.qq.com/connect/...`). These are
region/social conveniences. **Optional**, and largely China‑only. Not needed for PC control.

> No **Agora** SDK was found in the string pool (`grep -i agora` → none). DJI Fly’s liveshare here is
> RTMP/RTSP + native, not Agora‑based.

---

## 5. Host map (from `com/dji/component/application/config/FlyHttpApi`)

The app resolves logical keys to hosts here. Selected WM160‑relevant entries (release):

| Logical key | Host (release) | Used for |
|---|---|---|
| `activeHttpApi` | `https://active.dji.com` | device activation (`api/v3/eagle/activation`) |
| `flightForbidHttpApi` / `flightForbidUnlockHttpApi` / `flightSafeHttpApi` | `https://flysafe-api.dji.com` | GEO/NFZ static data + unlock (§2.A) |
| `flightHttpApi` / `flightRecorderHttpDomain` | flight‑log host | `flight_log/zipupload` |
| `accountCenterHttpApi` | `https://account-api.dji.com` (rebrand `account-api.uav.com`) | login/register |
| `careHttpApi` / care family | `https://repair.dji.com`, `service.dji.com`, `support.dji.com` | DJI Care |
| CloudControl (separate config class) | `https://api.uavservice.org/` | remote config (§3) |
| geo‑IP | `https://mydjiflight.dji.com/api/v2/geocoder_service/geoip` | region/area code |
| DEM | `https://digital-elevation.djigate.com` | terrain tiles |
| media/social | `www.skypixel.com`, `light-*.skypixel.com`, ByteDance/Tencent | sharing, editor |
| app content H5 | `https://app-h5.dji.com/8A3A4DCF5FF34C1A/...` | in‑app web pages |
| statistics | `statistical-report.djiservice.org`, `apigateway.djiservice.org` | analytics |

Staging twins live on `*.dbeta.me` / `test-*` / `stag-*` and `report-t.djicorp.com`,
`security-fac.djicorp.com`, `prototype-be.djicorp.com` (selected only in debug builds via
`FlyHttpTestApi`). Region/area code comes from `getAreaCode()` (default `"FF"`) and `geoIPUrl`.

---

## 6. DUML that touches “cloud”/streaming (for completeness)

Cloud HTTP is orthogonal to DUML, but three DUML commands are adjacent:

| Name | cmd_set / cmd_id | Notes / WM160 status |
|---|---|---|
| `uav_general_activate_device_req` | **0x00 / 0x32** | Cloud activation handshake req (needs online account). See `FLIGHT_GATING.md §A`. |
| `DataFlycSetActiveResult` | **0x03 / 0x62** | App → FC: report activation outcome. Payload `[0..3] UAVActivationState u32 \| [4..7] u32 \| [8..11] u32 \| [12..43] 32‑byte string`. `UAVActivationState{Success=0,NoNetwork=1,InvalidId=2,FailedForNet=3,OTHER=100}`. **This is the only cloud‑linked DUML you must satisfy for a fresh WM160.** |
| `uav_dm368_set_sh_start_live_streaming_req` / `…get_sh_get_live_streaming_setting_info_req` | **0x08 / 0x78 (120)**, **0x08 / 0x79 (121)** | Onboard (aircraft‑side) live‑streaming control on the DM368 video SoC. Present in the full DJI table (`cmdmap.txt` L427‑428). **Almost certainly NOT‑WM160**: the Mini 1 has no onboard LTE/RTMP; DJI Fly re‑streams phone‑side via §4.1. Undecidable purely statically — confirm by hooking the FC response: if the Mini answers `0x08/0x79` with a valid setting it supports it, otherwise it NACKs. |
| `uav_camera_set_liveview_source_camera_req` | **0x02 / 0x09** | selects liveview source camera; not internet streaming. |

---

## 7. What is undecidable statically → live capture / Frida

1. **CloudControl `X-Wk-Sign` HMAC input** — algorithm is in Java
   (`CloudControlRequestService->createNewHeaderMap`), but the `X-Wk-SecretId`/secret value is loaded
   from config (possibly StringFog‑obfuscated, `com/uav/stringfog/lib`). Hook
   `CloudControlRequestService->createNewHeaderMap` to dump the final header map.
2. **Account signing (`appId`/token)** — `appId` value and token lifecycle: hook
   `com/uav/accountui/accountdependency/AccountCenterClient->intercept` and
   `HeaderInterceptor->intercept` to capture live headers.
3. **Whether the WM160 honors `0x08/0x78` onboard live‑streaming** — capture the FC reply on the DUML
   channel (COM5 / AOA), or hook the DUML router (`duss_parse_composite_data` per `MASTER_REPORT §2.1`).
4. **Exact activation request body to `active.dji.com`** — behind account auth + packer; hook the
   okhttp call at `active.dji.com/api/v3/eagle/activation` (or the `activeHttpApi` builder in
   `FlyHttpApi`) to see the JSON (SN, activation token, region).
5. **NFZ‑unlock licence bytes** — the signed licence returned by
   `flysafe-api.dji.com/api/v3/geofence_unlock/*` is verified on‑device by `libFRCorkscrew`
   (`FLIGHT_GATING.md §C`); the wire format needs a live unlock capture to document.

---

## 8. Bottom line for WM160

| Cloud capability | Host | Required to fly WM160? |
|---|---|---|
| One‑time activation | `active.dji.com` + DUML `0x00/0x32`,`0x03/0x62` | **YES, once per airframe** (fresh unit only) |
| GEO/NFZ static data | `flysafe-api.dji.com` | No (only affects where it *will* fly) |
| NFZ self/licence unlock | `flysafe-api.dji.com` | Only to enter an Authorization Zone; DJI‑signed, unforgeable |
| Account login | `account-api.dji.com` / `.uav.com` | Only to perform activation/unlock |
| CloudControl remote config | `api.uavservice.org` | No — app feature flags only |
| LiveShare RTMP/RTSP | user‑supplied URL (native, §4.1) | No — optional re‑broadcast of decoded video |
| Social live‑stream (Douyin/WeChat) | Tencent/ByteDance | No — China social, optional |
| SkyPixel media / DJI Care / questionnaire / editor SDK / flight‑log upload / DEM | various dji.com/djiservice.org | No — all optional convenience |

For the PC‑control goal: **activate the drone once with the stock app/account, then you can drop every
cloud dependency.** Keep FlySafe‑unlock only if you must fly in a restricted zone, and keep LiveShare
only if you want the PC to re‑broadcast video.
