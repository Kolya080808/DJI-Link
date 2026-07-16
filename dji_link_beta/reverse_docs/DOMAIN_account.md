# DOMAIN: account — DJI Fly login / account / session, and whether WM160 flight needs it

Evidence-based reverse of DJI Fly `dji.go.v5` v1.21.4. Sources: `reverse_docs/unpacked_app_dex/*.dex`
(16 DEX, disassembled with `baksmali`), `reverse_docs/all_classes.txt`, `reverse_docs/full_table.txt`
(DUML command table), `reverse_docs/FLIGHT_GATING.md §A` (activation gate), `MASTER_REPORT.md §2.2`
(DUML frame/addressing). Every claim cites a class / smali / table entry. Filtered to **Mavic Mini 1 =
WM160 = UAV59 / ProductType 0x3b**.

---

## 0. TL;DR (the answer to the load-bearing question)

**A DJI account login is NOT required to fly an already-activated WM160.** The account subsystem is
**100 % app-side + DJI-cloud HTTP**. It talks to `https://account-api.dji.com` over REST; it never
sends a "login" to the aircraft. There is **no per-flight login DUML command — none exists**. The
only place an account ever couples to the drone is a **one-time cloud activation** (documented in
`FLIGHT_GATING.md §A`; DUML `0x00/0x32` + `0x03/0x62`). Once the FC has recorded activation it
persists on the aircraft and is re-read, not re-authenticated, each session.

Consequence for the PC ground-station: **skip the entire account domain.** You do not implement
login, tokens, SSO or session storage to drive an activated WM160 over DUML/AOA. The only account-
gated features you lose are cloud/online extras (GEO-zone self-unlock, DJI Care/membership, flight
records sync, firmware download entitlement) — none of which are motor-start gates on a <250 g WM160.

---

## 1. Package / class map (what implements the domain)

| Layer | Package | Role |
|---|---|---|
| **Login/auth HTTP (real endpoints)** | `com.uav.accountui.accountdependency.*` (`classes_07a5000c.dex`) | Retrofit `ApiService`, base-URL config, header interceptor, response models. This is the actual DJI-SSO REST client. |
| **Account-center service facade** | `uav.component.accountcenter.*` (interfaces) + `uav.account.manager.UAVAccountCenterService` (`classes_03a5700c.dex`) | `IAccountCenterService` — login/register/logout/getToken/getProfiles/checkToken, listeners. |
| **Startup token check** | `uav.account.manager.AccountCenterBackgroundLogic` (`classes_03a5700c.dex`) | `initCheckToken` — validates stored token on app launch. |
| **Session storage / cross-app share** | `uav.account.manager.UAVAccountShareReceiver` (+ `$Keys`), `uav.account.util.UAVSharedPreferencesManager` | Persists token/uid/email; broadcasts to sibling DJI apps. |
| **Membership / DJI Care (separate server)** | `uav.account.manager.UAVMemberManager`, `uav.account.protocol.MemberProtocolBox`, `UserCenterApi`, `MemberProtocolHttpClient` | `https://api.uavservice.org/` — member/VIP, **not** login. |
| **UI (single-letter obfuscated)** | `com.dji.account.ui.*`, `activity_account_*`, `fragment_account_center_*` layouts | Sign-in screens, one-click/WeChat/OAuth buttons. |
| **SDK value objects (account→drone)** | `uav.sdk.keyvalue.value.common.UserAccountLoginInfo`, `uav.sdk.systeminfo.UserAccountLoginInfo` (`classes_0451d00c.dex`) | Container `{token,userID,email,city,userPhone,userApiCenterID}` the SDK can push down for GEO — see §6. |

**None of these classes are model-specific.** The account stack is identical for every DJI aircraft;
there is no WM160/UAV59 branch in it. (Model filtering happens later, in flight/GEO logic, not here.)

---

## 2. The auth flow — DJI SSO / OAuth + cookie-token, over HTTP

### 2.1 Host

Base URL resolves through `AccountCenterBaseUrlConfig` (`classes_07a5000c` `AccountCenterBaseUrlConfig.smali`),
which for the three environments calls `HttpApiBaseConfig.y0()/Y0()/k0()` (prod/pre/dev). The
resolved production host is **`https://account-api.dji.com`** (string present in
`classes_016b200c.dex`, `classes_09b2900c.dex`). Staging host seen elsewhere: `stag-dsapi.dbeta.me`.

