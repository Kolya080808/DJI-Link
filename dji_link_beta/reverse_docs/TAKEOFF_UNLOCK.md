# TAKEOFF_UNLOCK — how DJI Fly unlocks / enables takeoff on the Mavic Mini 1 (WM160)

Evidence-based reverse of DJI Fly `dji.go.v5` v1.21.4. Goal: let the PC take off under any state
the app itself can reach. Every claim cites a class / smali / enum / table. Numeric values are read
out of the disassembly (baksmali), never guessed.

Scope note: the **DarkNoGps / no-GPS unlock** and **camera exposure** are covered by a separate
agent and are only cross-referenced here. This file covers the general takeoff-gating framework and
everything else.

## Provenance of the citations
- The P3 DUML builders (`uav/midware/data/model/P3/Data*`) were disassembled from
  `reverse_docs/unpacked_app_dex/classes_0451d00c.dex`; the cmd-id enums
  (`uav/midware/data/config/P3/CmdId*`) from `classes_016b200c.dex`; the app-side diagnostic /
  takeoff enums from the same two DEX. All values below were parsed from those `.smali`.
- `CmdSet` byte (arg of `Pack.m`): `a=0x00 COMMON, b=0x01 SPECIAL, c=0x02 CAMERA, d=0x03 FLYC,
  e=0x04 GIMBAL …` (`CmdSet.smali`, per `FLIGHT_GATING.md`).
- `CmdIdFlyc$CmdIdType` letters resolved this session (constructor `<init>(String,int ordinal,
  int value)`, `value()` = 3rd arg): `p=SetFunc=0x2A, s=SetLimits=0x2D, t=GetLimits=0x2E,
  w=SetHomePoint=0x31, o=ExecFly=0x27, e=SetFlyForbidArea=0x08, H=UploadUnlimitAreas=0x41,
  I=EnableUnlimitAreas=0x47, U=SetActiveResult=0x62, v2=JoyStick=0x8E, F5=GetSetWarningAreaEnable=0xCC,
  H5=UpdateFlyforbidArea=0xCD, F8=GetNewFlyforbidArea=0xCF, aa=SetFlyforbidData=0xE9,
  ae=SetMotorForceDisable=0xFE, fd=SetParamsByHash=0xF9, ad=GetParamsByHash=0xF8`.
- `CmdIdCommon$CmdIdType.t = 0x32` (constructor `<init>(String,int,int,Z,Z,Class)`, value=3rd arg).
- `CmdIdSpecial$CmdIdType.b = JoySitckSetParams = 0x02`.
- All builders: sender = APP (0x0a); takeoff/limit/active builders set receiver = FLYC (0x03),
  `CMDTYPE.a` / `NEEDACK.a` = req-with-ack (0x40).

---

## 1. The complete motor-start-refusal / "cannot take off" reason list

### Two layers, one wire value
The FC reports why it will not spin the motors as a **single byte at OSD offset +0x33** (the OSD
push, cmd_set 0x03). The app parses that byte with the enum
**`DataOsdGetPushCommon$MotorStartFailedCause`** (`classes_0451d00c.dex`), whose constructor carries
an **explicit integer value** for every constant. That gives the authoritative, gap-preserving
value→cause map below — it supersedes the partial `diag_codes.py MOTOR_FAIL_NAME` (which agrees on
every value it has). Separately, the app's key/value SDK re-expresses these as the
`FC_CANNOT_TAKE_OFF_*` constants of the enum **`uav/sdk/keyvalue/value/diagnostic/DiagnosticCode`**
(the 96 `FC_CANNOT_TAKE_OFF_*` names verified present there); those feed the on-device
DiagnosticCode→text tables in `diag_codes_full.py` (see `ERROR_CODES.md`).

**Runtime rule for the PC:** read OSD byte +0x33, look it up in the table below. `0 = None` means
the FC is willing to arm. `diag_codes.py MOTOR_NOT_START[value]` gives the paired DiagnosticCode
(30xxx) for a human string.

### Authoritative +0x33 value → cause (from `DataOsdGetPushCommon$MotorStartFailedCause.smali`)

Clearability legend:
- **CMD** — clearable by a DUML command the app can send (given below).
- **WAIT** — transient; clears itself once the FC finishes (calibration/warmup/init) — poll +0x33.
- **PHYS** — physical / hardware / battery / firmware — the app cannot clear it in software either
  (recalibrate, charge, re-seat battery, reboot, or repair). Listed for completeness.

