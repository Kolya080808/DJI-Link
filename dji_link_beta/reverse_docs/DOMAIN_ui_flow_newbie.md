# DOMAIN: ui_flow_newbie — App UI flow, gating screens, newbie guide, hidden menus (WM160 / Mavic Mini 1 / UAV59)

Scope: the app's user-facing navigation graph — main page, connection guide, pre-flight "Start Fly" gate, the newbie/beginner guide overlays and gates, the developer/factory hidden menus, and dialog-driven unlocks. Filtered to **WM160 = Mavic Mini 1 = UAV59 = did 59**.

**Evidence base.** The prompt named `com.dji.mainpageui` / `uav.service.newbieguide`. The real (post-packer) code ships under **`com.uav.mainpageui`** (main page + connection guide + newbie-guide overlay), **`uav.service.newbieguide`** (the persistence/service impl), **`uav.component.newbieguide`** (interface), and **`com.uav.productconfig.newbieguide.generate`** (per-drone resource config). All packages are inside the unpacked DEX set, decompiled here with `baksmali` from:
- `unpacked_app_dex/classes_00b9d00c.dex` → `com.uav.mainpageui.*` (main page, connection guide, newbie overlay)
- `unpacked_app_dex/classes_016b200c.dex` → `uav.service.newbieguide.UAVNewbieGuideServiceImpl`
- `unpacked_app_dex/classes_03a5700c.dex` → `uav.component.newbieguide.INewbieGuideService`, `uav.component.appstate.DeveloperModeToolConfig`
- `unpacked_app_dex/classes_07a5000c.dex` → `com.uav.accountcenterui.setting.*` (Settings, developer mode, developer settings)

Class list cross-ref: `reverse_docs/all_classes.txt`. Per-drone resource manifest: `decompiled/assets/cloud_resource/dynamic_names/59_config.json`.

> **Nature of this domain (read first).** This is the **UI/navigation and gating layer**, not a wire protocol. It does **not** send DUML `cmd_set/cmd_id` frames directly. Screen transitions are driven by (a) **MSDK/CSDK key reads** — `Lcom/uav/rx/csdk/RxCSDK;->P(Luav/sdk/keyvalue/key/UAVKeyInfo;)` (synchronous get) and Rx observables on the same keys; and (b) **SharedPreferences persistence flags** via `Lcom/uav/component/persistence/PersistenceDataListener;`. The only network I/O in-domain is an HTTP device-UUID report inside the hidden developer menu (below). Where a decision (e.g. "does WM160 enter the RC connection guide vs the Wi‑Fi card") is resolved from a runtime key or a cloud product-config blob and cannot be read statically, it is flagged **[LIVE-CAPTURE]** with the exact Frida hook.

---

## 1. Top-level screen / Activity map (WM160-relevant)

| Screen | Class (evidence) | Role |
|---|---|---|
| Main page (home) | `com/uav/mainpageui/mainpage/BaseMainPageActivity` hosting a `mainPageFragment` | Landing hub after launch/login; entry to Fly, Album, Me/Settings, Wi‑Fi connect card |
| Connection guide (wizard) | `com/uav/mainpageui/connectionguide/ConnectionGuideActivity` | Step-by-step "connect your drone" wizard, driven by a 22-state machine (§3) |
| Step-indicator sub-flow | `com/uav/mainpageui/connectionguide/StepIndicatorActivity` (+ `StepIndicatorViewModel`) | Numbered progress stepper used inside the guide |
| Newbie-guide overlay | `com/uav/mainpageui/newbieguide/MainPageNewbieGuideView` (+ `MainPageNewbieGuideComponent`, `NewbieGuideHoleView`, `parts/PopupView`, `parts/TriangleView`) | Spotlight/hole coach-mark overlay drawn on top of the main page |
| Settings root | `com/uav/accountcenterui/setting/UAVSettingsFragment` (+ `UAVSettingsViewModel`) | Me → Settings; contains "Replay Beginner Guide", version-tap dev unlock |
| About | `com/uav/accountcenterui/setting/about/UAVAboutFragment` | Version display ("V %s"), part of the dev-unlock tap target chain |
| Developer settings | `com/uav/accountcenterui/setting/developer/UAVDeveloperSettingsFragment` | Hidden page, only reachable after unlock (§5) |
| Developer tools | `com/uav/accountcenterui/setting/developermode/DeveloperModeToolsFragment` + `DevelopAppCaptureBarCodeActivity` | QR-scan / device-UUID report tool (§5) |
| Direct-FPV setting | `com/uav/accountcenterui/setting/directfpv/UAVDirectFpvFragment` | Toggles "direct connect / direct FPV" path |