### 2.2 REST endpoints (from `ApiService.smali` retrofit annotations)

All are `POST` `application/x-www-form-urlencoded` under `apis/apprest/` on the account host unless a
`@Url` (absolute) is noted. Method → path → form fields:

| App method | HTTP path | Form fields |
|---|---|---|
| `getConfig` | `apis/apprest/v1/init_data` | `appId` |
| `checkIfAccountExist` | `apis/apprest/v1/check_account_exist` | `email, areaCode, phone, accountType, locale, srandom, verificationCode` |
| `getCaptcha` | `apis/apprest/v1/validate_captcha` (captcha) | `srandom` |
| `validateCaptcha` | `apis/apprest/v1/validate_captcha` | `captchaModule, srandom, verificationCode` |
| `sendSmsCode` | `apis/apprest/v2/send_code` | `smsCodeModule, captchaTicket, areaCode, phone, smsType` |
| `checkSmsCode` | `apis/apprest/v1/check_code` | `areaCode, phone, smsCode, smsType` |
| `sendEmailCode` / `sendEmailValidationCode` | `apis/apprest/v1/send_email`, `apis/apprest/v2/email/validation/send` | `email, captchaTicket, emailType` / `captchaTicket, emailType` |
| `validateEmail` | `apis/apprest/v2/email/validation` | `emailCode` |
| **`login`** (email/phone + password) | **`apis/apprest/v1/user_login`** | `areaCode, userName, password, verificationCode, srandom` |
| **`login_or_register_with_sms_code`** (phone + SMS) | `apis/apprest/v1/login_or_register_with_sms_code` | (phone/areaCode/smsCode) |
| `registerWithEmailAndLogin` | `apis/apprest/v1/email_register_with_login` | `email, password, confirmPassword, userType, subscription, verificationCode, srandom` |
| `registerWithPhoneAndLogin` | `apis/apprest/v1/phone_register_with_login` | `areaCode, phone, smsCode, subscription` (also a password variant) |
| `resetPasswordWithPhone` | `apis/apprest/v1/phone_reset` | `areaCode, phone, password, confirmPassword, smsCode` |
| `resetPasswordWithEmail` | `apis/apprest/v1/send_reset_email` | `email, backUrl, verificationCode, srandom, locale` |
| **`authUrl`** (get OAuth authorize URL) | **`apis/apprest/v1/tokenv2/generate/authurl`** | `backUrl, token, locale` |
| **`loginByOAuth`** (exchange code) | `@Url` (absolute) | `code` |
| `bindPhoneByOAuth` / `oauthBindEmailByCode` / `oauthBindEmailByPassword` | `@Url` | `phone/areaCode/oauthTicket/smsCode/password` etc. |
| **`simLogin`** (carrier one-click) | **`/apis/apprest/v2/one-click-login`** | `oneClickLoginToken` |
| `bindPhone` | `apis/apprest/v2/phone/binding` | `areaCode, phone, smsCode, emailValidateTicket` |
| `getVcode` | `apis/apprest/v1/vcode` | — |
| `bindingInfo` | (GET) | — |

### 2.3 What a login returns — `LoginResponse` (`@SerializedName` in `LoginResponse.smali`)

```
user_id, nick_name, inner_email, register_phone, area_code, vip_level,
cookie_name, cookie_key       <-- the session token is delivered as an HTTP COOKIE (name+value)
```
`OAuthLoginResponse = { success:bool, LoginResponse, Info, ticket:String }`.

**Auth model:** DJI SSO issues a **cookie-based session token** (`cookie_name`/`cookie_key`). The
app stores that value as its "token" (see §3) and replays it via `HeaderInterceptor`
(`accountdependency.HeaderInterceptor`) on subsequent authenticated calls. The `authUrl` +
`loginByOAuth(code)` pair is the standard OAuth-authorize-then-exchange-code flow used for
third-party sign-in (WeChat/Apple/Google/Facebook/ByteDance buttons are gated by
`ConfigResponse$CustomizedPageConfigBean` flags: `wechatLoginVisible`, `appleLoginVisible`,
`googleLoginVisible`, `facebookLoginVisible`, `bytedanceLoginVisible`).