```
val name                         class   how the app clears it (or why it can't)
0   None                         —       FC ready to arm
1   CompassError                 PHYS    move away from metal; if persists → recalibrate (§2 cali)
2   AssistantProtected           CMD     a PC/Assistant is connected & holding the FC; disconnect it /
                                         release ground-station+control-auth (§6). Self-inflicted.
3   DeviceLocked                 PHYS    aircraft bound/locked (anti-theft); needs owner-account unlock
4   DistanceLimit                CMD     raise radius: 0x03/0x2D Far, or radius_limit_enabled_0=0 (§3)
5   IMUNeedCalibration           CMD/WAIT IMU cali: 0x03/2A/09 then wait (§2)
6   IMUSNError                   PHYS    IMU serial mismatch — firmware/hardware
7   IMUWarning (preheating)      WAIT    IMU warming up — poll until it clears
8   CompassCalibrating           WAIT    finish/await compass cali
9   AttiError (no attitude)      WAIT    keep still on level ground until attitude converges
10  NoviceProtected              CMD     novice mode forces GPS; novice_func_enabled_0=0 (§3)
11  BatteryCellError             PHYS    battery
12  BatteryCommuniteError        PHYS    re-seat battery
13  SeriouLowVoltage             PHYS    charge
14  SeriouLowPower               PHYS    charge
15  LowVoltage                   PHYS    charge
16  TempureVolLow                PHYS    warm the battery
17  SmartLowToLand               PHYS    charge
18  BatteryNotReady              WAIT    wait for battery handshake
19  SimulatorMode                CMD     exit simulator (cmd_set 0x0B SIMULATOR stop)
20  PackMode                     CMD     unfold / 0x03/2A UnPackMode(0x17) — N/A on Mini (non-folding)
21  AttitudeAbNormal             WAIT    place level, keep still
22  UnActive                     CMD     one-time activation: 0x00/0x32 + 0x03/0x62 (§4)
23  FlyForbiddenError            CMD     NFZ; unlock / move — 0x03/0x47 + 0x03/0x41 (§5) or relocate
24  BiasError                    CMD/WAIT recalibrate IMU (§2)
25  EscError                     PHYS    ESC/motor hardware
26  ImuInitError                 WAIT    reboot/keep still while IMU inits
27  SystemUpgrade                WAIT    firmware upgrading — wait
28  SimulatorStarted             CMD     stop simulator (0x0B)
29  ImuingError                  WAIT    IMU busy — wait
30  AttiAngleOver                PHYS    aircraft tilted > limit — place level
31  GyroscopeError               PHYS    gyro hardware
32  AcceletorError               PHYS    accelerometer hardware
33  CompassFailed                PHYS    compass hardware
34  BarometerError               PHYS    baro hardware
35  BarometerNegative            PHYS    baro hardware
36  CompassBig (mod too large)   PHYS    magnetic interference — move; else recalibrate
37  GyroscopeBiasBig             CMD/WAIT recalibrate IMU (§2)
38  AcceletorBiasBig             CMD/WAIT recalibrate IMU (§2)
39  CompassNoiseBig              PHYS    interference — move
40  BarometerNoiseBig            PHYS    wind/airflow — shield baro
41  InvalidSn                    PHYS    serial invalid — firmware
44  FLASH_OPERATING              WAIT    FC flash busy — wait
45  GPS_DISCONNECT               PHYS    GPS module — (Mini can still arm w/o GPS unless dark, see 147)
47  SDCardException              CMD     usually non-blocking; format/re-seat SD (0x02 camera)
61  IMUNoconnection              PHYS    IMU bus
62-66 RCCalibration*             CMD     RC calibrate: 0x06/0x03 (not needed for PC virtual-stick)
67  AircraftTypeMismatch         PHYS    firmware/model mismatch
68  FoundUnfinishedModule        WAIT    module boot — wait
70-75 *_ABNORMAL (gyro/baro/     PHYS    sensor/nav/topology hardware
      compass/gps/ns/topology)
76  RC_NEED_CALI                 CMD     0x06/0x03 (irrelevant for PC sticks)
77  INVALID_FLOAT                PHYS    firmware
78-82 M600_BAT_*                 PHYS    N/A on Mini (M600 battery codes)
83  INVALID_VERSION              WAIT    version mismatch — upgrade
84-92 GIMBAL_* (gyro/esc/shock/  PHYS/CMD gimbal cali 0x04/0x08 for calibratable ones; else hardware
      disorder/updating)
93  IMUcCalibrationFinished      WAIT    transient success marker
94  TAKEOFF_ROLLOVER             PHYS    flipped — right the aircraft
95  MOTOR_STUCK                  PHYS    clear prop obstruction
96  MOTOR_UNBALANCED             PHYS    prop/motor
97  MOTOR_LESS_PADDLE            PHYS    install propellers
98  MOTOR_START_ERROR            PHYS    ESC/motor
99  MOTOR_AUTO_TAKEOFF_FAIL      WAIT    retry AUTO_FLY
100 RollOverOnGround             PHYS    place upright
101 BatVersionError              PHYS    battery firmware
102-103 RTK_*                    PHYS    N/A on Mini
104 ESC_SHORT_CUT_ERROR          PHYS    ESC
105 POWER_SYSTEM_HARDWARE_ERROR  PHYS    power board
106 BAT_HW_VERSION_ERROR         PHYS    battery
107 BATTERY_IN_LOADER            WAIT    battery in bootloader — reboot
112 ESC_CALIBRATING              WAIT    ESC cali running
113 GPS_SIGN_INVALID             PHYS    GPS signature (anti-spoof) — move to open sky
114 GIMBAL_IS_CALIBRATING        WAIT    await gimbal cali
115 LOCK_BY_APP                  CMD     YOUR motor-force-disable; clear: 0x03/0xFE 00 (§6)
116 START_FLY_HEIGHT_ERROR       CMD     height-limit block; raise via 0x03/0x2D High (§3)
117 ESC_VERSION_NOT_MATCH        PHYS    ESC firmware
118 IMU_ORI_NOT_MATCH            CMD     IMU orientation — recalibrate (§2)
119 STOP_BY_APP                  CMD     app stop-motor latch; send START_MOTOR / AUTO_FLY again (§6)
120 COMPASS_IMU_ORI_NOT_MATCH    CMD     recalibrate (§2)
122 ESC_ECHOING                  WAIT    ESC self-test
123 ESC_OVER_HEAT                PHYS    let cool
124 BATTERY_INSTALL_ERROR        PHYS    re-seat battery
125 BE_IMPACT                    WAIT    impact detected — power-cycle
126 MODE_FAILUER                 PHYS    firmware
127 CRASH                        PHYS    reboot aircraft
128 HEIGHT_CONTROL_ANOMALY       PHYS    baro/height sensor
129 LOW_VERSION_OF_BATTERY       PHYS    battery firmware
130 VOLTAGE_OF_BATTERY_TOO_HIGH  PHYS    battery
131 BATTERY_EMBED_ERROR          PHYS    battery
132 COOLING_FAN_EXCEPTION        PHYS    N/A on Mini
133 EAGEL_TEMPERATURE_ERROR      PHYS    vision chip temp
134 LOST_GPS_IN_POR_A_ERROR      WAIT    regained-GPS transient
136 RC_THROTTLE_IS_NOT_IN_MIDDLE CMD     center the throttle stick (send neutral sticks, §6)
138 FLIGHT_BSP_ERROR             PHYS    firmware
139 FLIGHT_RESTRICTION_STRATEGY  CMD     restriction-strategy (UOM/geo policy); relocate / see §5
146 BATTERY_ICO_ERROR            PHYS    battery gauge
147 DARK_NEED_GPS                CMD     low-light-needs-GPS; DarkNoGpsLockEnable (SEPARATE AGENT)
162 FIRST_TAKE_OFF_WARNING       CMD     first-flight yellow warning; user confirms → re-send AUTO_FLY (§7)
OTHER (0x7fffffff)               —       any value not in the table
```

