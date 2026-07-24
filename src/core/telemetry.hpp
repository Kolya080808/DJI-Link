// DJI Mavic Mini (WM160) telemetry decoder, ported from telemetry.py.
//
// NOTE (per project scope): ALL GPS parsing is intentionally left out for now —
// no satellite count, no GPS level, no drone_lat/lon, no home coordinates — matching
// the Python beta where that path is still unfinished. The "home recorded" yes/no
// flag DOES work and is parsed. See memory: cpp-client-goal.
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
