#include "core/drone.hpp"

#include "core/duml.hpp"
#include "core/param_hash.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <map>
#include <stdexcept>
#include <thread>

namespace djilink {
namespace {
double clamp1(double v) {
    return std::max(-1.0, std::min(1.0, v));
}
constexpr double kPi = 3.14159265358979323846;
} // namespace

Drone::Drone(Transport* transport)
    : t_(transport), alive_(std::make_shared<std::atomic<bool>>(true)) {}

Drone::~Drone() {
    stop();
}

void Drone::stop() {
    alive_->store(false);
}

std::uint16_t Drone::next_seq() {
    seq_ = static_cast<std::uint16_t>((seq_ + 1) & 0xFFFF);
    return seq_;
}

void Drone::cmd(std::uint8_t cmd_set, std::uint8_t cmd_id, const Bytes& payload,
                std::uint8_t receiver, bool ack) {
    DumlPacket pkt;
    pkt.sender = DEV_APP;
    pkt.receiver = receiver;
    pkt.cmd_set = cmd_set;
    pkt.cmd_id = cmd_id;
    pkt.seq = next_seq();
    pkt.cmd_type = ack ? 0x40 : 0x00;
    pkt.payload = payload;
    Bytes frame = pkt.encode();
    // FLYC config/param commands must be SIMPLE-encrypted on the radio path.
    if (encrypt_config && cmd_set == 0x03 &&
        (cmd_id == 0xF0 || cmd_id == 0xF7 || cmd_id == 0xF8 || cmd_id == 0xF9 || cmd_id == 0xFA)) {
        frame = encrypt_frame(frame);
    }
    t_->send(frame);
}

// ---------------------------------------------------------------- flight control
void Drone::set_sticks(double roll, double pitch, double yaw, double throttle) {
    Sticks s = Sticks{roll, pitch, yaw, throttle}.clamp();
    cmd(profile_.cmd_set, profile_.cmd_id, sticks_to_payload(s, profile_), DEV_FC, false);
}

void Drone::request_control() {
    cmd(0x49, 0x80, {0x01}, 0x00);
    cmd(0x49, 0x80, {0x01}, DEV_FC);
}
void Drone::release_control() {
    cmd(0x49, 0x80, {0x00}, 0x00);
    cmd(0x49, 0x80, {0x00}, DEV_FC);
}
void Drone::set_ground_station_mode(bool on) {
    cmd(0x03, 0x80, {static_cast<std::uint8_t>(on ? 1 : 2)}, DEV_FC);
}
void Drone::enable_virtual_stick(bool on) {
    if (on) {
        request_control();
        set_ground_station_mode(true);
    } else {
        set_ground_station_mode(false);
        release_control();
    }
}
void Drone::rc_to_pc_control() {
    cmd(0x06, 0xF1, {0x01});
}
void Drone::preempt_control() {
    cmd(0x19, 0x41, {0x01});
}

void Drone::fc_function(std::uint8_t sub) {
    cmd(0x03, 0x2A, {sub}, DEV_FC);
}
void Drone::takeoff() {
    fc_function(0x01);
}
void Drone::cancel_takeoff() {
    fc_function(0x0D);
}
void Drone::land() {
    fc_function(0x02);
}
void Drone::cancel_land() {
    fc_function(0x0E);
}
void Drone::force_land() {
    fc_function(0x1E);
}
void Drone::return_to_home() {
    fc_function(0x06);
}
void Drone::cancel_rth() {
    fc_function(0x0C);
}
void Drone::start_motors() {
    fc_function(0x07);
}
void Drone::stop_motors() {
    fc_function(0x08);
}
void Drone::start_calibration() {
    fc_function(0x09);
}
void Drone::set_home_to_aircraft() {
    fc_function(0x03);
}
void Drone::motor_force_disable(bool disable) {
    cmd(0x03, 0xFE, {static_cast<std::uint8_t>(disable ? 1 : 0)}, DEV_FC);
}

// ---------------------------------------------------------------- limits / params
void Drone::set_max_altitude(int metres) {
    const int m = std::max(15, std::min(500, metres));
    Bytes v;
    put_u16(v, static_cast<std::uint16_t>(m));
    set_param("g_config.flying_limit.max_height_0", v);
}
void Drone::set_max_distance(int metres) {
    const int m = std::max(15, std::min(5000, metres));
    Bytes v;
    put_u16(v, static_cast<std::uint16_t>(m));
    set_param("g_config.flying_limit.max_radius_0", v);
}
void Drone::set_max_altitude_cmd(int metres) {
    const int m = std::max(15, std::min(500, metres));
    Bytes p{0x01};
    put_u16(p, static_cast<std::uint16_t>(m));
    cmd(0x03, 0x2D, p, DEV_FC);
}
void Drone::set_max_distance_cmd(int metres) {
    const int m = std::max(15, std::min(5000, metres));
    Bytes p{0x02};
    put_u16(p, static_cast<std::uint16_t>(m));
    cmd(0x03, 0x2D, p, DEV_FC);
}
void Drone::assistant_unlock() {
    Bytes v;
    put_u32(v, 1);
    cmd(0x03, 0xDF, v, DEV_FC);
}
void Drone::get_limits(int mode) {
    cmd(0x03, 0x2E, {static_cast<std::uint8_t>(mode)}, DEV_FC);
}

void Drone::set_param(const std::string& name, const Bytes& value_bytes) {
    Bytes p;
    put_u32(p, param_hash(name));
    p.insert(p.end(), value_bytes.begin(), value_bytes.end());
    cmd(0x03, 0xF9, p, DEV_FC);
}
void Drone::set_horizontal_speed(double mps) {
    const double tilt = std::max(5.0, std::min(40.0, mps * 2.5));
    Bytes v;
    put_f32(v, static_cast<float>(tilt));
    set_param("g_config.mode_normal_cfg.tilt_atti_range_0", v);
}
void Drone::unlock_no_gps(bool unlock) {
    set_param("fc_dark_need_gps_0", {static_cast<std::uint8_t>(unlock ? 0 : 1)});
}
void Drone::get_param_info(int index) {
    Bytes p;
    put_u16(p, static_cast<std::uint16_t>(index));
    cmd(0x03, 0xF0, p, DEV_FC);
}
void Drone::read_param(const std::string& name) {
    Bytes p;
    put_u32(p, param_hash(name));
    cmd(0x03, 0xF8, p, DEV_FC);
}
void Drone::set_flight_mode(const std::string& name) {
    static const std::map<std::string, double> kTilt = {{"cine", 10.0},      {"cinema", 10.0},
                                                        {"cinematic", 10.0}, {"normal", 20.0},
                                                        {"sport", 30.0},     {"max", 40.0}};
    std::string key = name;
    std::transform(key.begin(), key.end(), key.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    auto it = kTilt.find(key);
    if (it == kTilt.end()) {
        throw std::invalid_argument("unknown mode '" + name + "'; use cine/normal/sport/max");
    }
    Bytes v;
    put_f32(v, static_cast<float>(it->second));
    set_param("g_config.mode_normal_cfg.tilt_atti_range_0", v);
}

// ---------------------------------------------------------------- home point
void Drone::set_home_point(double lat_deg, double lon_deg) {
    Bytes p{0x02};
    put_f64(p, lat_deg * kPi / 180.0);
    put_f64(p, lon_deg * kPi / 180.0);
    p.push_back(0x00);
    cmd(0x03, 0x31, p, DEV_FC);
}
void Drone::set_home_to_current_location() {
    Bytes p{0x00};
    put_f64(p, 0.0);
    put_f64(p, 0.0);
    p.push_back(0x00);
    cmd(0x03, 0x31, p, DEV_FC);
}
void Drone::set_rth_altitude(int metres) {
    const int m = std::max(20, std::min(500, metres));
    Bytes v;
    put_u16(v, static_cast<std::uint16_t>(m));
    set_param("g_config.go_home.fixed_go_home_altitude_0", v);
}

// ---------------------------------------------------------------- gimbal
void Drone::gimbal_calibrate() {
    cmd(0x04, 0x08, {}, DEV_GIMBAL);
}

void Drone::gimbal_angle(double pitch_deg, double yaw_deg, double roll_deg, double duration_s) {
    Bytes p;
    put_u16(p, static_cast<std::uint16_t>(static_cast<std::int16_t>(std::llround(yaw_deg * 10))));
    put_u16(p, static_cast<std::uint16_t>(static_cast<std::int16_t>(std::llround(roll_deg * 10))));
    put_u16(p, static_cast<std::uint16_t>(static_cast<std::int16_t>(std::llround(pitch_deg * 10))));
    const int dur = std::max(0, std::min(255, static_cast<int>(std::llround(duration_s * 10))));
    p.push_back(0x01);
    p.push_back(static_cast<std::uint8_t>(dur));
    cmd(0x04, 0x14, p, DEV_GIMBAL);
}
void Drone::gimbal_speed(double pitch_dps, double yaw_dps, double roll_dps) {
    Bytes p;
    put_u16(p, static_cast<std::uint16_t>(static_cast<std::int16_t>(std::llround(yaw_dps * 10))));
    put_u16(p, static_cast<std::uint16_t>(static_cast<std::int16_t>(std::llround(roll_dps * 10))));
    put_u16(p, static_cast<std::uint16_t>(static_cast<std::int16_t>(std::llround(pitch_dps * 10))));
    p.push_back(0x81);
    p.push_back(0x00);
    cmd(0x04, 0x0C, p, DEV_GIMBAL);
}
void Drone::gimbal_recenter() {
    cmd(0x04, 0x4C, {0xFE, 0x01}, DEV_GIMBAL);
}

// ---------------------------------------------------------------- stick frames
void Drone::set_sticks_mobilerc(double roll, double pitch, double yaw, double throttle, int mode) {
    auto ch = [](double v) -> std::uint64_t {
        int r = 1024 + static_cast<int>(std::llround(clamp1(v) * 660));
        r = std::max(364, std::min(1684, r));
        return static_cast<std::uint64_t>(r & 0x7FF);
    };
    const std::uint64_t packed =
        ch(throttle) | (ch(roll) << 11) | (ch(yaw) << 22) | (ch(pitch) << 33);
    const std::uint16_t flags = static_cast<std::uint16_t>(0x0200 | ((mode & 3) << 10));
    Bytes p{0x00};
    for (int i = 0; i < 8; ++i)
        p.push_back(static_cast<std::uint8_t>((packed >> (8 * i)) & 0xFF));
    p.push_back(0x00);
    p.push_back(0x00);
    put_u16(p, flags);
    cmd(0x01, 0x02, p, DEV_FC, false);
}

std::uint8_t Drone::build_stick_flag(bool rollpitch_velocity, bool yaw_rate, bool vertical_velocity,
                                     bool body_frame, bool advanced) {
    const int rp = rollpitch_velocity ? 1 : 0;
    const int vt = vertical_velocity ? 0 : 1; // VELOCITY=0, POSITION=1
    const int yw = yaw_rate ? 1 : 0;          // ANGLE=0, ANGULAR_VELOCITY=1
    const int co = body_frame ? 1 : 0;        // GROUND=0, BODY=1
    return static_cast<std::uint8_t>(
        ((rp << 6) | (vt << 4) | (yw << 3) | (co << 1) | (advanced ? 1 : 0)) & 0xFF);
}

void Drone::set_sticks_float(double roll, double pitch, double yaw, double throttle,
                             std::uint8_t flag) {
    // WM160 wire order (empirical): pitch, roll, throttle, yaw.
    Bytes p{flag};
    put_f32(p, static_cast<float>(pitch));
    put_f32(p, static_cast<float>(roll));
    put_f32(p, static_cast<float>(throttle));
    put_f32(p, static_cast<float>(yaw));
    cmd(0x03, 0x8E, p, DEV_FC);
}

void Drone::set_sticks_velocity(double roll, double pitch, double yaw, double throttle,
                                std::uint8_t flag, double h_mps, double v_mps, double yaw_dps) {
    set_sticks_float(clamp1(roll) * h_mps, clamp1(pitch) * h_mps, clamp1(yaw) * yaw_dps,
                     clamp1(throttle) * v_mps, flag);
}

// ---------------------------------------------------------------- camera / video
void Drone::take_photo(int ptype) {
    cmd(CMDSET_CAMERA, 0x10, {0x00}, DEV_CAMERA); // work mode = PHOTO
    auto alive = alive_;
    Transport* t = t_;
    std::uint8_t pt = static_cast<std::uint8_t>(ptype & 0xFF);
    std::uint16_t seq = next_seq();
    std::thread([alive, t, pt, seq]() {
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        if (!alive->load())
            return;
        DumlPacket pkt;
        pkt.sender = DEV_APP;
        pkt.receiver = DEV_CAMERA;
        pkt.cmd_set = CMDSET_CAMERA;
        pkt.cmd_id = 0x01;
        pkt.seq = seq;
        pkt.cmd_type = 0x40;
        pkt.payload = {pt};
        t->send(pkt.encode());
    }).detach();
}

void Drone::start_record() {
    cmd(CMDSET_CAMERA, 0x10, {0x01}, DEV_CAMERA); // work mode = RECORD/video
    auto alive = alive_;
    Transport* t = t_;
    std::uint16_t s0 = next_seq(), s1 = next_seq(), s2 = next_seq();
    std::uint16_t seqs[3] = {s0, s1, s2};
    std::thread([alive, t, seqs]() {
        for (int i = 0; i < 3; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(i == 0 ? 400 : 600));
            if (!alive->load())
                return;
            DumlPacket pkt;
            pkt.sender = DEV_APP;
            pkt.receiver = DEV_CAMERA;
            pkt.cmd_set = CMDSET_CAMERA;
            pkt.cmd_id = 0x02;
            pkt.seq = seqs[i];
            pkt.cmd_type = 0x40;
            pkt.payload = {0x01}; // 1 = START
            t->send(pkt.encode());
        }
    }).detach();
}

void Drone::stop_record() {
    cmd(CMDSET_CAMERA, 0x02, {0x00}, DEV_CAMERA);
}
void Drone::set_camera_mode(int mode) {
    cmd(CMDSET_CAMERA, 0x10, {static_cast<std::uint8_t>(mode & 0xFF)}, DEV_CAMERA);
}
void Drone::request_i_frame() {
    cmd(0x02, 0xB3, {}, DEV_CAMERA);
}

void Drone::start_liveview(int camera_source) {
    cmd(0x02, 0x09, {static_cast<std::uint8_t>(camera_source & 0xFF)}, DEV_CAMERA);
    Bytes cap{5};
    for (int codec = 0; codec < 5; ++codec) {
        cap.push_back(static_cast<std::uint8_t>(codec));
        put_u32(cap, codec == 0 ? 0xFFFFFFFFu : 0u);
    }
    cmd(0x08, 0x41, cap, DEV_DM368);
    Bytes fps;
    put_u16(fps, 30);
    fps.push_back(0x00);
    fps.push_back(0x00);
    cmd(0x08, 0x42, fps, DEV_DM368);
    cmd(0x08, 0x69, {0, 100, 0}, DEV_DM368);
    request_i_frame();
}

void Drone::set_zoom(double factor) {
    int z = std::max(0, std::min(0xFFFF, static_cast<int>(std::llround(factor * 100))));
    Bytes p{0x09, 0x00, 0x00, static_cast<std::uint8_t>(z & 0xFF),
            static_cast<std::uint8_t>((z >> 8) & 0xFF)};
    cmd(CMDSET_CAMERA, 0x34, p, DEV_CAMERA);
}

void Drone::set_exposure_mode(int mode) {
    cmd(CMDSET_CAMERA, 0x1E, {static_cast<std::uint8_t>(mode & 0xFF), 0x00}, DEV_CAMERA);
}

void Drone::set_iso_auto() {
    set_exposure_mode(1);
    shutter_denom_ = -1;
    cmd(CMDSET_CAMERA, 0x2A, {0}, DEV_CAMERA);
}

void Drone::set_iso(int iso) {
    static const std::map<int, int> kIsoIndex = {{0, 0},      {100, 3},   {200, 4},  {400, 5},
                                                 {800, 6},    {1600, 7},  {3200, 8}, {6400, 9},
                                                 {12800, 10}, {25600, 11}};
    set_exposure_mode(4);
    auto it = kIsoIndex.find(iso);
    const int idx = (it != kIsoIndex.end()) ? it->second : iso;
    cmd(CMDSET_CAMERA, 0x2A, {static_cast<std::uint8_t>(idx & 0x7F)}, DEV_CAMERA);
    if (shutter_denom_ < 0)
        set_shutter(30);
}

void Drone::set_shutter(int denom) {
    set_exposure_mode(4);
    shutter_denom_ = denom;
    const std::uint16_t integral = static_cast<std::uint16_t>((1 << 15) | (denom & 0x7FFF));
    Bytes p{0x01};
    put_u16(p, integral);
    p.push_back(0x00);
    cmd(CMDSET_CAMERA, 0x28, p, DEV_CAMERA);
}

void Drone::set_shutter_auto() {
    shutter_denom_ = -1;
    cmd(CMDSET_CAMERA, 0x28, {0, 0, 0, 0}, DEV_CAMERA);
}

void Drone::set_ev(int ev_thirds) {
    set_exposure_mode(1);
    shutter_denom_ = -1;
    const int val = 0x10 + ev_thirds * 3;
    cmd(CMDSET_CAMERA, 0x2E, {static_cast<std::uint8_t>(std::max(0, std::min(0xFF, val)))},
        DEV_CAMERA);
}

void Drone::set_white_balance(int mode, int ct_index) {
    cmd(CMDSET_CAMERA, 0x2C,
        {static_cast<std::uint8_t>(mode & 0xFF), static_cast<std::uint8_t>(ct_index & 0xFF), 0, 0,
         0},
        DEV_CAMERA);
}
void Drone::set_video_format(int resolution, int framerate, int fov) {
    cmd(CMDSET_CAMERA, 0x18,
        {static_cast<std::uint8_t>(resolution & 0xFF), static_cast<std::uint8_t>(framerate & 0xFF),
         static_cast<std::uint8_t>(fov & 0xFF), 0, 0},
        DEV_CAMERA);
}
void Drone::set_photo_mode(int code) {
    cmd(CMDSET_CAMERA, 0x6A, {static_cast<std::uint8_t>(code & 0xFF), 0, 0, 0, 0, 0}, DEV_CAMERA);
}
void Drone::set_video_codec(bool h265) {
    cmd(CMDSET_CAMERA, 0xAB, {static_cast<std::uint8_t>(h265 ? 1 : 0), 0}, DEV_CAMERA);
}

void Drone::send_raw(std::uint8_t cmd_set, std::uint8_t cmd_id, const Bytes& payload,
                     std::uint8_t receiver, bool ack) {
    cmd(cmd_set, cmd_id, payload, receiver, ack);
}

} // namespace djilink