### Bottom line for #1
The only refusals a PC ground-station can actually *clear with a command* are the self-inflicted /
policy / limit ones: **2, 4, 10, 15/16/17-charge, 19/28 (sim), 22 (activate), 23 (NFZ), 115/119
(app-lock), 116 (height), 136 (throttle), 147 (dark-GPS), 162 (first-warning)**, plus the
recalibration-fixable IMU/compass ones (5, 24, 37, 38, 118, 120) via the cali command. Everything
tagged PHYS is a hardware/battery/firmware condition the **app cannot bypass either** — it is not a
software gate, so there is nothing to "unlock". WAIT items resolve by polling +0x33.

---

## 2. Pre-flight checklist the app runs before enabling takeoff

### The sensor-check push: `DataFlycGetPushCheckStatus` (`classes_0451d00c.dex`)
An FC push (21-byte payload; `setPushRecPack` asserts `length == 0x15`) carrying one 32-bit status
bitfield read at offset 0 (`DataBase.get(0,4,Integer)`). Each accessor is `(field >> bit) & 1`:

```
bit 0  getIMUAdvanceCaliStatus       bit 8  getGyroscopeStatus
bit 1  getIMUBasicCaliStatus         bit 9  getBarometerDataStatus
bit 2  getIMUHorizontalCaliStatus    bit 10 getAircraftAttiStatus
bit 3  getVersionStatus              bit 11 getIMUDataStatus
bit 4  getIMUDirectionStatus         bit 12 getDataLoggerStatus
bit 5  getIMUInitStatus              bit 13 getLastIMUAdvanceCaliStatus (FlycVer>=5)
bit 6  getBarometerInitStatus        bit 14 getLastIMUBasicCaliStatus  (FlycVer>=5)
bit 7  getAccDataStatus              bit 15 isReadingData              (FlycVer>=16)
                                     bit 24 isKernelBoardHighTemperature
                                     bit 30 isBatteryInstalledError (senderId==0x801)
```
`isOK()` returns true when any of the cali/version/direction/init/baro/acc/gyro/atti/imu/logger bits
is set — i.e. a "check flagged" aggregate. Several accessors gate on `DataOsdGetPushCommon.getFlycVersion()`
(the field only exists on newer FC firmware), so read the FC version first.

