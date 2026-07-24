// High-level Drone API for the DJI Mavic Mini 1 (WM160), ported from drone.py.
// A single point through which any command source controls the drone. Media /
// playback methods are intentionally omitted for now (project scope).
#pragma once

#include "core/bytes.hpp"
#include "core/control.hpp"
#include "core/transport.hpp"

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>

namespace djilink {

// DUML addresses (drone.py). NOTE: we speak as the MOBILE APP (0x02); 0x0a is the
// PC/Assistant address and makes the FC lock the motors (AssistantProtected).
inline constexpr std::uint8_t DEV_APP = 0x02;
inline constexpr std::uint8_t DEV_RC = 0x02;
inline constexpr std::uint8_t DEV_FC = 0x03;
inline constexpr std::uint8_t DEV_GIMBAL = 0x04;
inline constexpr std::uint8_t DEV_CAMERA = 0x01;
inline constexpr std::uint8_t DEV_DM368 = 0x08;

inline constexpr std::uint8_t CMDSET_CAMERA = 0x02;

class Drone {
public:
    explicit Drone(Transport* transport);
    ~Drone();

    // Encrypt FC config/param frames (needed on the app/radio path). Plaintext for serial.
    bool encrypt_config = true;

    void stop();

    // ---- flight control ----
    void set_sticks(double roll, double pitch, double yaw, double throttle);
    void request_control();
    void release_control();
    void set_ground_station_mode(bool on = true);
    void enable_virtual_stick(bool on = true);
    void rc_to_pc_control();
    void preempt_control();

    void takeoff();
    void cancel_takeoff();
    void land();
    void cancel_land();
    void force_land();
    void return_to_home();
    void cancel_rth();
    void start_motors();
    void stop_motors();
    void start_calibration();
    void set_home_to_aircraft();
    void motor_force_disable(bool disable = true);

    // ---- flight limits / params ----
    void set_max_altitude(int metres);
    void set_max_distance(int metres);
    void set_max_altitude_cmd(int metres);
    void set_max_distance_cmd(int metres);
    void assistant_unlock();
    void get_limits(int mode = 1);
    void set_param(const std::string& name, const Bytes& value_bytes);
    void set_horizontal_speed(double mps);
    void unlock_no_gps(bool unlock = true);
    void get_param_info(int index);
    void read_param(const std::string& name);
    void set_flight_mode(const std::string& name);

    // ---- home point ----
    void set_home_point(double lat_deg, double lon_deg);
    void set_home_to_current_location();
    void set_rth_altitude(int metres);

    // ---- gimbal ----
    void gimbal_calibrate();
    void gimbal_angle(double pitch_deg, double yaw_deg = 0.0, double roll_deg = 0.0,
                      double duration_s = 1.0);
    void gimbal_speed(double pitch_dps, double yaw_dps = 0.0, double roll_dps = 0.0);
    void gimbal_recenter();

    // ---- stick frames ----
    void set_sticks_mobilerc(double roll, double pitch, double yaw, double throttle, int mode = 0);
    static std::uint8_t build_stick_flag(bool rollpitch_velocity = true, bool yaw_rate = true,
                                         bool vertical_velocity = true, bool body_frame = false,
                                         bool advanced = false);
    void set_sticks_float(double roll, double pitch, double yaw, double throttle,
                          std::uint8_t flag = 0x48);
    void set_sticks_velocity(double roll, double pitch, double yaw, double throttle,
                             std::uint8_t flag = 0x4A, double h_mps = 5.0, double v_mps = 2.0,
                             double yaw_dps = 90.0);

    // ---- camera / video ----
    void take_photo(int ptype = 1); // 1 = SINGLE
    void start_record();
    void stop_record();
    void set_camera_mode(int mode);
    void request_i_frame();
    void start_liveview(int camera_source = 0);
    void set_zoom(double factor);
    void set_exposure_mode(int mode);
    void set_iso_auto();
    void set_iso(int iso);
    void set_shutter(int denom);
    void set_shutter_auto();
    void set_ev(int ev_thirds);
    void set_white_balance(int mode, int ct_index = 0);
    void set_video_format(int resolution, int framerate, int fov = 0);
    void set_photo_mode(int code);
    void set_video_codec(bool h265 = false);

    // universal escape hatch
    void send_raw(std::uint8_t cmd_set, std::uint8_t cmd_id, const Bytes& payload = {},
                  std::uint8_t receiver = DEV_FC, bool ack = true);

private:
    std::uint16_t next_seq();
    void cmd(std::uint8_t cmd_set, std::uint8_t cmd_id, const Bytes& payload = {},
             std::uint8_t receiver = DEV_FC, bool ack = true);
    void fc_function(std::uint8_t sub);

    Transport* t_;
    FlightProfile profile_;
    std::uint16_t seq_ = 0;
    int shutter_denom_ = -1;                   // last user-set 1/N shutter (-1 = auto)
    std::shared_ptr<std::atomic<bool>> alive_; // guards detached camera-sequence threads
};

} // namespace djilink
