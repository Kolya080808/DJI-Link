# DOMAIN: voice_audio — Voice / Audio Command Recognition

**Scope filter: Mavic Mini 1 = WM160 = UAV59.**
**Bottom line up front:** The app ships a full voice-control subsystem (wake-word → ASR → NLU → command parse → command execute), but it is a **handheld / voice-capable-aircraft feature gated dynamically by an aircraft-reported capability key that WM160 does not set**. Every command it can emit (QuickShots, ActiveTrack/tracking, Spotlight, follow modes, MasterShot, SmartPortrait) is a feature **WM160 firmware does not have**. **Verdict: NOT-WM160.** The recognizer is **third-party = Alibaba IDST NUI** (confirmed, not merely "likely").

Evidence base: `reverse_docs/all_classes.txt`, `reverse_docs/unpacked_app_dex/*.dex` (baksmali'd), `decompiled/lib/arm64-v8a/*.so`, `reverse_docs/full_table.txt`, `reverse_docs/cmdmap.txt`. Smali cited by class; disassembled from the DEX named in `all_classes.txt`.

---

## 1. What it does

An on-device (offline) voice pipeline that:
1. Captures mic audio (`libuavaudiorecord.so`, `libaudiodatacollector.so`).
2. Runs wake-word + noise-suppression + VAD + ASR + NLU via the **Alibaba IDST "NUI"** native engine, wrapped by DJI's `com.ml.speech.vui.VoiceAppManager`.
3. Produces a JSON recognition string, which a **native** parser turns into a structured `AudioCommandRecognitionResult`.
4. Executes that result — the actual DUML command to the aircraft/gimbal is **built and sent inside native code** — and returns an `AudioControlReturnCode`.

Two UI surfaces exist, both **handheld-FPV** oriented (not the WM160 map/flight UI): layouts `handheld_fpv_control_bar_speech_recognition`, `handheld_fpv_speech_recognition_panel`, `portrait_speech_recognition_layout`, `layout_shortcut_speech_subtitle` (seen in `all_classes.txt` under `kotlinx/android/synthetic/main/...`).

---

## 2. Class map & flow

### 2.1 Public component API — `uav.component.audiorecognition.*` (DEX `classes_03a5700c`)
- `IRecognitionService` (tag string `"AudioRecognitionServices"`) — top-level service interface. Notable abstract methods:
  - `U()`/`V()` → `io.reactivex.Observable` (event + recognized-command streams)
  - `R/w(IRecognitionResultCallBack)`, `p/T(IParseResultCallBack)`, `I/a0(RecognitionEventInterceptor)`
  - `q(uav.sdk.keyvalue.value.common.AudioCommandRecognitionResult, IExecutionResultCallback)` — **execute a parsed command**
  - `B(Z)`, `X(Z)`, `F()`, `j()`, `l()` — enable/switch/start/stop toggles
- `IRecognitionResourceService` — model-resource lifecycle: `isDownloading()`, `cancelDownload()`, `getConfig():SpeechConfig`, `e():String` (resource path/URL), reactive `d()/f()/j()/l()`.
- `RecognitionEvent` (sealed) subtypes: `AudioDataArrivedEvent`, `AudioRecognizedEvent`, `EnforceToRecognitionEvent`, `EnforceToWaitToRecognitionStateEvent`, `RecognitionSwitchEvent`.
- `RecognitionResult`, `RecognitionEventInterceptor`, callbacks `IParseResultCallBack` / `IRecognitionResultCallBack` / `IExecutionResultCallback`.
- `SpeechConfig` — single field `shouldShowProcess:Z` (default `false`).
- `SpeechConstants` — `SPEECH_FUNCTION_RES_ID = "87533dd19780279c2aed50f10c15936e"` (Alibaba NUI function/app resource id — the model bundle key).

### 2.2 Service implementation — `fly.audiorecognize.*` (DEX `classes_016b200c`)
- `service.RecognitionService` — the concrete `IRecognitionService`. Holds a `RecognitionModule`, RxJava subjects, and `com.uav.audiodatacollector.{AudioControlType,AudioControlTriggerType,AudioControlEndType}` state. Key internals:
  - `M0() → com.uav.flymodel.generated.api.flight.HandHeldFunctionMode` (**current mode is a HandHeld mode**).
  - Persisted flag string `"key_is_audio_recognition_enabled"`; foreground-service notification points at `com.uav.component.fpv.FpvComponentActivity`.
  - `subscribeRecognitionCommandObservable$1..4` — on `AudioRecognizedEvent`, calls parse then execute.
  - `S0(...)` logs Chinese debug: `状态码: <code> - 识别: <json>` ("status code / recognition"), `invalid_command`.
- `module.RecognitionModule` — loads the SDK by `base.Type`; log `语音sdk - load SDK type: … init dur:` ("voice sdk – load SDK type").
- `base.Type` enum (name, value): `InnovateSoundChinese=0`, `InnovateSoundEnglish=1` — i.e. only Chinese/English, chosen by language.
- `implementation.InnovateSoundAudioRecognizer` — the real recognizer. Wraps `com.ml.speech.vui.VoiceAppManager`; log tag `"InnovateAudioRecognizer"`, resource dir string `"ml_speech_model"`, save path `"voice_engine_audio"`. Implements NUI-style callbacks: `onWakeup`, `onManualWakeup`, `onVadStart`, `onVadEnd`, `onAsrResult(String)`, `onAsrCancel`, `onNluResult(String)` (extracts `"command"`), `onTimeout`, `onError(int,String)` ("Failed to initialize native voice engine", "no AI data resource, start download").
- `audiocollector.DataCollectorMetaDataProvider` — tags uploaded/collected audio with `KeyProductType`; paths `speechrecognition/`, `/ml_vui`; log tag `AudioControlDataCollector`. (Data-collection/telemetry path.)
- `interfaces.{RecognitionImplementation,Callback}`, `utils.AudioRecognitionUtilsKt`, `internal/audiorecognize/{BuildConfig,R$string}`.

### 2.3 Command parse/execute — `uav.sdk.audiocontrol.*` (DEX `classes_0451d00c`) — **NATIVE**
- `uav.sdk.audiocontrol.jni.AudioControlManager`:
  - `parseJson(String, AudioCommandParseCallback)` → **`native native_ParseJson(String, cb)`**
  - `executeAudioCommand(AudioCommandRecognitionResult, AudioCommandExecuteCallback)` → **`native native_ExecuteAudioCommand(byte[], cb)`**
  - So NLU-JSON → structured result → **DUML packet build+send all happen in native `.so`.** No cmd_set/cmd_id exists in Java.
- Callbacks `AudioCommandExecuteCallback`, `AudioCommandParseCallback`.

### 2.4 Flymodel gating — `com.uav.flymodel...flight.handheld.*` (DEX `classes_0855200c`/`08fe100c`)
- `api.flight.AudioControlModel`, `impl.flight.handheld.AudioControlModelImpl`, `HandHeldModelImpl$audioControl$2`.
- `handwrite.flight.handheld.v1.V1AudioControlGenKt.a()` builds a `FlyObservable<Boolean>` off KeyValue key **`KeyIsAudioControlSupport`** (`UAVStdFlightAssistantKey.c.o`), **default `Boolean.FALSE`**. This is the master "does this aircraft support voice control" gate. Note the package is **`...flight.handheld`** and it lives beside `IsHandHeldFunctionSupport` / `HandHeldFunctionMode`.

---

## 3. Third-party engine: **Alibaba IDST NUI** (CONFIRMED)

`all_classes.txt` contains the full Alibaba SDK: `com.alibaba.idst.nui.*` — `Constants$NuiEvent`, `Constants$NuiResultCode`, `Constants$ModeType`, `Constants$VadMode`, `Constants$WuwType` (wake-up-word), `Constants$NuiVprEvent`, `AsrResult`, `INativeFileTransCallback`, etc. This is **Alibaba Cloud Intelligent Speech Interaction ("NUI" / 智能语音交互)**, running on-device.

DJI wrapper: `com.ml.speech.vui.{VoiceAppManager, VoiceAppListener, Config, AsrOption}` (DEX `classes_061ed00c`).

Native libraries (`decompiled/lib/arm64-v8a/`):
| .so | role |
|---|---|
| `libneonuijni_public.so`, `libneonui_shared.so` | Alibaba NUI ("NeoNUI") native engine + JNI |
| `libvoice_engine_jni.so` | DJI `com.ml.speech.vui` voice-engine JNI |
| `libml_ns.so` | noise suppression |
| `libml_vc.so` | voice control / command |
| `libml_vot.so` | voice trigger (wake-word) |
| `libuavaudiorecord.so`, `libaudiodatacollector.so` | mic capture / data collector |

(`libaudio_highlight.so` is video-highlight audio — unrelated.)

---

## 4. DUML commands / HTTP endpoints

### 4.1 DUML
- **No voice-recognition DUML cmd_set/cmd_id is present in Java.** `full_table.txt`/`cmdmap.txt` contain only *unrelated* audio entries:
  - `0x02/0x9F (159)` `uav_camera_set_audio_param_req` (camera mic/audio params — recording, not voice control)
  - `0x02/0xA0 (160)` `uav_camera_get_audio_param_req`
  - `0x50/0x05 (5)` `uav_esdd_get_get_remote_audio_remux_state_req`
  These are **camera audio-recording params**, not voice commands. NOT part of this pipeline.
- The real command emission is **native** (`AudioControlManager.native_ExecuteAudioCommand`) and the support gate goes through the **KeyValue** key `IsAudioControlSupport`, whose DUML encoding is done by the native KeyValue codec — **statically undecidable here.** The key is defined on `UAVFlightAssistantSettingKey.o` = `UAVKeyInfoGL(component=FlightAssistant, subComponent, "IsAudioControlSupport", BooleanConverter)`; neighbours in the same class include `"IsHandHeldFunctionSupport"` (field `n`) and `"IsHandHeldCircleHeightFunctionSupported"` (field `p`).

**To capture the actual on-wire command (requires live device / Frida):**
- Hook `uav.sdk.audiocontrol.jni.AudioControlManager.native_ExecuteAudioCommand([B, cb)` — dump the `byte[]` (the parsed command it's about to fire) and the return `AudioControlReturnCode`.
- Hook `AudioControlManager.native_ParseJson(String, cb)` — dump the raw NLU JSON.
- Hook `fly.audiorecognize.implementation.InnovateSoundAudioRecognizer.onNluResult(String)` / `onAsrResult(String)` — dump ASR/NLU text.
- Trace DUML on the AOA/serial link while firing a command to recover cmd_set/cmd_id, or hook the native DUML send inside `libvoice_engine_jni.so` / the KeyValue codec (`libdcl_jni.so`).

### 4.2 HTTP
- No hard-coded voice URL in the recognition classes. Model resources are fetched via `IRecognitionResourceService` (`isDownloading()`, `e():String` path, "no AI data resource, start download") whose endpoint is supplied by config/DI (Koin `KoinDefUavPublicconfigAudioRecognizerConfig`, `KoinDefComDjiComponentApplicationConfigGetAudioRecognizerConfig`). `AudioRecognizerConfig` carries only `resIDAppLogo:int`. Model bundle keyed by `SPEECH_FUNCTION_RES_ID = 87533dd19780279c2aed50f10c15936e`, stored under `speechrecognition/ml_vui`. Actual download host resolves at runtime — capture with Frida on `IRecognitionResourceService.e()` or via network trace.

---

## 5. Enums with wire values (from smali `<clinit>`)

### 5.1 `AudioCommandType` — ctor `(name, ordinal, value)`; **value** = on-wire
| name | value | name | value |
|---|---|---|---|
| WAKEUP | 0 | STOPRECORD | 0x11 (17) |
| EXECUTE | 1 | GIMBAL_HORIZONTAL | 0x12 (18) |
| TRACKING | 2 | GIMBAL_VERTICAL | 0x13 (19) |
| SPOTLIGHT | 3 | TAKE_OFF | 0x14 (20) |
| QS_SLASH | 4 | SET_FCM_MODE | 0x15 (21) |
| QS_CIRCLE | 5 | SKI_FOLLOW | 0x16 (22) |
| QS_ROCKET | 6 | BIKE_FOLLOW | 0x17 (23) |
| QS_COMET | 7 | SMART_PORTRAIT | 0x18 (24) |
| QS_HELIX | 8 | DOLLY_ZOOM | 0x19 (25) |
| CUSTOM_MODE | 9 | MASTER_SHOT | 0x1a (26) |
| HEIGHT_ADJUSTMENT | 0xa (10) | PAUSE | 0x1c (28) |
| DISTANCE_ADJUSTMENT | 0xb (11) | RESUME | 0x1d (29) |
| STOP | 0xc (12) | DIRECTION_ADJUSTMENT | 0x2711 (10001) |
| GOHOME | 0xd (13) | FOLLOW_SPAN_PARMA_ADJUSTMENT | 0x2712 (10002) |
| LANDING | 0xe (14) | UNKNOWN | 0xffff (65535) |
| TAKEPHOTO | 0xf (15) | | |
| STARTRECORD | 0x10 (16) | | |

Note every entry is either a QuickShot (QS_*), ActiveTrack/tracking, Spotlight, follow (SKI/BIKE/DIRECTION/SPAN), MasterShot, DollyZoom, or SmartPortrait — **intelligent modes WM160 lacks entirely**, plus generic flight verbs (TAKE_OFF/LANDING/GOHOME).

### 5.2 `AudioControlReturnCode` — value = ordinal (except UNKNOWN)
0 NO_ERROR · 1 DEVICE_NOT_SUPPORT · 2 DEVICE_NOT_AVAILABLE · 3 COMMAND_INVALID · 4 ONLY_WAKEUP · 5 COMMAND_SEND_FAILED · 6 PARAM_ADJUSTMENT_LIMIT_REACHED · 7 INVALID_FOLLOW_MISSION_PARAM · 8 SHOOT_NUM_NOT_SUPPORT · 9 COMMAND_EXECUTING · 0xa NOT_SHOOTING · 0xb COMMAND_NOT_SUPPORT · 0xc AIRCRAFT_NOT_SUPPORTED_COMMAND · 0xd MASTER_SHOT_DISTANCE_NOT_SUPPORT · 0xe RTH_NOT_AVAILABLE · 0xf AIRCRAFT_HAS_TAKEN_OFF · **UNKNOWN = 0xffff**. (`DEVICE_NOT_SUPPORT`/`AIRCRAFT_NOT_SUPPORTED_COMMAND` are exactly what WM160 would return.)

### 5.3 `CameraVoiceControlLanguage`
CHINESE=0, ENGLISH=1, UNKNOWN=2. (matches `base.Type` Chinese/English only.)

### 5.4 Parameter value enums (ctor `(name, ordinal, value)`, value = ordinal+1)
- `AudioControlFollowDirectionValue`: FRONT=1, BACK=2, LEFT=3, RIGHT=4, FRONT_LEFT=5, FRONT_RIGHT=6, BACK_LEFT=7, BACK_RIGHT=8, UNKNOWN=9
- `AudioControlCameraActionValue`: RECORD=1, PHOTO=2, UNKNOWN=3
- `AudioControlSlashActionValue`: PARALLEL=1, RISE=2, UNKNOWN=3
- `AudioControlSmartPortraitShotModeValue`: SINGLE_PHOTO=1, BURST_PHOTO=2, UNKNOWN=3
- `AudioControlUnitValue`: METER=1, FEET=2, KM=3, UNKNOWN=4
- (also present, same family, not dumped here: `AudioControlCircleHeightValue`, `AudioControlDistanceAdjustmentValue`, `AudioControlFollowDistanceValue`, `AudioControlFollowHeightValue`, `AudioControlHeightAdjustmentValue`, `AudioControlRocketSpinValue`)

### 5.5 `AudioCommandRecognitionResult` (the parsed command struct, Parcelable)
Fields: `command:AudioCommandType`, `numberValue:Integer`, `cameraActionValue`, `circleHeightValue`, `distanceAdjustmentValue`, `followDirectionValue`, `followDistanceValue`, `followHeightValue`, `heightAdjustmentValue`, `rocketSpinValue`, `slashActionValue`, `smartPortraitShotModeValue`, `smartPortraitShotNum:Integer`, `smartPortraitTemplateValue:List`, `unitValue`. This is the object handed to `native_ExecuteAudioCommand`.

---

## 6. WM160 verdict

**NOT-WM160 / not available on Mavic Mini 1.** Reasons, in order of strength:
1. **Dynamic capability gate** `KeyIsAudioControlSupport` (`UAVFlightAssistantSettingKey.o`, "IsAudioControlSupport") defaults `FALSE` and is only true if the connected aircraft reports it. WM160 (a 2019 Mini 1) firmware does not implement audio control; it would fall through to `DEVICE_NOT_SUPPORT` / `AIRCRAFT_NOT_SUPPORTED_COMMAND`.
2. **Handheld feature family.** All gating/model code sits in `com.uav.flymodel...flight.handheld`, uses `HandHeldFunctionMode` / `IsHandHeldFunctionSupport`, and the UI layouts are `handheld_fpv_*`. This is oriented at voice-capable handheld/near-hand devices, not the WM160 map-based flight UI.
3. **Every emittable command is a mode WM160 lacks** — QuickShots, ActiveTrack/TRACKING, Spotlight, SKI/BIKE follow, MasterShot, DollyZoom, SmartPortrait. Mini 1 has none of these.
4. No `WM160`/`UAV59`/`Mini` reference anywhere in `fly.audiorecognize.*` or `uav.component.audiorecognition.*`.

The pipeline is present in the shared app binary because it's a multi-model app; it simply never activates for WM160.

---

## 7. Needs a live capture / Frida (statically undecidable)
- **Exact DUML** cmd_set/cmd_id + payload for each `AudioCommandType`: built natively — hook `AudioControlManager.native_ExecuteAudioCommand([B,cb)` and/or DUML-trace the link; KeyValue `IsAudioControlSupport` DUML encoding lives in the native codec (`libdcl_jni.so`).
- **Confirm the gate returns false on WM160**: hook `com.uav.flymodel...V1AudioControlGenKt.a()` result or read KeyValue `KeyIsAudioControlSupport` with WM160 connected.
- **Model-download host**: hook `IRecognitionResourceService.e()` / net-trace (resource id `87533dd19780279c2aed50f10c15936e`).
- **Raw ASR/NLU**: hook `InnovateSoundAudioRecognizer.onAsrResult/onNluResult`, and Alibaba `com.alibaba.idst.nui` NUI callbacks.