### What each pre-flight requirement reports and what clears a "required" state
| requirement | reported by (push) | clear a "required"/"in-progress" state |
|---|---|---|
| IMU basic/horizontal/direction/init cali | `DataFlycGetPushCheckStatus` bits 0-5; also +0x33 val 5/24/37/38/118/120 | run IMU cali `0x03/2A/09` (`FLYC_COMMAND.Calibration=0x09`), then poll until +0x33=0 |
| Compass cali | +0x33 val 1/8/36/39; keys `Flight.FlightSensor.Compass.Start/StopCompassCalibration` | `0x03/2A/09` starts the FC cali routine; move away from metal |
| Barometer init/data | check-status bits 6/9; +0x33 val 34/35/40/128 | keep still, shield from wind; PHYS if error |
| Gyro / accel data | check-status bits 7/8; +0x33 val 31/32/37/38 | recalibrate (IMU cali); PHYS if dead |
| Aircraft attitude ready | check-status bit 10; +0x33 val 9/21/30 | place level, keep still (WAIT) |
| FC/version status | check-status bit 3; +0x33 val 83 | firmware upgrade |
| Install / orientation error | `DataFlycGetPushFlycInstallError`; +0x33 val 118/120 | recalibrate IMU |
| Mass-center cali (if used) | `DataFlycGetPushMassCenterCaliStatus`; `FLYC_COMMAND.MASS_CENTER_CALI=0x36 / EXIT=0x37` | N/A on Mini |
| ESC status | +0x33 val 25/104/112/117/122/123 | `ESC_CALIBRATING(112)` self-clears; others PHYS |
| Gimbal cali | +0x33 val 84-92/114; `DataGimbalAutoCalibration 0x04/0x08` | gimbal auto-cali `0x04/0x08`, await `DataGimbalGetPushAutoCalibrationStatus` |
| Battery gate | check-status bit 30; +0x33 val 11-18,101,106,107,124,131,146; battery push `0x0D/02` | charge / re-seat — PHYS |
| Aircraft connected | OSD push present at all (cmd_set 0x03) + `DataOsdGetPushCommon` | ensure link (AOA/serial) up |
| Camera ready | camera push `0x02` present; `DataCameraGetPushStateInfo` | not a hard takeoff gate on Mini |
| Height-limit reason | `DataOsdGetPushHome$HeightLimitStatus`: `NON_LIMIT / NON_GPS / ORIENTATION_NEED_CALI / ORIENTATION_GO / AVOID_GROUND / NORMAL_LIMIT / LIMIT_BY_NFZ / LIMIT_BY_NOVICE_MODE` | tells you *why* a height cap is active (NFZ vs novice vs no-GPS) |

The app does **not** gate motor-start on the camera or on most of these itself; the FC does, and it
reports through +0x33. A PC that reads +0x33 and the check-status push has the same information the
app's checklist UI has.

---

## 3. Novice / Beginner mode and beginner caps

**There is no dedicated "novice mode" DUML command.** Novice/Beginner is purely FC parameters,
written by the hashed param write `0x03/0xF9` (`DataFlycSetParams`, `CmdIdFlyc.fd`). Evidence:
- App key path `Flight.FlightControl.FlightGuide.NoviceModeEnabled` / keys `KeyNoviceModeEnabled`,
  `KeyIsNoviceModeOn` → `FlightGuideModelImpl$noviceModeEnabled` (strings, all DEX).
- The backing FC param is **`g_config.novice_cfg.novice_func_enabled_0`** (string present in DEX and
  in `flyc_param_infos.json`).

