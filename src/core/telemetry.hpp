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

// The user-facing mode we DERIVE from the live FLYC_STATE byte (roadmap T4) — as opposed to the
// FlightMode a user asks for (flight_mode.hpp) or the RC gear we emit to select it. Kept separate
// from FlightMode because the firmware reports a Tripod state (38) distinctly from Cinematic (19),
// and the Cine<->Tripod equivalence is still an on-hardware open question: collapsing 38 into Cine
// here would bake in an unverified guess, so Tripod is its own value until confirmed.
enum class DerivedFlightMode { Normal, Sport, Cine, Tripod };

// Map a raw FLYC_STATE code to the user mode it implies, or nullopt for a transient/action state
// (takeoff, landing, RTH, virtual sticks, QuickShot, ...) during which the user's selected mode is
// unchanged — the caller must then keep the previous value ("transient states keep last"). Total,
// never throws.
std::optional<DerivedFlightMode> derived_user_mode(int flyc_state);

// Human-readable HUD label for a derived mode ("Normal"/"Sport"/"Cine"/"Tripod"); Capitalized for
// display, distinct from flyc_state_name's raw enum labels. Total, never throws.
std::string derived_flight_mode_name(DerivedFlightMode mode);

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
    // Sticky user mode derived from FLYC_STATE (roadmap T4). A decisive state overwrites it; a
    // transient ACTION (takeoff/land/RTH/joystick/QuickShot) leaves it, so the HUD (T5) does not
    // flicker mid-manoeuvre. Sticky against actions, not against a real block change: losing GPS
    // degrades Sport/Cine into Atti — a decisive Normal per roadmap — so the HUD then reads Normal
    // by design (flag for the T9 hardware checklist). Not reset on land/disarm; nullopt until the
    // first decisive state.
    std::optional<DerivedFlightMode> user_mode;
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
