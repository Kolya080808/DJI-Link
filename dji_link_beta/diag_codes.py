"""
DJI (WM160) diagnostics tables — from reversing libsdk_jni.so
(FCDiagnosticsUtility::CodeFor*), verified against res/raw/hms2sdkcode.json.

Chain for decoding the "motors won't start" cause:
  OSD byte +0x33 (FCMotorStartFailureError)
    -> MOTOR_NOT_START[val] = DiagnosticCode (30xxx)
    -> (the text for 30xxx is loaded by the app from the HMS server; the APK has only numbers)

Below: value->DiagnosticCode tables + human-readable labels for common
low codes (standard DJI values) + known takeoff strings from resources.
"""

from __future__ import annotations

# FCMotorStartFailureError value (byte +0x33) -> DiagnosticCode.
# 96 mapped; missing ones -> "unknown".
MOTOR_NOT_START = {
    1: 30220, 2: 30069, 3: 30070, 5: 30055, 6: 30071, 7: 30072, 8: 30073,
    9: 30066, 10: 30065, 11: 30074, 13: 30076, 14: 30077, 16: 30078, 17: 30079,
    18: 30080, 19: 30081, 21: 30082, 22: 30222, 23: 30083, 24: 30084, 25: 30085,
    26: 30067, 27: 30223, 28: 30086, 29: 30253, 30: 30087, 31: 30088, 32: 30089,
    33: 30090, 34: 30091, 35: 30092, 36: 30093, 37: 30094, 38: 30095, 39: 30096,
    40: 30097, 45: 30098, 47: 30099, 51: 30700, 52: 30701, 61: 30100, 63: 30101,
    64: 30102, 65: 30103, 66: 30104, 74: 30105, 75: 30106, 76: 30107, 77: 30108,
    79: 30212, 80: 30075, 83: 30224, 93: 30109, 94: 30110, 95: 30111, 96: 30112,
    97: 30113, 98: 30114, 99: 30115, 100: 30116, 101: 30117, 102: 30118,
    103: 30119, 104: 30209, 105: 30210, 106: 30211, 107: 30218, 112: 30120,
    113: 30121, 114: 30217, 115: 30122, 116: 30123, 117: 30124, 118: 30125,
    119: 30126, 120: 30127, 122: 30128, 123: 30129, 124: 30068, 125: 30130,
    127: 30132, 128: 30225, 129: 30133, 130: 30134, 131: 30068, 132: 30135,
    133: 30136, 134: 30137, 136: 30138, 138: 30219, 139: 30139, 146: 30140,
    147: 30239, 162: 30251, 163: 30254, 166: 30260,
}

# Human-readable labels for low values (standard DJI enum).
MOTOR_FAIL_NAME = {
    0: "NONE — all ok",
    1: "COMPASS_ERROR — compass error",
    2: "ASSISTANT_PROTECTED — Assistant connected/protected",
    3: "DEVICE_LOCKED — device locked",
    4: "DISTANCE_LIMIT — distance limit exceeded",
    5: "IMU_NEED_CALIBRATION — IMU calibration needed",
    6: "IMU_SN_ERROR — IMU serial number error",
    7: "IMU_PREHEATING — IMU warming up (wait)",
    8: "COMPASS_CALIBRATING — compass calibration in progress",
    9: "IMU_NO_ATTITUDE — IMU without attitude",
    10: "NO_GPS_AND_NOVICE — no GPS in novice mode",
    11: "BATTERY_CELL_ERROR — battery cell error",
    12: "BATTERY_COMMUNICATION_ERROR — no communication with battery",
    13: "SERIOUS_LOW_VOLTAGE — critically low voltage",
    14: "SERIOUS_LOW_POWER — critically low charge",
    15: "LOW_VOLTAGE — low voltage",
    147: "BACKUP_COMMUNICATE_FAIL — backup channel communication failure",
}

# Known DiagnosticCode texts (from resources/the app)
DIAG_TEXT = {
    30239: "cannot take off: no satellite positioning (weak GPS)",
}

# Separately: diagnostic code 30239 (weak-GPS lock) is cleared LOCALLY —
# by writing the FC parameter DarkNoGpsLockEnable=false (takeoff in ATTI). Requires a
# mechanism for writing parameters by hash (see §7 MASTER_REPORT — Frida/runtime needed for the hash).

# MotorStopReason value -> DiagnosticCode (10 of 32).
MOTOR_STOP = {
    94: 30141, 95: 30142, 96: 30143, 97: 30144, 98: 30145, 99: 30146,
    100: 30147, 104: 30234, 116: 30235, 125: 30236,
}

# IMUFailureReason value -> DiagnosticCode.
IMU_FAILURE = {
    2: 30044, 3: 30046, 4: 30059, 5: 30041, 6: 30042, 7: 30062, 8: 30055,
    9: 30055, 10: 30062, 11: 30043, 12: 30054, 13: 30054, 14: 30054, 15: 30054,
}

# GoHomeStage value -> DiagnosticCode.
GOHOME_STAGE = {1: 30240, 2: 30241, 3: 30242, 4: 30243, 5: 30200,
                6: 30200, 7: 30200, 8: 30200, 9: 30810}

# flyc_state (OSD +0x1e & 0x7F) — standard DJI values.
FLYC_STATE = {
    0: "MANUAL", 1: "ATTI", 3: "ATTI_HOVER", 4: "HOVER", 5: "GPS_BLAKE",
    6: "GPS_ATTI", 7: "GPS_CRUISE", 8: "GPS_HOME_LOCK", 9: "GPS_HOT_POINT",
    10: "ASSISTED_TAKEOFF", 11: "AUTO_TAKEOFF", 12: "AUTO_LANDING",
    15: "GO_HOME", 17: "JOYSTICK", 33: "GPS_ATTI_WRISTBAND", 40: "CLICK_GO",
}


def motor_fail_text(value: int) -> str:
    """Human-readable description of the motor start failure cause from byte +0x33.

    Resolves the whole chain automatically: name table -> DiagnosticCode -> code text.
    The caller should never have to look anything up by hand.
    """
    name = MOTOR_FAIL_NAME.get(value)
    code = MOTOR_NOT_START.get(value)
    text = DIAG_TEXT.get(code) if code else None

    # The name and the DiagnosticCode come from two different namespaces and can
    # disagree (e.g. 147 is BACKUP_COMMUNICATE_FAIL, yet maps to code 30239 whose text
    # is about GPS). The name describes this very value, so it wins; the code is only
    # ever reported as a number alongside it, never as a competing explanation.
    if name:
        return f"{name} [code {code}]" if code else name
    if text:
        return f"{text} [code {code}]"
    if code:
        # Mapped to a DiagnosticCode, but its text lives on DJI's HMS server and is not
        # in the APK — the number is still the actionable part.
        return f"DiagnosticCode {code} (value {value}; text only on DJI's HMS server)"
    return f"unknown cause, raw value {value}"