Params (from `flyc_param_infos.json`, `index` = FC param index, exact values):
```
novice_func_enabled_0   index 343  size1  min0  max1   def0    ← 1 = novice ON (forces GPS, caps)
novice_cfg.max_height_0 index 344  f32    min1  max100 def30   ← beginner height cap (m)
novice_cfg.max_radius_0 index 345  f32    min5  max100 def30   ← beginner radius cap (m)
novice_cfg.atti_range_0 index 346 / vert_up_vel_0 354 / vert_down_vel_0 355  (beginner speed caps)
```
**Turn beginner mode OFF:** write `novice_func_enabled_0 = 0` via `0x03/0xF9` (needs the name→hash,
see below). While it is on, +0x33 reports `NoviceProtected` (val 10) if you try to arm without GPS,
and `DataOsdGetPushHome$HeightLimitStatus = LIMIT_BY_NOVICE_MODE`.

**Hash blocker (same as speed/gains):** `0x03/0xF9` addresses params by a 4-byte name-hash generated
behind the app packer — not derivable statically (`MASTER_REPORT.md §7`). Get it once via Frida on
`DataFlycSetParams.start()` while toggling Beginner mode, or dump the FC config
(`DataCommonGetCfgFile 0x00/0x4F`, `GetParamInfoByHash 0x03/0xF7`). The beginner **height/radius
caps** themselves can also be lifted the non-hash way — raise the general limits with `0x03/0x2D`
(§ below) — but the `novice_func_enabled_0` flag has no non-hash setter.

### Beginner caps vs the general max-height/distance (non-hash path)
`DataFlycSetLimits` → **`0x03/0x2D`** (`CmdIdFlyc.s`), receiver FLYC, 3-byte payload confirmed
(`doPack`): `[0]=mode u8` (`DataFlycGetLimits$MODE`: High=1, Far=2, Low=3, OTHER=0x64), `[1..2]=value
u16 LE metres`, **no hash**.
```
0x03/0x2D  01 <u16 LE>   set max flight height (m). FC clamps to flying_limit.max_height 15..500 (idx236 def120)
0x03/0x2D  02 <u16 LE>   set max radius/distance (m). FC clamps to max_radius 15..5000 (idx235 def30)
0x03/0x2D  03 <u16 LE>   set min height
```
Read back with `DataFlycGetLimits 0x03/0x2E` (`CmdIdFlyc.t`), payload `[mode]`. Related enable flags
(hash write): `advanced_function.height_limit_enabled_0` (idx205, min1 max2 def1 — **cannot be 0**, a
height limit is always on) and `advanced_function.radius_limit_enabled_0` (idx207, def0 = no distance
limit). So the ceiling can be raised to 500 m and the radius unlimited without the hash; only the
*floor value* of the always-on height limit remains.

---

## 4. Activation gate

### How the app detects "not activated"
- FC pushes an activation request: **`DataFlycGetPushActiveRequest`** (FC → app "please activate").
- +0x33 reports `UnActive` (val 22) if you try to arm un-activated; `DiagnosticCode`
  `FC_CANNOT_TAKE_OFF_DRONE_NOT_ACTIVATED` is the paired app enum. App-side soft gate:
  `ActivationModelImpl.needActivateDevices`, `showNotActivateRemindDlg`,
  `FpvActivateGate$listenFcNotTakeOff` (strings). A PC ground-station simply doesn't run the soft gate.

### The exact activation DUML
1. **Query/activate on COMMON:** `DataFlycActiveStatus` → **`0x00/0x32`** (`CmdSet.a` COMMON /
   `CmdIdCommon.t` = 0x32 = `uav_general_activate_device`, `full_table.txt`). This is the
   cloud-activation exchange (needs a logged-in DJI account + internet — the ONE account-gated event).
2. **Report the result back to the FC:** `DataFlycSetActiveResult` → **`0x03/0x62`** (`CmdSet.d` FLYC
   / `CmdIdFlyc.U` = 0x62), receiver FLYC. **44-byte (0x2C) payload confirmed** (`doPack`):
   ```
   [0..3]   activationState  u32 LE  (UAVActivationState.value)
   [4..7]   appId            u32 LE
   [8..11]  appLevel         u32 LE
   [12..43] appCommKey       32-byte string (BytesUtil.r0, truncated/zero-padded to 32)
   ```
   `UAVActivationState` (`DataFlycSetActiveResult$UAVActivationState.smali`): `Success=0, NoNetwork=1,
   InvalidId=2, FailedForNet=3` (+ higher OTHER).

### Confirmation for an already-activated unit
Activation **persists on the aircraft**. `getIsActivated`/`isActivated` read FC state; the app does
**not** re-authenticate per flight and sends no per-flight "login". So: **an already-activated WM160
needs nothing for takeoff** — no account, no internet. Only a factory-fresh unit needs the one-time
`0x00/0x32` + `0x03/0x62` with a real account online. (Login is otherwise only needed for GEO/NFZ
licence unlock, §5, which a sub-250g WM160 mostly does not require.)

---

## 5. Geo / NFZ takeoff blocks for a sub-250g WM160

