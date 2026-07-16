# DJI Fly error / diagnostic / status codes (WM160)

Static extraction from the unpacked **DJI Fly v1.21.4** (`dji.go.v5`). All tables ship in
`../diag_codes_full.py` (plain Python, compiles with `python3 -m py_compile`). This file
is the summary + provenance. Companion `../diag_codes.py` (unchanged) holds the native-lib
value→DiagnosticCode tables (motor/IMU/gohome/motorstop).

## Headline finding: DiagnosticCode text is LOCAL, not server-only

Earlier notes assumed the 30xxx texts lived only on DJI's HMS server. That is **wrong**.
The full resolution chain is on-device:

```
drone HMS alarm-id (8-hex)  --res/raw/hms2sdkcode.json-->  DiagnosticCode (int)
DiagnosticCode (int)        --config DSL baked in DEX-->    resName (e.g. fpv_tips_*)
resName                     --res/values/strings.xml-->     localized text
```

The DiagnosticCode→resName+severity map is the Kotlin DSL
`com.uav.diagnostic.config.module.<Sub>CodesKt.a(CodesBuilder)` (in
`unpacked_app_dex/classes_0855200c.dex`; recovered by baksmali). Each code is registered
via `CodesBuilder.b(code, lambda)`, and the lambda calls
`CodeConfigBuilder.c/b/f(Level, resId)` for checklist / capsule / tips strings plus a
severity `Level` (Red/Yellow/White/Transparent). Verified same integer namespace as
`diag_codes.py`: **30239 = "Unable to take off (no satellite positioning)"** (the DarkNoGps
code), 30037/30044 etc. all line up.

The only DJI **server** URLs in this area are FlySafe/GEO zone geometry
(`flysafe-api.dji.com`, `flysafe.dji.com`) and HMS **log-file** upload — neither carries
diagnostic display text. (Confirmed by DEX search: `parseHmsToDiagnostic`,
`getIdentifier(resName,"string")`, no text-fetch endpoint.)

## Namespaces / tables shipped in `diag_codes_full.py`

| # | Table | Entries | Source | Text? |
|---|-------|---------|--------|-------|
| 1 | `DIAGCODE_TEXT` — DiagnosticCode → {mod, lvl, tips/capsule/checklist} | **743** codes | `*CodesKt.a()` in `classes_0855200c.dex` + `strings.xml` | 718 have ≥1 English text (414 with a full "tips" sentence); 25 codes have no bundled text (kept with module+severity) |
| 2 | `HMS_ALARMID_TO_DIAGCODE` — 8-hex alarm-id → DiagnosticCode(s) | **1076** ids (689 distinct codes) | `res/raw/hms2sdkcode.json` (verbatim) | via table 1 (579/689 covered) |
| 3 | `REDUNDANCY_ERRORS` — FC (dev_type,err_type) sensor/cal errors | **178** | `res/raw/redunredundancy_error_code_desc_new.json` | Chinese `text_zh` only + semantic `tips_id`; no English string bundled |
| 4 | `ERROR_ENUM_CATALOG` — other error/status enums | **28** | `reverse_docs/TELEMETRY_TABLE.txt` | names + DUML source + native VA; value tables in native lib (see below) |

Helpers: `diagcode_text(code)`, `diagcode_info(code)`, `hms_alarmid_to_diagcode(id)`,
`hms_alarmid_text(id)`, `redundancy_error(dev_type, err_type)`.

### Table 1 breakdown by subsystem (module)

| module | codes | notes for WM160 |
|--------|------:|-----------------|
| FlightController | 339 | core: motors, IMU, compass, baro, GPS/positioning, takeoff, RTH, height/distance limits, low-battery-behavior — all reachable |
| Navigation | 107 | RTH/homing/waypoint/smart-flight reasons — reachable |
| Vision | 67 | mostly obstacle-avoidance; WM160 has **only downward VPS** → forward/back/up codes unreachable |
| Airlink | 58 | RC↔drone link (OcuSync/Wi-Fi). WM160 uses enhanced Wi-Fi — subset applies |
| Camera | 56 | storage/SD/recording/lens — reachable |
| Gimbal | 33 | calibration/stuck/overload — reachable |
| Battery | 29 | overcurrent/overheat/cell/comm/low-power — reachable |
| RemoteController | 26 | RC calibration/battery/link — reachable |
| EmbeddedSystem | 26 | generic system/inner faults — reachable |
| Product | 2 | product-level — reachable |

Severity (`DIAG_LEVELS`): Red = critical/blocking (295), Yellow = warning (381),
White = notice (42), Transparent/None = silent-or-unset (25).

### WM160 relevance, stated plainly

The APK ships **one** table set for every drone the app supports; none of these tables is
keyed by product. So WM160 can raise **any** code its firmware emits, and the ~110 HMS
codes with no table-1 text plus the vision/RTK/dual-battery codes are simply not reachable
on WM160 hardware. There is no per-model filter to extract — the model-specific part is
purely which codes the WM160 firmware actually sends.

## What is NOT statically recoverable here

- **Native-lib enum value tables** (table 4): `TakeoffFailureError`, `AutoRTHReason`,
  `GPSModeFailureReason`, `HeightLimitReason`, `ExitHomingReason`, `BatteryThresholdBehavior`,
  gimbal calibration states, etc. Their `value→meaning` tables live in **libsdk_jni.so**
  (~80 MB), which is present here only as a **132-byte git-LFS pointer**. The 4 already
  decoded (Motor/IMU/GoHome/MotorStop) are in `diag_codes.py`. To pull the rest, fetch the
  real `.so` and read the `CodeFor*` functions at the VAs listed in `ERROR_ENUM_CATALOG`.
- **~110 HMS alarm-ids** map to DiagnosticCodes that have no config entry in this build →
  no bundled text. Left absent, not guessed.
- **Redundancy DB** ships only Chinese `detail_ch_tips`; the English `tips_id` is a semantic
  key, not a resolvable string resource.

## Files touched / produced

- Produced: `../diag_codes_full.py`, this report.
- Not modified: `../diag_codes.py` and all other existing files.
- Key evidence files: `decompiled/res/raw/hms2sdkcode.json`,
  `decompiled/res/raw/redunredundancy_error_code_desc_new.json`,
  `decompiled/res/values/strings.xml`,
  `unpacked_app_dex/classes_0855200c.dex` (→ `com.uav.diagnostic.config.module.*CodesKt`),
  `reverse_docs/TELEMETRY_TABLE.txt`.