The main page also drives these gating flows (they open dialogs/fragments rather than full Activities):
- `mainpage/business/StartFlyEntryClickRouterDelegate` — the **pre-flight gate** when the user taps "Start Fly" (§4).
- `mainpage/business/GetHomeActivateRemindDialogStateChangeObservableUseCase` + `HomePageViewAction` — the **activation reminder dialog**.
- `mainpage/business/CheckFirstLoginUseCase`, `CheckAppUpgradeUseCase`, `HandleActivateSuccessUseCase`, `InitProductRecognizeRegisterHandlerUseCase` — startup gates.
- `mainpage/business/wificonnect/*` — the **Wi‑Fi connect card** on the home page (WM160's normal connect path, §6).

Startup ordering note: `BaseMainPageActivity` handles `privacy&&auth_dialog_source` and `set_main_page_activity_has_showed_action`, plus firmware-update routing (`NEED_GO_FIRMWARE_UPDATE_ACTIVITY`, `upgrade_activity_entrance`, `upgrade_activity_auto_download_or_upgrade`) and an **AOA branch** `startDeviceActivityByAoaActivity` (const-strings in `BaseMainPageActivity.smali`). The AOA branch is directly relevant to the PC-control project — it is how the app pivots when an Android-Open-Accessory host is attached; behaviour is **[LIVE-CAPTURE]** (hook `BaseMainPageActivity` `onCreate`/intent handling).

---

## 2. Connection-guide step fragments (the wizard screens)

All under `com/uav/mainpageui/connectionguide/step/…`, wired to states by `ConnectionGuideActivity` (each fragment class is referenced 4× in `ConnectionGuideActivity.smali`, i.e. it is in the state→fragment `when` map):

```
ConnectionGuideSelectConnTypeFragment      (selectconntype/)   choose Glass/RC/Wi‑Fi/Beacon
ConnectionGuideDeviceSelectFragment        (selectdrone/)      pick which drone
ConnectionGuideRcSelectFragment            (selectrc/)         pick which remote
ConnectionGuideOpenRcGlassFragment         (openrc/)           "turn on your RC/goggles"
ConnectionGuideRcTurnOnFragment            (pairrc/)           RC power-on confirm
ConnectionGuidePairRcFragment              (pairrc/)           RC pairing
ConnectionGuideSearchRcFragment            (searchrc/)         searching for RC
ConnectionGuideDroneTurnOnConfirmStepFragment (droneturnon/)   "turn on your drone" confirm
ConnectionGuidePairDroneStepFragment       (pairdrone/)        drone pairing
ConnectionGuideSearchDroneStepFragment     (searchdrone/)      searching for drone
ConnectionGuidePairDroneFailHelpFragment                       pairing-failed help
ConnectionGuideConnectDeviceWifiFragment   (connectwifi/)      connect phone to device Wi‑Fi  ← WM160
PlugInSdrDongleStepFragment                (pluginsdrdongle/)  plug in SDR dongle             NOT-WM160
ConnectionGuideSpecialConnectFragment      (specialconn/)      "special connection" branch
ConnectionGuideToPcFragment                                    guide user to PC connection
ConnectionGuideToOpr94Fragment                                 guide to OPR/"94" device
ConnectionGuideNeedUpdateFragment                              firmware update required
ConnectionGuideNeedGuideOtherRcFragment                        route to another RC's guide
ConnectionGuideRcNotSupportFragment                            "this RC not supported"
```
Base class: `ConnectionStepBaseFragment`.

`ConnectionGuideToPcFragment` is notable for the PC-control project — the stock app itself has a "connect to PC" guide screen. Its trigger is `ConnGuideState$SpecialConnState` (referenced from `ConnGuideAction`, `ConnGuideState`, `ConnectionGuideActivity`).

---

## 3. Connection-guide state machine

### 3.1 `ConnType` enum — **wire ordinals** (from `ConnType.smali` `<clinit>`)

| Field | Name const-string | ordinal (`const/4 v2`) |
|---|---|---|
| `a` | `"Glass"` | 0 |
| `b` | `"Rc"` | 1 |
| `c` | `"Wifi"` | 2 |
| `d` | `"Beacon"` | 3 |
| `e` | `"Unknown"` | 4 |

`ConnTypeInfo` wraps a `ConnType` with metadata. **WM160 (Mavic Mini 1) connects over enhanced Wi‑Fi via its RC → `Wifi` (ordinal 2) is the relevant type; `Beacon` (3) and the SDR-dongle path are NOT-WM160.** The exact incremental type list offered for a given drone is computed in `ConnectionGuideService.getIncrementalTypeListObservable` and `ConnGuideStateManager` ("`direct connect device support list=`" log) from runtime capability — **[LIVE-CAPTURE]**: hook `Lcom/uav/mainpageui/connectionguide/ConnGuideStateManager;` `watchingConnectState` and `ConnGuideStateManagerRepo->getIsWifiLinkObservable`.

### 3.2 `ConnGuideState` sealed class — all 22 states

From `com/uav/mainpageui/connectionguide/ConnGuideState$*.smali`:

```
StartState                 SelectConnectTypeState     SelectDroneState
SelectRcOrGlassState       OpenRcOrGlassState         SearchRcState
PairRcState                RcTurnOnConfirmState       SearchDroneState
PairDroneState             DroneTurnOnConfirmState     PairDroneHelpGuide
OpenDeviceWifiState        SpecialConnState           PlugInSdrDongleState
NeedUpdateState            NeedUpgradeDroneState       NeedDa2Upgrade
NeedGuideOtherRc           GuideRcNotSupport           FinishState
AbortState
```

Manager: `ConnGuideStateManager` (+ `Companion`, `WhenMappings`). Transitions are logged as `"curr state <X> , start trans to <Y>"`, `"push state <Z>"`, `"state not changed"` (const-strings in `ConnGuideStateManager.smali`). Inputs are Rx observables from `ConnGuideStateManagerRepo`: `getDroneConnectObservable`, `getRcConnectObservable`, `getGlassConnectObservable`, `getBeaconConnectObservable`, `getIsWifiLinkObservable` — combined via `combineLatest`. If no supported drone is found it logs `"no supported drone, exit connection guide"` and aborts. Actions enum: `ConnGuideAction`.

WM160 typical path (Wi‑Fi): `StartState → SelectConnectTypeState → SelectDroneState → OpenDeviceWifiState (ConnectDeviceWifiFragment) → SearchDroneState → FinishState`. The RC-pairing states (`SearchRcState`/`PairRcState`/`PlugInSdrDongleState`/`NeedDa2Upgrade`) belong to OcuSync/SDR products and are **NOT-WM160**. Exact per-WM160 path is runtime-selected — **[LIVE-CAPTURE]**.

---

## 4. Pre-flight gate — "Start Fly"

Entry point: `mainpage/business/StartFlyEntryClickRouterDelegate` (the delegate invoked when the home "Start Fly" entry is clicked). Sequence of checks (from const-strings + component lookups in `StartFlyEntryClickRouterDelegate.smali`):

1. **Calibration component** `CaliUpgradeUIComponent` — "Cali Permission To Fpv" / logs 标定组件判断是否可以进入FPV调用失败 ("calibration component judging whether FPV can be entered failed"). Reads a `permission response des`.
2. **Database-upgrade component** `DatabaseUpgradeUIComponent` — reads `key_permission_des` / `key_permission_result`; logs 数据库升级组件判断… ("database-upgrade component decides whether some pages can be entered from home").
3. **Firmware/upgrade** `UpgradeUIComponent`.
4. **Wi‑Fi capability router** `GoNextByWifiCapabilitiesUseCase` → produces a `GoNextActionResult(actionType=…)`.

### 4.1 `GoNextActionType` enum (from `startfly/GoNextActionType.smali`)

| ordinal | name | meaning |
|---|---|---|
| 0 | `GoFPV` | proceed into the flight/FPV view |
| 1 | `ShowWifiLinkSwitchView` | prompt to switch Wi‑Fi link |
| 2 | `ShowWifiModeView` | prompt Wi‑Fi mode |
| 3 | `ShowFirmWareUpdateView` | force firmware update first |
| 4 | `None` | no-op |

Result carrier: `GoNextActionResult { actionType, sideInfo }`. View state: `StartFlyViewState { ComboState a; boolean b }`.

### 4.2 `ComboState` events (from `startfly/ComboState.smali`)
`EVENT_RC_CONNECT`, `EVENT_CONNECT`, `EVENT_DISCONNECT`, `EVENT_ACTIVATE`, `EVENT_GLS_CONNECT`, `EVENT_WIFI_FAST_CONNECTED`. These are the aggregated connection/activation signals that light up (or gray out) the Start-Fly button. `CollectAllConcernedCheckStateUseCase` / `GetTriggerCheckStateObservableUseCase` / `AllConcernedCheckState` feed it. For WM160 the relevant events are `EVENT_CONNECT`, `EVENT_ACTIVATE`, `EVENT_WIFI_FAST_CONNECTED` (Wi‑Fi fast connect card, §6); `EVENT_GLS_CONNECT` (goggles) and `EVENT_RC_CONNECT` (OcuSync RC) are NOT-WM160.

### 4.3 Activation-reminder dialog
`GetHomeActivateRemindDialogStateChangeObservableUseCase` emits `HomePageViewAction`:
- ordinal 0 `ShowNotActivateRemindDialog`
- ordinal 1 `HideNotActivateRemindDialog`

Until the aircraft is activated, this dialog blocks/annoys entry. Activation success is handled by `HandleActivateSuccessUseCase`. WM160 must be activated once (network) before free flight — this is the activation gate. Whether WM160 is currently activated is an MSDK key/read — **[LIVE-CAPTURE]** hook `HandleActivateSuccessUseCase` / activate data source.

---

## 5. Developer / factory hidden menus & the unlock

### 5.1 Unlock: tap the version number 5×
In `com/uav/accountcenterui/setting/UAVSettingsViewModel.smali`:
- field `versionClickCount:I`, `lastVersionClickTime:J`.
- Each version tap: if too long since last tap it resets the counter, else `versionClickCount += 1`.
- Threshold check: `const/4 v1, 0x5 ; if-lt versionClickCount, 5 → cond` — **5 rapid taps**.
- **Gate:** it only unlocks if MSDK key `Luav/sdk/keyvalue/key/UAVProductKey;->i0` reads `Boolean.TRUE` (`RxCSDK.P(UAVProductKey.i0).equals(TRUE)`). If so it calls `UAVSettingsRepository.saveDisplayModeSwitch(true)`, posts toast `R.string.me_developer_turn_on_toast` = **"Developer options enabled"**, and resets the counter.

So the "hidden developer menu" is a **soft/software unlock** (5 taps on the version), further gated by a product capability key `UAVProductKey.i0`. **Whether WM160 satisfies `UAVProductKey.i0` is [LIVE-CAPTURE]** — hook `Lcom/uav/rx/csdk/RxCSDK;->P(Luav/sdk/keyvalue/key/UAVKeyInfo;)Ljava/lang/Object;` and filter for `UAVProductKey.i0`, or hook `UAVSettingsViewModel` version-click handler directly. There is no evidence of a DUML "factory mode" cmd in this domain; factory/engineering functions on WM160 live in the DUML layer (see `MASTER_REPORT.md` / `DUML_COMMANDS_FULL.md`), not this UI package.

### 5.2 Developer settings page
`com/uav/accountcenterui/setting/developer/UAVDeveloperSettingsFragment` — appears once display-mode switch is on. Config object: `uav.component.appstate.DeveloperModeToolConfig { List domainWhiteList }` (a whitelist of domains the dev tools may hit). Koin bindings: `KoinDefUavComponentAppstateDeveloperModeToolConfig`, `KoinDefComDjiComponentApplicationConfigGetDeveloperModeToolConfig`.

### 5.3 Developer tools (QR / device-UUID report)
`DeveloperModeToolsFragment` + `DevelopAppCaptureBarCodeActivity`:
- Shows `deviceIdTv` / `fcuIdTv` (+ copy buttons `deviceIdCpTv`, `fcuIdCpTv`), reads MSDK `KeyProductUUID`.
- Scans a `QR_CODE` (`scanView`, `registerForActivityResult`).
- Posts an HTTP report (`postScanUrl`): `Content-Type: application/json; charset=utf-8`, JSON body fields `drone`, `mbrand`, `mmodel`, `sn`; headers `X-Request-ID`, `X-Device-Id`, `X-Namespace: 1`, `X-App-Ver`, `X-App-Plat: 2`. Response models `UuidReportResponseModel` / `UuidReportResponseResult`. **Endpoint URL is built at runtime (`$url`) and constrained to `DeveloperModeToolConfig.domainWhiteList` — [LIVE-CAPTURE]** hook `DeveloperModeToolsFragment$postScanUrl$*`.

This is a device-provisioning/UUID tool, model-agnostic; usable with WM160's UUID but not WM160-specific.

---

## 6. WM160's normal connect path — Wi‑Fi connect card (not the RC wizard)

`com/uav/mainpageui/mainpage/business/wificonnect/`:
- `WifiConnectCardState`, `GetWifiConnectCardStateChangeObservableUseCase`, `GetWifiConnectScanDeviceObservableUseCase`, `WifiConnectCardEntryClickUseCase`, `SharedModule_ProvideWifiConnectDataSourceFactory`.
- Settings has `key_wififast_device_connect_once` and `UAVWifiFastSettingsFragment` — a "Wi‑Fi fast connect" one-time flag.

For a Wi‑Fi-link drone (WM160 class), the home page shows a **Wi‑Fi connect card** that scans for the device and connects, rather than forcing the full RC/SDR connection guide. The `EVENT_WIFI_FAST_CONNECTED` combo event (§4.2) confirms this path feeds the Start-Fly gate.

---

## 7. Newbie / beginner guide

### 7.1 Service & interface
- Interface `uav.component.newbieguide.INewbieGuideService` — abstract methods `c()`, `d(Z)`, `e(Z)`, `f()Z`, `j()Z`, `l()Z`, `m(Z)`, `n()`.
- Impl `uav.service.newbieguide.UAVNewbieGuideServiceImpl` (a prioritized app service: `getName()`, `priority()`, `init(Context)`). Persists state via `PersistenceDataListener`. Persistence keys it manages:
  - `key_newbie_guide_fly_practice` — "fly practice" completion.
  - `key_mini_rc_newbie_guide` — **Mini-RC newbie guide** (WM160's RC family).
  - `key_beacon_newbie_guide_has_complete` — beacon tutorial done (**NOT-WM160**, beacon flow).
  - broadcasts `ACTION_OPEN_BEACON_TUTORIAL_COMPONENT` (NOT-WM160).
- `uav.service.newbieguide.JudgeEnterBeaconTutorialModel { isDirectDevice, beaconTutorialCompleteState, guideFlyPracticeState, isInFpv, isInMainPage, isBeaconConnect, beaconActivateStatus }` — decides whether to launch the beacon tutorial. WM160 has no beacon, so this evaluates false for WM160 (**NOT-WM160**).

### 7.2 Main-page coach-mark overlay
`MainPageNewbieGuideView` / `MainPageNewbieGuideComponent`:
- Gated by SharedPreferences flag **`key_newbie_guide_main_page_2024_1`** (shown once; logs `"got has showed: <bool>"`).
- Injected via broadcast `action_inject_mainpageui_newbieguide_view`.
- Renders a spotlight hole (`NewbieGuideHoleView`) + popup bubble (`parts/PopupView` with `parts/TriangleView`, `parts/TriangleOrientation`, `parts/BaseAlign`). Utility: `newbieguide/utils/NewbieGuideViewUtils`.

### 7.3 Per-drone newbie-guide resource config — **WM160 is fully supported**
`com.uav.productconfig.newbieguide.generate`:
- `DroneNewbieGuideProductConfigList` (`droneList`), `IDroneNewbieGuideProductConfig` (per-drone getters `a()`…`z()` returning strings/Files/Drawables + `getId()`), config keys in `NewbieGuideProductConfigKey$key…ByDrone`:
  `keyEntranceImage`, `keyCheckDroneTitle`, `keyCheckPropellerImage`, `keyCheckTailImage`, `keyCheckGimbalImage`, `keyCheckGimbalTip`, `keyCheckRC`, `keyCheckRCTip`, `keyNoGpsImage`, `keyBrakeReturnHome`, `keyBtnTakeOffVideo`, `keyAircraftBtnTeachVideo`, `keyFpvHighlightSlowmoTutorialVideo`, and `keySpotlight{Free,Standard}TeachVideo[1..6]`.

**WM160 evidence** — `assets/cloud_resource/dynamic_names/59_config.json` (`"did": 59`) ships the concrete WM160 newbie-guide assets that back those keys, e.g.:
```
uav59_entranceimage_fpv_newbie_guide_entrance_uav59.png     ← keyEntranceImageByDrone
uav59_checkpropellerimage_fpv_newbie_guide_hardware_prepare_1.png ← keyCheckPropellerImageByDrone
uav59_checktailimage_fpv_newbie_guide_hardware_prepare_2.png     ← keyCheckTailImageByDrone
uav59_checkrc_img_teaching_4.png                             ← keyCheckRCByDrone
checkgimbalimage_img_teaching_2.png                          ← keyCheckGimbalImageByDrone
nogpsimage_img_teaching_160_3x.png                           ← keyNoGpsImageByDrone
uav59_brakereturnhome_genenal_rc_160.png                     ← keyBrakeReturnHomeByDrone
turnonvideourl_user_guide_movie_step2_uav59.mp4             (connection-guide turn-on video)
pairvideourl_user_guide_movie_step3_uav59.mp4              (connection-guide pairing video)
```
Plus IMU/compass/gimbal calibration UI images (`*_uav59_imucali_*`, `*_compass_calibration_*uav59*`, `gimbalcalibrationimage_…uav59_normal.png`) and `producthdpicture_uav59.png`. So the **WM160 newbie guide is a first-class, fully-resourced flow** (entrance card → hardware checklist propeller/tail/gimbal/RC → no-GPS teaching → brake-to-RTH → take-off video).

The extra `Spotlight*`/`Portrait*`/`Fancy*`/`MasterShot`/`QuickShot`/`HandheldGesture` guides in §7.4 are QuickShot/handheld/portrait features of newer drones (Neo/Mini-4 class) and are **NOT-WM160** unless a WM160 asset is present (only the ones listed above are).

### 7.4 "Replay Beginner Guide" — dialog-driven reset (Settings)
Settings → `me_more_settings_reset_newbie_guide_title` = **"Replay Beginner Guide"**, confirm dialog `me_more_settings_reset_newbie_guide_dialog_content` = "Reset Guide？", success toast "Reset successfully". `UAVSettingsViewModel` clears every newbie-guide SharedPreferences flag and calls `INewbieGuideService.n()` + `m(true)`. Full flag set reset (from `UAVSettingsViewModel.smali`):
```
key_newbie_guide_main_page_2024_1        key_newbie_guide_fly_practice
key_newbie_guide_no_gps_has_shown        key_beacon_newbie_guide_has_complete
key_mini_rc_newbie_guide                 key_aircraft_btn_take_off_guide_has_shown
key_has_shown_portrait_newbie_guide      key_no_more_portrait_safety_guide
key_fancy_guide_view_is_showed           key_fancy_mode_newbie_guide_has_showed
key_has_spotlight_guide_shown            key_handheld_gesture_guide_view_is_finished
key_handheld_gesture_guide_view_is_skipped  key_handheld_highlight_slowmo_tutorial_has_showed
key_panorama_free_guide_is_completed     key_panorama_free_guide_select_state
key_start_master_shot_bubble_guide_complete key_start_quick_shot_bubble_guide_complete
key_guide_master_shot_video_show         key_guide_master_shot_select_target_show
key_guide_quick_shot_video_show          key_guide_quick_shot_select_target_show
key_guide_config_hyper_lapse_bubble      key_guide_config_panorama_format_bubble
key_gallery_bubble_guide_{crate,download,edit,templates}_shown
key_bubble_me_page                       key_step_indicator_pop_window_shown
key_ignore_first_time_dialog_flag        key_complete_bubble_guide_smart_eye_config_view
key_complete_emergency_stop_bubble_guide_smart_eye_config_view
```
For WM160 the meaningful ones are `key_newbie_guide_main_page_2024_1`, `key_newbie_guide_fly_practice`, `key_newbie_guide_no_gps_has_shown`, `key_mini_rc_newbie_guide`, `key_aircraft_btn_take_off_guide_has_shown`; the rest belong to QuickShot/handheld/portrait/smart-eye features other models expose.

---

## 8. Other dialog-driven unlocks / gates on the flow

- **Privacy & auth gate:** `BaseMainPageActivity` const `privacy&&auth_dialog_source`; Settings routes to `IPrivacyConfigUIComponent` targets `ActivityPrivacyNoticeRequest`, `ActivityProductTerms`, `ActivityUserTerms`. Must be accepted before use.
- **Flight-limit unlock:** Settings launches login with `paramToggleLoginFrom = "from_change_unlock_fly_limit"` (`UAVSettingsFragment.smali`) — logging in unlocks the higher altitude/distance fly limits. Dialog/login-gated.
- **App-upgrade gate:** `CheckAppUpgradeUseCase` + `BaseMainPageActivity` firmware routing (`NEED_GO_FIRMWARE_UPDATE_ACTIVITY`) can force update before flight (`GoNextActionType.ShowFirmWareUpdateView`).
- **First-login gate:** `CheckFirstLoginUseCase`.
- **License:** Settings → `ILicenseManageComponent` `action_to_manage_activity`.

---

## 9. WM160 support summary

| Element | WM160 status |
|---|---|
| Main page + Wi‑Fi connect card | **Supported** (`EVENT_WIFI_FAST_CONNECTED` path) |
| Connection guide `ConnType=Wifi` (2) | **Supported** |
| Connection guide RC-pair / SDR-dongle / `Beacon`(3) states | **NOT-WM160** (OcuSync/SDR products) |
| Start-Fly gate (`GoNextActionType`, activation dialog, cali/db/fw checks) | **Supported** |
| Main-page newbie overlay `key_newbie_guide_main_page_2024_1` | **Supported** |
| Per-drone newbie guide (entrance/checklist/no-GPS/RTH/take-off video) | **Supported** — assets in `59_config.json` |
| Beacon tutorial (`JudgeEnterBeaconTutorialModel`) | **NOT-WM160** |
| QuickShot/MasterShot/Handheld/Portrait/SmartEye guides | **NOT-WM160** |
| Developer-mode unlock (5× version tap) | Software path exists; **gated by `UAVProductKey.i0` — [LIVE-CAPTURE]** |
| Developer UUID/QR tool | Model-agnostic |

## 10. Items needing a live capture / Frida
1. `UAVProductKey.i0` value on WM160 (does the 5-tap developer unlock actually fire?) — hook `Lcom/uav/rx/csdk/RxCSDK;->P(Luav/sdk/keyvalue/key/UAVKeyInfo;)Ljava/lang/Object;`.
2. WM160's actual connection-guide path & offered `ConnType` list — hook `ConnGuideStateManager->watchingConnectState` and `ConnGuideStateManagerRepo->getIsWifiLinkObservable`/`getDroneConnectObservable`.
3. AOA branch behaviour on the main page (relevant to PC control) — hook `BaseMainPageActivity` intent handling (`startDeviceActivityByAoaActivity`).
4. Activation state gating the Start-Fly dialog — hook `HandleActivateSuccessUseCase` / the activate data source.
5. Developer-tool HTTP endpoint + `domainWhiteList` — hook `DeveloperModeToolsFragment$postScanUrl$*`.