### What the app checks (all FC pushes, cmd_set 0x03)
- **`DataFlycGetPushForbidStatus`** — the live zone state. Enums:
  - `NewFlyfrbState`: `OUTSIDE_LIMIT, LOCATION_UNKNOWN, SEEM_IN_LIMIT, PHONE_IN_LIMIT, UAV_IN_LIMIT,
    SEEM_IN_LIMIT_HEIGHT, PHONE_IN_LIMIT_HEIGHT, UAV_IN_LIMIT_HEIGHT`.
  - `UAVFlightLimitAreaState`: `None, NearLimit, InHalfLimit, InSlowDownArea, InnerLimit,
    InnerUnLimit, OTHER` (the LimitAreaLevel graduation).
  - `UAVFlightLimitActionEvent`: `None, ExitLanding, Collision, StartLanding, StopMotor, OTHER`
    — **`StopMotor` / `StartLanding` are the actions that actually stop a takeoff**; `None`/`NearLimit`
    are warn-only.
  - `GohomeFrbAreaState` — NFZ-around-home state.
- `DataFlycGetPushLimitState` (`UAVLimitsAreaStatus`), `DataFlycGetPushNewUnlimitState`,
  `DataFlycGetPushRequestLimitUpdate`, `DataFlycPushForbidDataInfos` — zone geometry / white-list state.
- `DataOsdGetPushHome$HeightLimitStatus = LIMIT_BY_NFZ` marks an NFZ-imposed height cap.
- +0x33 reports `FlyForbiddenError` (val 23) when the FC refuses to arm inside a restricted zone;
  `FC_CANNOT_TAKE_OFF_IN_FLY_LIMIT_ZONE` is the paired app enum.

### Is takeoff actually blocked, or only warned?
Depends on the zone class the FC assigns, surfaced via the enums above:
- **Warning / Enhanced-Warning / Authorization zones** → the app raises the yellow "confirm takeoff"
  dialog (`fpv_nfz_before_takeoff_dialogue_confirm_btn`) and, on confirm, **still sends the normal
  AUTO_FLY** — `UAVFlightLimitActionEvent` stays `None`, +0x33 does not latch `FlyForbiddenError`.
  These are advisory. Most GEO zones for a sub-250g aircraft fall here.
- **Restricted zones** (airports etc.) → `UAVFlightLimitActionEvent = StopMotor/StartLanding`, +0x33
  = `FlyForbiddenError (23)`, and the FC genuinely refuses. This is a firmware/GEO-DB block.

### Local override surface (what the app sends to unlock)
- **`DataFlycEnableUnlimitAreas` → `0x03/0x47`** (`CmdIdFlyc.I`), receiver FLYC, **1-byte payload
  `[enable u8]`** (`doPack` confirmed: single bool). This flips the FC into "honour the app's
  unlock/white-list areas".
- **`DataFlycUploadUnlimitAreas` → `0x03/0x41`** (`CmdIdFlyc.H`) — uploads the unlock white-list
  (circle/polygon; `DataFlycGetPushNewUnlimitState$WhiteListPushCircle/Polygon`).
- **`DataFlycSetFlyforbidData` → `0x03/0xE9`** (`CmdIdFlyc.aa`), `DataFlycUpdateFlyforbidArea` 0x03/0xCD,
  `DataFlycGetNewFlyforbidArea` 0x03/0xCF, **`GetSetWarningAreaEnable` 0x03/0xCC** (`CmdIdFlyc.F5`) —
  push/refresh/toggle of the zone database & warning-area enable.
- NFZ-DB firmware upgrade path: `0x03/0xBB/0xBC/0xBD` (`full_table.txt`).

Real unlock of an authorization/restricted zone needs a **DJI-signed unlock licence** tied to the
drone SN and account (`FINDINGS.md §8`, `libFRCorkscrew`) uploaded via `0x03/0x41` then enabled via
`0x03/0x47`. The `0x03/0x47 enable=1` alone only tells the FC to honour already-authorised areas — it
does not self-authorise a restricted zone. For an advisory zone, no upload is needed: just confirm and
send AUTO_FLY. **Simplest practical answer for a sub-250g WM160: take off from a non-restricted spot;
advisory zones are pass-through.**

---

## 6. The "start motors" / arm path in full

### There is no CSC (combination-stick-command) DUML
A string search for `CSC` / `combinationStick` / `armMotor` across all 16 DEX returns nothing.
Classic CSC (both sticks to the bottom-inner corners) is an **RC-level physical gesture** the FC
decodes from raw stick channel values — it is not a distinct command. On the PC path you either:
- send the **function-control arm/takeoff** (what the app uses), or
- emulate CSC by driving the four stick channels to the corner values over the stick channel
  (`0x01/0x0A` special-TLV or `0x01/0x02` mobile-RC joystick) — same encoding as normal sticks.