**Carrier one-click / SIM login (`simLogin`, `one-click-login`)** is **China-only**: it depends on
`com.mobile.auth.gatewayauth` (Aliyun) and `cmpassport.com` / `unicom.online.account`
(China Mobile/Unicom) SDKs (`classes_069e600c.dex`, `com.unicom.online.account.shield`).
NOT-relevant for a Western WM160 / PC project.

---

## 3. Session storage — what is persisted, and where

### 3.1 Local SharedPreferences keys (const-strings in `uav.account` package)

```
key_account_token           <- the session cookie/token value
key_token_expire_time       <- expiry (see checkTokenExpireTime)
key_account_is_token_valid
key_account_is_register_phone
oneClickLoginToken          <- carrier one-click token (CN)
latest_login_account_info   <- last account blob (for account switcher / prefill)
```
(All appear as literal keys in the account manager/protocol smali.)

### 3.2 Cross-app account sharing — `UAVAccountShareReceiver`

DJI apps share a signed-in account via a broadcast. Action = **`android.intent.action.APP.AccountSet`**;
payload keys (`UAVAccountShareReceiver$Keys`):
```
key_account_token, key_account_uid, key_account_email, key_has_actived
```
So a token obtained by any co-installed DJI app (Fly, GO, etc., same signature) is broadcast and
picked up here. `key_has_actived` is the **cloud** activation flag, distinct from the FC-side
activation state read over DUML.

---

## 4. Token lifecycle — check / refresh / logout

- **Startup check:** `AccountCenterBackgroundLogic.initCheckToken` → `IAccountCenterService`
  `checkToken` calls (log tags: `checkToken res status=`, `checkToken token start with`,
  `checkTokenExpireTime invalid`, `checkToken not login`). On invalid → `actionHandleTokenInvalid` /
  `paramHandleTokenInvalid` broadcast; `OnTokenInvalidListener` fires.
- **Profiles:** `getProfilesByToken` (log `getProfilesByToken success t=%s`) fetches the user profile
  with the stored token.
- **Refresh reporting:** `postTokenRefreshInfoToServer` pushes refreshed token info back up.
- **Logout:** `actionLogout` / `actionLogoutNoWeb`; log tags `logout success/failure`,
  `onLogOut() refresh DpadServiceManager status`. Clears the stored token/flags. **Logout does not
  touch the aircraft** — it only clears app state and re-broadcasts account-cleared.

The account-center facade methods (`IAccountCenterService`, `classes_03a5700c`) are single-letter
obfuscated (e.g. `login/logout/getToken/getMemberInfo` map to `S0/U/b0/R/V1`…), so mapping a specific
Java method name to a specific REST call statically is unreliable — resolve at runtime (see §8).

---

## 5. Membership / DJI Care — a *different* server (not login)

`UAVMemberManager` + `MemberProtocolBox` + `UserCenterApi` + `MemberProtocolHttpClient`
(`classes_03a5700c`) hit **`https://api.uavservice.org/`** for VIP/membership/DJI-Care-Refresh data,
authenticated with the same `token`. Log tags: `getProfilesByToken`, `handleResultSuccess
CMDID_ACCOUNT_CENTER_LOGIN`. This is entitlement/UI only; **no drone control, not a flight gate.**

---

## 6. The ONLY account↔drone coupling on WM160

There is no "send login to FC" command. Three account-adjacent DUML/SDK items exist; only the first
matters for flight:

1. **One-time activation (the real gate).** Fully documented in `FLIGHT_GATING.md §A`. Summary:
   - `DataFlycActiveStatus.start()` → **`cmd_set 0x00` (`CmdSet.a` COMMON) / `CmdIdCommon.t = 0x32`**
     = `uav_general_activate_device` (`full_table.txt 0x00/0x32`). Queries FC activation state.
     (`DataFlycGetPushActiveRequest` = FC→app "please activate".)
   - Cloud activation itself (requires a logged-in DJI account + internet) → same `0x00/0x32`
     `uav_general_activate_device_req`.
   - App reports result to FC: **`DataFlycSetActiveResult.start()` → `cmd_set 0x03` (`CmdSet.d`
     FLYC) / `CmdIdFlyc.U = 0x62`**, receiver FLYC, 44-byte payload
     `[0..3] UAVActivationState u32 | [4..7] u32 | [8..11] u32 | [12..43] 32-byte string`.
     `UAVActivationState`: `Success=0, NoNetwork=1, InvalidId=2, FailedForNet=3, OTHER=100`.
   - FC refusal surfaces in the OSD push at **byte +0x33** as `FC_CANNOT_TAKE_OFF_DRONE_NOT_ACTIVATED`
     (`MASTER_REPORT.md §6`, `diag_codes.py`). Corroborating strings:
     `error_account_user_not_activated_313`, `home_account_not_activated`.
   - **Verdict WM160: needed once, if factory-fresh. Persists on the aircraft. Not per-flight, not a
     login.**

