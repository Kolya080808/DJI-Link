// DJI Mavic Mini (WM160) telemetry decoder, ported from telemetry.py.
//
// GPS coordinate parsing (drone_lat/lon, home coordinates) is intentionally left out —
// but the satellite count and GPS signal level ARE decoded (0x43 @0x24 / (u32@0x20 >> 18) & 0xF)
// exactly as the beta does, because they are the HUD's "can I fly / is the fix good" cue.
// The "home recorded" yes/no flag also works and is parsed. See memory: cpp-client-goal.
#pragma once

#include "core/bytes.hpp"

#include <cstdint>
#include <optional>
#include <string>

namespace djilink {

struct DumlPacket; // fwd

// SDKCtrlDevice: who currently commands the FC (OSD-common @0x34). APP(1) means
// the FC accepted our virtual-stick control.
std::string sdk_ctrl_device_name(int code);
// DataOsdGetPushCommon$FLYC_STATE code -> name (sparse; verified from the jar).
std::string flyc_state_name(int code);

// Flight profile explicitly observed in FLYC_STATE. This is not the selected RC gear: transient
// and degraded states do not identify which config block remains selected.
enum class ObservedFlightProfile { Normal, Sport, Cine, Tripod };

// Map a raw FLYC_STATE code to the user mode it implies, or nullopt for a transient/action state
// (takeoff, landing, RTH, virtual sticks, QuickShot, ...) during which the user's selected mode is
// unchanged — the caller must then keep the previous value ("transient states keep last"). Total,
// never throws.
std::optional<ObservedFlightProfile> observed_flight_profile(int flyc_state);

// Human-readable HUD label for a derived mode ("Normal"/"Sport"/"Cine"/"Tripod"); Capitalized for
// display, distinct from flyc_state_name's raw enum labels. Total, never throws.
std::string observed_flight_profile_name(ObservedFlightProfile profile);

// Everything we extract from the various pushes, accumulated into one state.
struct OsdState {
    std::optional<int> battery_pct;
    std::optional<std::uint32_t> battery_mv;
    std::optional<std::int32_t> battery_ma;
    std::optional<double> battery_temp_c;
    std::optional<int> remaining_flight_time_s;
    std::optional<double> altitude_m;
    std::optional<double> vps_height_m;
    std::optional<double> vx, vy, vz;
    std::optional<double> pitch, roll, yaw;
    std::optional<int> flight_mode;
    std::optional<std::string> flight_mode_name;
    // Last profile directly identified by FLYC_STATE. Ambiguous states such as Atti and Joystick
    // preserve it; nullopt until an explicit Normal/Sport/Cine/Tripod state is observed.
    std::optional<ObservedFlightProfile> observed_profile;
    std::optional<bool> is_flying;
    std::optional<bool> motors_on;
    std::optional<int> ctrl_device;
    std::optional<bool> is_recording;
    std::optional<int> record_time_s;
    std::optional<bool> home_set;
    std::optional<int> motor_fail_code;
    std::optional<std::string> motor_fail_reason;
    std::optional<int> imu_fail_code;
    std::optional<int> flight_time_s;
    std::optional<int> total_flights;
    std::optional<bool> sim_started;
    std::optional<bool> near_height_limit;
    std::optional<bool> near_dist_limit;
    std::optional<double> max_height_m;   // read via param 0xF8
    std::optional<double> max_distance_m; // read via param 0xF8
    std::optional<double> rth_altitude_m; // read via param 0xF8
    std::optional<bool> home_recorded;
    std::optional<int> satellites; // u8 @0x24 (getGpsNum) — count of locked satellites
    std::optional<int> gps_level;  // (u32@0x20 >> 18) & 0xF (getGpsLevel), 0..5

    std::string summary() const;
};

class Telemetry {
public:
    void feed_packet(const DumlPacket& pkt);
    OsdState& state() {
        return state_;
    }
    const OsdState& state() const {
        return state_;
    }

private:
    void parse_osd(const Bytes& p);
    void parse_battery(const Bytes& p);
    void parse_camera_state(const Bytes& p);
    void parse_home_location(const Bytes& p);

    OsdState state_;
};

} // namespace djilink