### Function-control = the app's arm/takeoff command
`DataFlycFunctionControl` → **`0x03/0x2A`** (`CmdSet.d` FLYC / `CmdIdFlyc.p` = 0x2A), receiver FLYC,
**1-byte payload = `FLYC_COMMAND.value()`** (2 bytes only for `OAR_PANEL_CALI`; `doPack` confirmed).
`FLYC_COMMAND` values verified from `DataFlycFunctionControl$FLYC_COMMAND.smali` (constructor
`<init>(String,ordinal,value)`, value=3rd arg):
```
AUTO_FLY=0x01  (takeoff: arm + auto-lift — what the app's "takeoff" button sends)
AUTO_LANDING=0x02  HOMEPOINT_NOW=0x03  HOMEPOINT_HOT=0x04  HOMEPOINT_LOC=0x05  GOHOME=0x06
START_MOTOR=0x07 (arm only, no lift)   STOP_MOTOR=0x08 (disarm)   Calibration=0x09
… DropTakeOff(cancel takeoff)=0x0D  DropLanding=0x0E  ForceLanding=0x1E  PRECISION_TAKE_OFF=0x22
```
So: **arm = `55 … 03 2A 07`**, **auto-takeoff = `55 … 03 2A 01`**, **disarm = `03 2A 08`**.

### Prerequisites (control authority / ground-station)
- **`0x49/0x80`** `uav_sdk_get_or_release_control_auth` (`full_table.txt`), 1-byte `01`=take / `00`=release.
  Take control before arming.
- **`0x03/0x80`** `uav_fc_set_ground_station_on_off` (`full_table.txt`), 1-byte on/off — puts the FC in
  "listen to external control". The MSDK `setVirtualStickModeEnabled(true)` maps here.
- Arbitration if the RC keeps priority: `0x06/0xF1` RC→PC, `0x19/0x40/0x41/0x46` lock/preempt/task-occupy.
- **Joystick-mode params:** `DataFlycSetJoyStickParams` → **`0x01/0x02`** (`CmdSet.b` SPECIAL /
  `CmdIdSpecial.b`=0x02 = `uav_action_virtual_rc_joystick`), receiver OFDM; carries a `FlycMode`
  (`A=0` ATTI, `P=1` GPS/positioning, `F=2` function). This is the mobile-RC joystick channel that
  also selects the flight mode.
- Stick streaming (5–25 Hz): `0x01/0x0A` special-TLV, `0x01/0x02` mobile-RC, or `0x03/0x8E`
  `DataFlycJoystick` (`CmdIdFlyc.v2`, 17-byte float `[flag][roll,pitch,yaw,throttle f32 LE]`). Which
  one WM160 accepts is the #1 HW-open item (see `FLIGHT_GATING.md §H`).
- **Emergency disarm / self-lock:** `DataFlycSetMotorForceDisable` → **`0x03/0xFE`** (`CmdIdFlyc.ae`),
  **1-byte payload `[isDisable u8]`** (`doPack` confirmed — single byte, not 2). `01` locks motors
  (+0x33 then reads `LOCK_BY_APP`=115), `00` releases. Note: `drone.py motor_force_disable` currently
  sends a 2-byte `[flag,mode]` — the app builder sends **1 byte**; align it.
- Auto-flight/mission execution (peripheral): `DataFlycExecFly` → `0x03/0x27` (`CmdIdFlyc.o`), 4-byte
  u32 `TYPE` (`PAUSE_FLY, RESUME_FLY, AUTO_LANDING, ENTER_SINGAL, OUT_SINGAL, START_FLY, START_TURN`).
  Not the normal takeoff path.

### Recommended PC arm sequence (props off first)
```
1. 0x49/0x80 01                 take control auth              (ack)
   (if ignored) 0x06/F1 01, 0x19/41 01
2. 0x03/0x80 01                 ground-station / external-control ON  (ack)
3. read OSD +0x33 → resolve via §1 table; clear whatever is CMD-clearable
4. 0x03/2A 07   START_MOTOR (arm)      — or 0x03/2A 01 AUTO_FLY (arm+lift)
5. stream sticks 5–25 Hz
6. panic: 0x03/2A 02 land | 0x03/2A 06 GOHOME | 0x03/0xFE 01 force-disable
7. 0x49/0x80 00                 release control
```

---

## 7. App-side "confirm takeoff" dialogs and what they send

These are UI gates. Crucially, **none of them sends a distinct "take off anyway" DUML** — after the
user taps confirm, the app sends the **same `0x03/2A/01 AUTO_FLY`**. The "unlock" ones additionally
flip an FC setting *before* the normal takeoff:

