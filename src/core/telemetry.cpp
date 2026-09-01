#include "core/telemetry.hpp"

#include "core/diag_codes.hpp"
#include "core/duml.hpp"

#include <algorithm>
#include <cmath>
#include <map>
#include <optional>
#include <sstream>

namespace djilink {
namespace {

const std::map<int, std::string> kSdkCtrlDevice = {
    {0, "RC"}, {1, "APP"}, {2, "ONBOARD"}, {3, "CAMERA"}};

// DataOsdGetPushCommon$FLYC_STATE — sparse; codes verified from the jar (telemetry.py).
const std::map<int, std::string> kFlycState = {{0, "Manual"},
                                               {1, "Atti"},
                                               {2, "Atti_CL"},
                                               {3, "Atti_Hover"},
                                               {4, "Hover"},
                                               {5, "GPS_Blake"},
                                               {6, "GPS_Atti"},
                                               {7, "GPS_CL"},
                                               {8, "GPS_HomeLock"},
                                               {9, "GPS_HotPoint"},
                                               {10, "AssistedTakeoff"},
                                               {11, "AutoTakeoff"},
                                               {12, "AutoLanding"},
                                               {13, "AttiLanding"},
                                               {14, "NaviGo"},
                                               {15, "GoHome"},
                                               {16, "ClickGo"},
                                               {17, "Joystick"},
                                               {19, "Cinematic"},
                                               {23, "Atti_Limited"},
                                               {24, "NaviSubMode_Draw"},
                                               {25, "NaviMissionFollow"},
                                               {26, "NaviSubMode_Tracking"},
                                               {27, "NaviSubMode_Pointing"},
                                               {28, "PANO"},
                                               {29, "Farming"},
                                               {30, "FPV"},
                                               {31, "SPORT"},
                                               {32, "NOVICE"},
                                               {33, "FORCE_LANDING"},
                                               {35, "TERRAIN_TRACKING"},
                                               {36, "PALM_CONTROL"},
                                               {37, "QUICK_SHOT"},
                                               {38, "TRIPOD_GPS"},
                                               {39, "TRACK_HEADLOCK"},
                                               {41, "ENGINE_START"},
                                               {43, "DETOUR"},
                                               {46, "TIME_LAPSE"},
                                               {49, "OMNI_MOVING"},
                                               {50, "POI_WITH_VISION"},
                                               {51, "SMART_TRACK"},
                                               {52, "LOST_POWER_FORCE_LANDING"},
                                               {100, "OTHER"}};

std::string opt_str(const std::optional<int>& v) {
    return v ? std::to_string(*v) : "None";
}

} // namespace

std::string sdk_ctrl_device_name(int code) {
    auto it = kSdkCtrlDevice.find(code);
    return it != kSdkCtrlDevice.end() ? it->second : std::to_string(code);
}

std::string flyc_state_name(int code) {
    auto it = kFlycState.find(code);
    return it != kFlycState.end() ? it->second : ("?" + std::to_string(code));
}

std::optional<ObservedFlightProfile> observed_flight_profile(int flyc_state) {
    switch (flyc_state) {
        // The three explicitly selectable modes each report their own FLYC_STATE code.
        case 31: // SPORT
            return ObservedFlightProfile::Sport;
        case 19: // Cinematic
            return ObservedFlightProfile::Cine;
        case 38: // TRIPOD_GPS — kept distinct from Cine until the Cine<->Tripod link is confirmed
                 // on hardware (roadmap open unknown), so we do not fold 38 into Cine here.
            return ObservedFlightProfile::Tripod;
        // GPS_Atti and Novice explicitly identify the ordinary position profile. Atti/Hover and
        // other degraded states are ambiguous because the selected gear can remain unchanged.
        case 6:  // GPS_Atti
        case 32: // NOVICE
            return ObservedFlightProfile::Normal;
        default:
            // Transient / action / intelligent-flight states (Manual, AutoTakeoff, AutoLanding,
            // GoHome, Joystick, QuickShot, Pano, GPS_HotPoint, ...) do not reflect the user's
            // selected mode: return nullopt so the caller keeps the last decisive value.
            return std::nullopt;
    }
}

std::string observed_flight_profile_name(ObservedFlightProfile profile) {
    switch (profile) {
        case ObservedFlightProfile::Normal:
            return "Normal";
        case ObservedFlightProfile::Sport:
            return "Sport";
        case ObservedFlightProfile::Cine:
            return "Cine";
        case ObservedFlightProfile::Tripod:
            return "Tripod";
    }
    return "Normal"; // unreachable: the switch is exhaustive over the enum
}

std::string OsdState::summary() const {
    std::ostringstream os;
    std::string m = flight_mode_name ? *flight_mode_name
                                     : (flight_mode ? std::to_string(*flight_mode) : "None");
    os << "mode=" << m << "  battery=" << opt_str(battery_pct) << "%"
       << "  altitude=" << (altitude_m ? std::to_string(*altitude_m) : "None") << "m"
       << "  climb=" << (vz ? std::to_string(*vz) : "None") << "m/s"
       << "  remain_time=" << opt_str(remaining_flight_time_s) << "s"
       << "  flying=" << (is_flying ? (*is_flying ? "True" : "False") : "None")
       << "  motors=" << (motors_on ? (*motors_on ? "True" : "False") : "None")
       << "  home=" << (home_set ? (*home_set ? "True" : "False") : "None")
       << "  sats=" << (satellites ? std::to_string(*satellites) : std::string("-"))
       << "  gps_lvl=" << (gps_level ? std::to_string(*gps_level) : std::string("-"));
    if (motor_fail_code && *motor_fail_code) {
        os << "  !MOTOR_START_FAIL_CAUSE=" << (motor_fail_reason ? *motor_fail_reason : "") << "("
           << *motor_fail_code << ")";
    }
    return os.str();
}

void Telemetry::feed_packet(const DumlPacket& pkt) {
    const Bytes& p = pkt.payload;
    if (pkt.cmd_set == 0x03 && pkt.cmd_id == 0x43 && p.size() >= 0x34) {
        parse_osd(p);
    } else if (pkt.cmd_set == 0x09 && pkt.cmd_id == 0x01 && p.size() >= 0x34) {
        parse_osd(p);
    } else if (pkt.cmd_set == 0x03 && pkt.cmd_id == 0x44 && p.size() >= 0x18) {
        parse_home_location(p);
    } else if (pkt.cmd_set == 0x09 && pkt.cmd_id == 0x02 && p.size() >= 0x18) {
        parse_home_location(p);
    } else if (pkt.cmd_set == 0x0D && pkt.cmd_id == 0x02 && p.size() >= 0x14) {
        parse_battery(p);
    } else if (pkt.cmd_set == 0x02 && pkt.cmd_id == 0x80 && p.size() >= 0x1f) {
        parse_camera_state(p);
    }
}

void Telemetry::parse_camera_state(const Bytes& p) {
    OsdState& st = state_;
    if (auto b0 = get_u8(p, 0)) {
        const int rs = (*b0 >> 6) & 3;
        st.is_recording = (rs == 1 || rs == 2); // 3=STOP is not recording
    }
    if (auto rt = get_u16(p, 0x1d))
        st.record_time_s = *rt;
}

void Telemetry::parse_battery(const Bytes& p) {
    OsdState& st = state_;
    st.battery_mv = get_u32(p, 0x01);
    st.battery_ma = get_s32(p, 0x05);
    if (auto temp = get_s16(p, 0x11))
        st.battery_temp_c = *temp * 0.1;
    if (auto pct = get_u8(p, 0x14); pct && *pct <= 100) {
        st.battery_pct = *pct;
    } else {
        auto full = get_u32(p, 0x09);
        auto rem = get_u32(p, 0x0D);
        if (full && rem && *full > 0) {
            st.battery_pct = std::min(
                100, static_cast<int>(std::lround(static_cast<double>(*rem) / *full * 100)));
        }
    }
}

void Telemetry::parse_osd(const Bytes& p) {
    OsdState& st = state_;
    // GPS coordinate block (@0x00 lon / @0x08 lat, f64 radians) intentionally skipped.
    if (auto alt = get_s16(p, 0x10))
        st.altitude_m = *alt * 0.1;
    if (auto vx = get_s16(p, 0x12))
        st.vx = *vx * 0.1;
    if (auto vy = get_s16(p, 0x14))
        st.vy = *vy * 0.1;
    if (auto vz = get_s16(p, 0x16))
        st.vz = *vz * 0.1; // vertical velocity = climb rate
    if (auto pi = get_s16(p, 0x18))
        st.pitch = *pi * 0.1;
    if (auto ro = get_s16(p, 0x1a))
        st.roll = *ro * 0.1;
    if (auto ya = get_s16(p, 0x1c))
        st.yaw = *ya * 0.1;
    if (auto mode = get_u8(p, 0x1e)) {
        st.flight_mode = *mode & 0x7F;
        st.flight_mode_name = flyc_state_name(*st.flight_mode);
        if (auto observed = observed_flight_profile(*st.flight_mode))
            st.observed_profile = *observed;
    }
    if (auto w = get_u32(p, 0x20)) {
        st.is_flying = ((*w >> 1) & 3) == 2; // groundOrSky==2 = flying
        st.motors_on = ((*w >> 3) & 1) != 0;
        st.gps_level = static_cast<int>((*w >> 18) & 0xF); // getGpsLevel, 0..5
    }
    // Satellite count: 1 BYTE @0x24 (getGpsNum). The app's "Short" is boxing, not width —
    // a u16 read inflates the count whenever p[0x25]!=0 (see TELEMETRY_TRUTH.md §7).
    if (auto sats = get_u8(p, 0x24))
        st.satellites = *sats;
    if (auto vps = get_s8(p, 0x29))
        st.vps_height_m = *vps * 0.1;
    if (auto ft = get_u16(p, 0x2a))
        st.flight_time_s = *ft / 10; // wire field is deciseconds
    if (auto cd = get_u8(p, 0x34))
        st.ctrl_device = *cd;
    if (auto mf = get_u8(p, 0x26)) {
        const int code = *mf & 0x7F;
        st.motor_fail_code = code;
        st.motor_fail_reason = motor_fail_text(code);
    }
}

void Telemetry::parse_home_location(const Bytes& p) {
    // Only the "home recorded" flag (u16 @0x14 bit0); coordinates are NOT parsed (never
    // trustworthy on WM160 — matches telemetry.py).
    if (auto flags = get_u16(p, 0x14)) {
        state_.home_recorded = (*flags & 1) != 0;
        state_.home_set = state_.home_recorded;
    }
}

} // namespace djilink