2. **Secure device/user binding (query-only).** `full_table.txt`:
   - `0x00/0xE5` `uav_general_get_secure_binding` (get)
   - `0x00/0xE6` `uav_general_get_secure_device_user_bind`
   These *read* whether the aircraft is bound to a DJI user; the app does not need to write them to
   fly. Whether WM160 firmware answers them is undecidable statically — confirm with a live probe.

3. **`UserAccountLoginInfo` SDK value (GEO/flysafe only).** `uav.sdk.keyvalue.value.common.UserAccountLoginInfo`
   = `{ token, userID, email, city, userPhone, userApiCenterID }` (Parcelable, `classes_0451d00c`).
   Carried by the SDK's key-value layer to seed GEO/no-fly-zone self-unlock with the logged-in user.
   It is **not wired to a DUML flight command** in the static graph (no key references it in
   `classes_0451d00c`), and GEO self-unlock is licence-based (signed by DJI certs; `FINDINGS.md §8`),
   not a motor gate on a sub-250 g WM160. Safe to ignore for the PC project.

---

## 7. WM160 support matrix

| Item | WM160 | Note |
|---|---|---|
| DJI-account login / SSO / token | app-side only | Model-agnostic; **not needed to fly an activated unit** |
| One-time cloud activation | required once if factory-fresh | `0x00/0x32` + `0x03/0x62`; persists on FC |
| Per-flight login DUML | **does not exist** | No such command in `full_table.txt` |
| Secure user-bind query `0x00/0xE5/0xE6` | firmware-dependent | read-only; live-probe to confirm WM160 answers |
| GEO/NFZ self-unlock (account+licence) | supported but licence-signed | Not a motor gate on <250 g WM160 |
| DJI Care / membership (`api.uavservice.org`) | app extra | Not flight-related |
| Carrier one-click / SIM login | **NOT-WM160-relevant** | China-only (Aliyun/cmpassport/unicom SDKs) |
| Third-party OAuth (WeChat/Apple/Google/FB/ByteDance) | UI-config gated | Cosmetic; not flight-related |

---

## 8. What needs a live capture / Frida (undecidable statically)

The packer + single-letter obfuscation on `IAccountCenterService` means the following are best
confirmed at runtime rather than trusted from static reads:

- **Exact login round-trip & cookie name.** Hook `com.uav.accountui.accountdependency.ApiService`
  Retrofit calls, or `OkHttp` (`HeaderInterceptor.intercept`), to capture the real request to
  `account-api.dji.com/apis/apprest/v1/user_login` and the `Set-Cookie` (`cookie_name`) token. Frida:
  intercept `okhttp3.Interceptor$Chain.proceed` / dump `Request`+`Response`.
- **Which obfuscated `IAccountCenterService` letter is `login`/`getToken`.** Hook
  `uav.account.manager.UAVAccountCenterService` methods and log args/returns to bind names to REST
  calls.
- **Whether WM160 firmware actually responds to `0x00/0xE5` / `0x00/0xE6` (secure user-bind).**
  Send the frames over COM5/AOA and observe the reply — pure firmware behavior, not in the APK.
- **The definitive "does my unit need activation" answer.** Read the OSD push **byte +0x33** live;
  if it maps to `DRONE_NOT_ACTIVATED` (`diag_codes.py MOTOR_NOT_START`), activation is missing —
  otherwise no account action is needed at all.
- **Static class bodies behind the packer.** The account classes live in `unpacked_app_dex/*.dex`
  (already unpacked here). If a future build re-packs them, hook `uav.account.protocol.*` /
  `com.uav.accountui.accountdependency.*` after DEX load.