| dialog (strings) | class / key | what confirm actually does |
|---|---|---|
| General takeoff confirm | `TakeOffAndLandConfirmDialogView`, `gotoTakeOffConfirmDialogView`, `need_go_to_take_off_confirm_dialog` (`com/uav/fpv/leftbar/component/takeoffandland/*`) | after confirm → `0x03/2A/01 AUTO_FLY`. No extra DUML. |
| First-flight yellow warning | +0x33 `FIRST_TAKE_OFF_WARNING`(162) / `FC_CANNOT_TAKE_OFF_FIRST_WARNING` | user acknowledges → re-send `0x03/2A/01`; the FC drops the FIRST_WARNING latch on the second request. |
| NFZ "before takeoff" | `fpv_nfz_before_takeoff_dialogue_confirm_btn` / `..._unlock_btn` | confirm (advisory zone) → AUTO_FLY; unlock (auth zone) → `0x03/0x41` upload + `0x03/0x47` enable, then AUTO_FLY (§5). |
| Dark / no-GPS unlock | `fpv_basic_flight_cannot_fly_dark_no_gps_unlock_btn/success/failed_toast`, key `Flight.FlyLimit.FlyLimitSettings.DarkNoGPSLockOn` | toggles `DarkNoGpsLockEnable` then AUTO_FLY — **SEPARATE AGENT**. |
| Moving-platform (boat/car) takeoff | `ConfirmInMovingPlatformTakeOff`, key `Flight.Takeoff.TakeoffSetting.ConfirmInMovingPlatformTakeOff` (`KeyConfirmInMovingPlatform`), `TakeoffSettingModelImpl$confirmInMovingPlatformTakeOff` | sets the FC "confirm in moving platform" takeoff flag (key-backed), then AUTO_FLY. |
| Landing confirm | `LandAndGoHomeVM$observableIsLandingConfirmationNeeded` | landing side; not a takeoff gate. |

**For a PC ground-station:** you skip every dialog — just clear the corresponding FC condition
(§1/§5/§6) and send `0x03/2A/01`. There is no hidden "confirm token" in the DUML; the confirmation is
purely local UI, except the two settings-writes (NFZ unlock upload/enable, DarkNoGps, moving-platform
flag) which precede the identical AUTO_FLY.

---

## What is undecidable statically (name the Frida hook)
1. **Param name→hash** for `novice_func_enabled_0`, speed/gain params, and any hashed FC setting —
   hook `uav/midware/data/model/P3/DataFlycSetParams->start(...)` (or the native DUML writer in
   `libsdk_jni`) while toggling the corresponding switch; or dump the FC config via
   `DataCommonGetCfgFile 0x00/0x4F` / `GetParamInfoByHash 0x03/0xF7`.
2. **`ConfirmInMovingPlatformTakeOff` / `DarkNoGpsLockEnable` exact DUML** — key-backed FC settings;
   hook `uav/midware/data/manager/P3/DataBase->start(...)` while toggling them in Safety/Takeoff
   settings to capture the cmd_id + payload.
3. **Which of `0x01/0x0A` / `0x01/0x02` / `0x03/0x8E` the WM160 FC accepts for sticks**, and whether
   `0x03/0x80`+`0x49/0x80` (and possibly `serial_api_cfg.advance_function_enable=1`) are hard
   preconditions — one Frida capture of `DataBase.start()` + the native send while the app flies with
   virtual sticks resolves it.
4. **Cmd-ids of the pure-push parsers** `DataFlycGetPushForbidStatus` / `GetPushLimitState` /
   `GetPushCheckStatus` — these classes carry no `start()`/CmdId; they are keyed in the receiver
   dispatch registry. The parse layouts are recovered (above); the exact push cmd-id is read live from
   the OSD/forbid push header or by hooking the dispatch map.

## Cross-file notes for the implementers (drone.py / diag_codes.py)
- `diag_codes.py MOTOR_FAIL_NAME` is a partial table; the complete authoritative value→cause map is in
  §1 (from `DataOsdGetPushCommon$MotorStartFailedCause`). Values/gaps match `MOTOR_NOT_START` exactly.
- `drone.py motor_force_disable` sends 2 payload bytes; the app builder `DataFlycSetMotorForceDisable`
  sends **1** byte. Change to a single `[1|0]`.
- `drone.py takeoff()` = `0x03/2A/01` (AUTO_FLY) and `start_motors()` = `0x03/2A/07` are correct.
- `set_max_altitude`/`set_max_distance` via `0x03/0x2D` are correct (3-byte `[mode][u16 LE]`).
