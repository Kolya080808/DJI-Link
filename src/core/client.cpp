#include "core/client.hpp"

#include "core/applog.hpp"
#include "core/param_hash.hpp"

#include <algorithm>
#include <cctype>
#include <cstdio>
#include <ctime>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace djilink {
namespace {

std::string hms() {
    const std::time_t t = std::time(nullptr);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[16];
    std::strftime(buf, sizeof(buf), "%H:%M:%S", &tm);
    return buf;
}

void log(const std::string& s) {
    std::printf("[%s] %s\n", hms().c_str(), s.c_str());
    std::fflush(stdout);
    applog::info(s);
}
void vlog(const std::string& s) {
    applog::debug(s);
}

// HEVC parameter-set NAL types: 32 VPS (0x40), 33 SPS (0x42), 34 PPS (0x44).
const std::map<int, const char*> kParamNals = {{0x40, "VPS"}, {0x42, "SPS"}, {0x44, "PPS"}};

// index of the next 00 00 01 start code at or after `from`, or npos.
std::size_t find_startcode(const Bytes& b, std::size_t from) {
    for (std::size_t i = from; i + 3 <= b.size(); ++i) {
        if (b[i] == 0 && b[i + 1] == 0 && b[i + 2] == 1)
            return i;
    }
    return std::string::npos;
}

int to_int(const std::string& s, int base = 10) {
    return static_cast<int>(std::stol(s, nullptr, base));
}

std::vector<std::string> split(const std::string& line) {
    std::vector<std::string> out;
    std::istringstream is(line);
    std::string tok;
    while (is >> tok)
        out.push_back(tok);
    return out;
}

} // namespace

Client::Client(std::unique_ptr<Transport> t, std::string mode, bool live)
    : transport_(std::move(t)), mode_(std::move(mode)), live_(live), drone_(transport_.get()),
      demux_([this](const Bytes& p) { on_duml_payload(p); },
             [this](const Bytes& p) { on_video_payload(p); },
             [this](std::uint16_t typ, const Bytes& p) { on_unit(typ, p); }) {}

Client::~Client() {
    close();
}

void Client::set_axes(const Sticks& a) {
    std::lock_guard<std::mutex> lk(axes_mu_);
    axes_ = a;
}
Sticks Client::axes() const {
    std::lock_guard<std::mutex> lk(axes_mu_);
    return axes_;
}
void Client::add_mouse_dx(double dx) {
    std::lock_guard<std::mutex> lk(axes_mu_);
    mouse_dx_ += dx;
}
double Client::take_mouse_dx() {
    std::lock_guard<std::mutex> lk(axes_mu_);
    const double d = mouse_dx_;
    mouse_dx_ = 0.0;
    return d;
}
void Client::set_msg(const std::string& s) {
    {
        std::lock_guard<std::mutex> lk(msg_mu_);
        msg_ = s;
    }
    std::printf("  %s\n", s.c_str());
    std::fflush(stdout);
    applog::info(s);
}
std::string Client::msg() const {
    std::lock_guard<std::mutex> lk(msg_mu_);
    return msg_;
}

void Client::start() {
    running_ = true;
    rx_ = std::thread([this] { rx_loop(); });
    sender_ = std::thread([this] { sender_loop(); });
    stats_ = std::thread([this] { stats_loop(); });
    std::thread([this] {
        std::this_thread::sleep_for(std::chrono::milliseconds(1500));
        static const char* names[] = {"g_config.flying_limit.max_height_0",
                                      "g_config.flying_limit.max_radius_0",
                                      "g_config.go_home.fixed_go_home_altitude_0"};
        for (const char* n : names) {
            if (!running_)
                return;
            drone_.read_param(n);
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }).detach();
}

void Client::start_video() {
    if (mode_ != "pi")
        return;
    try {
        drone_.start_liveview();
        log("[video] start_liveview sent");
    } catch (const std::exception& e) {
        log(std::string("[video] start_liveview failed: ") + e.what());
        return;
    }
    std::thread([this] {
        for (int i = 0; i < 5; ++i) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            if (!running_)
                return;
            drone_.request_i_frame();
        }
        log("[video] keyframe requested (K to ask again)");
    }).detach();
}

bool Client::flight_ok() {
    if (!live_) {
        set_msg("flight commands are blocked (not live)");
        return false;
    }
    if (!armed.load()) {
        set_msg("not ARMED — arm before takeoff");
        return false;
    }
    return true;
}

bool Client::airborne() {
    const auto& st = tele_.state();
    if (st.altitude_m && *st.altitude_m > 0.5)
        return true;
    return (st.is_flying && *st.is_flying) || (st.motors_on && *st.motors_on);
}

void Client::note_takeoff() {
    pending_auto_c_ = true;
    takeoff_t_ = std::chrono::steady_clock::now();
}
void Client::cancel_auto_c() {
    pending_auto_c_ = false;
}

void Client::close() {
    if (!running_.exchange(false))
        return;
    try {
        if (live_ && control.load()) {
            drone_.enable_virtual_stick(false);
            control.store(false);
        }
    } catch (...) {
    }
    drone_.stop();
    if (rx_.joinable())
        rx_.join();
    if (sender_.joinable())
        sender_.join();
    if (stats_.joinable())
        stats_.join();
    transport_->close();
}

std::string Client::stats() const {
    std::ostringstream os;
    os << "rx: duml=" << n_duml_ << " video_pl=" << n_video_ << " (" << (video_bytes_ / 1024)
       << "KB) frames=" << decoded_frames_.load();
    return os.str();
}

void Client::on_unit(std::uint16_t typ, const Bytes& payload) {
    if (typ == 0x574B) {
        std::string s(payload.begin(), payload.end());
        vlog("[rc-log] " + s);
    }
}

void Client::on_video_payload(const Bytes& pl) {
    ++n_video_;
    video_bytes_ += pl.size();
    if (n_video_ <= 3 || n_video_ % 200 == 0) {
        vlog("[video] payload #" + std::to_string(n_video_) + " " + std::to_string(pl.size()) +
             "B");
    }
    if (video_out_) {
        cache_param_sets(pl);
        video_out_->on_frame(pl, false);
    }
}

void Client::cache_param_sets(const Bytes& raw) {
    // NALs are split across payloads, so scan the join (tail + payload).
    Bytes buf = tail_;
    buf.insert(buf.end(), raw.begin(), raw.end());
    tail_.assign(buf.end() - static_cast<std::ptrdiff_t>(std::min<std::size_t>(16, buf.size())),
                 buf.end());
    std::size_t i = 0;
    while ((i = find_startcode(buf, i)) != std::string::npos) {
        const int nal = (i + 4 < buf.size()) ? buf[i + 3] : 0;
        if (kParamNals.count(nal)) {
            std::size_t j = find_startcode(buf, i + 3);
            std::size_t end = (j != std::string::npos) ? j : buf.size();
            param_sets_[nal] = Bytes(buf.begin() + static_cast<std::ptrdiff_t>(i),
                                     buf.begin() + static_cast<std::ptrdiff_t>(end));
        } else if ((nal >> 1) >= 16 && (nal >> 1) <= 21 && param_sets_.size() == 3) {
            // IRAP: prepend the cached parameter sets so the decoder can start.
            Bytes joined;
            for (auto& kv : param_sets_)
                joined.insert(joined.end(), kv.second.begin(), kv.second.end());
            if (video_out_)
                video_out_->on_frame(joined, true);
            if (!params_logged_) {
                params_logged_ = true;
                log("[video] IRAP + cached VPS/SPS/PPS injected — picture should start");
            }
        }
        i += 3;
    }
}

void Client::apply_limit(std::uint32_t rhash, double value) {
    static const struct {
        const char* name;
        int which;
    } kLimits[] = {{"g_config.flying_limit.max_height_0", 0},
                   {"g_config.flying_limit.max_radius_0", 1},
                   {"g_config.go_home.fixed_go_home_altitude_0", 2}};
    for (const auto& l : kLimits) {
        if (param_hash(l.name) == rhash) {
            auto& st = tele_.state();
            if (l.which == 0)
                st.max_height_m = value;
            else if (l.which == 1)
                st.max_distance_m = value;
            else
                st.rth_altitude_m = value;
            return;
        }
    }
}

void Client::on_duml_payload(const Bytes& payload) {
    for (const auto& p : duml_.feed(payload)) {
        ++n_duml_;
        if (p.cmd_set == 0x03 && p.cmd_id == 0xF8 && p.sender != 0x02 && p.payload.size() >= 7) {
            if (auto rhash = get_u32(p.payload, 1)) {
                if (auto v = get_u16(p.payload, 5))
                    apply_limit(*rhash, static_cast<double>(*v));
            }
        }
        tele_.feed_packet(p);
    }
}

void Client::rx_loop() {
    while (running_) {
        Bytes data;
        try {
            data = transport_->recv(300);
        } catch (const std::exception& e) {
            log(std::string("[rx] link closed: ") + e.what());
            break;
        }
        if (data.empty())
            continue;
        if (mode_ == "pi")
            demux_.feed(data);
        else
            on_duml_payload(data);
    }
}

void Client::sender_loop() {
    auto last_diag = std::chrono::steady_clock::now();
    while (running_) {
        if (live_ && armed.load() && auto_c.load() && pending_auto_c_ && !control.load() &&
            airborne()) {
            const double elapsed =
                std::chrono::duration<double>(std::chrono::steady_clock::now() - takeoff_t_)
                    .count();
            const auto& st = tele_.state();
            const bool in_takeoff =
                st.flight_mode && (*st.flight_mode == 10 || *st.flight_mode == 11);
            if (elapsed > 3.0 && (!in_takeoff || elapsed > 8.0)) {
                pending_auto_c_ = false;
                drone_.enable_virtual_stick(true);
                control.store(true);
                gs.store(true);
                set_msg("control auto-ON (takeoff settled)");
            }
        }
        if (live_ && armed.load() && control.load()) {
            // Fold in every mouse count accumulated since the last send, then clear it.
            Sticks a = axes();
            a.yaw = std::max(-1.0, std::min(1.0, a.yaw + take_mouse_dx() * kMouseYawSens));

            const auto now = std::chrono::steady_clock::now();
            if (std::chrono::duration<double>(now - last_diag).count() >= 1.0) {
                last_diag = now;
                const auto& st = tele_.state();
                std::string owner = st.ctrl_device ? sdk_ctrl_device_name(*st.ctrl_device) : "?";
                std::ostringstream os;
                os << "[stick] mode="
                   << (st.flight_mode_name ? *st.flight_mode_name : std::string("?"))
                   << " FC-owner=" << owner << " r/p/y/t=" << a.roll << "/" << a.pitch << "/"
                   << a.yaw << "/" << a.throttle;
                log(os.str());
            }
            try {
                if (stick_mobilerc.load())
                    drone_.set_sticks_mobilerc(a.roll, a.pitch, a.yaw, a.throttle);
                else
                    drone_.set_sticks_velocity(a.roll, a.pitch, a.yaw, a.throttle, stick_flag);
            } catch (...) {
            }
        } else {
            // Not sending: discard accumulated motion so enabling control later does not
            // apply a burst of yaw collected while the sticks were inactive.
            take_mouse_dx();
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
}

void Client::stats_loop() {
    while (running_) {
        for (int i = 0; i < 50 && running_; ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        if (!running_)
            break;
        log("[stats] " + stats() + "  | " + tele_.state().summary());
    }
}

// ---------------------------------------------------------------- console
void run_console_cmd(Client& cli, const std::string& line) {
    auto parts = split(line);
    if (parts.empty())
        return;
    std::string c = parts[0];
    std::transform(c.begin(), c.end(), c.begin(),
                   [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
    std::vector<std::string> a(parts.begin() + 1, parts.end());
    Drone& d = cli.drone();
    try {
        if (c == "arm") {
            if (!cli.live()) {
                cli.set_msg("ARM unavailable without a live link");
                return;
            }
            cli.armed.store(true);
            cli.set_msg("ARMED=1");
        } else if (c == "disarm") {
            cli.armed.store(false);
            cli.set_msg("ARMED=0");
        } else if (c == "readparam" || c == "rp" || c == "param") {
            if (a.empty()) {
                cli.set_msg("usage: rp <name|height|radius|tilt>");
                return;
            }
            static const std::map<std::string, std::string> aliases = {
                {"height", "g_config.flying_limit.max_height_0"},
                {"radius", "g_config.flying_limit.max_radius_0"},
                {"tilt", "g_config.mode_normal_cfg.tilt_atti_range_0"},
                {"speed", "g_config.mode_normal_cfg.tilt_atti_range_0"},
                {"gpsenable", "g_config.gps_cfg.gps_enable_0"},
                {"novice", "g_config.novice_cfg.max_height_0"}};
            auto it = aliases.find(a[0]);
            std::string name = it != aliases.end() ? it->second : a[0];
            d.read_param(name);
            cli.set_msg("read " + name);
        } else if ((c == "takeoff" || c == "to") && cli.flight_ok()) {
            d.takeoff();
            cli.note_takeoff();
            cli.set_msg("takeoff");
        } else if (c == "land") {
            d.land();
            cli.cancel_auto_c();
            if (cli.control.load()) {
                cli.control.store(false);
                cli.gs.store(false);
                d.enable_virtual_stick(false);
                cli.set_msg("land (control auto-OFF, returned to RC)");
            } else {
                cli.set_msg("land");
            }
        } else if (c == "rth" || c == "gohome") {
            d.return_to_home();
            cli.set_msg("RTH");
        } else if (c == "control") {
            bool want = !a.empty() && a[0] == "on";
            if (want && !cli.airborne()) {
                cli.set_msg("control on blocked: take off first");
            } else {
                cli.control.store(want);
                if (want)
                    d.request_control();
                else
                    d.release_control();
                cli.set_msg("control=" + std::string(want ? "1" : "0"));
            }
        } else if (c == "gs" || c == "groundstation") {
            bool on = !a.empty() && a[0] == "on";
            cli.gs.store(on);
            d.set_ground_station_mode(on);
            cli.set_msg("ground_station=" + std::string(on ? "1" : "0"));
        } else if (c == "gimbal") {
            if (!a.empty() && a[0] == "speed") {
                d.gimbal_speed(std::stod(a[1]));
                cli.set_msg("gimbal speed");
            } else {
                d.gimbal_angle(std::stod(a[0]));
                cli.set_msg("gimbal angle " + a[0]);
            }
        } else if (c == "recenter") {
            d.gimbal_recenter();
            cli.set_msg("gimbal recenter");
        } else if (c == "home") {
            if (!a.empty() && a[0] == "here") {
                d.set_home_to_current_location();
                cli.set_msg("home -> current location (needs GPS)");
            } else if (a.size() >= 2) {
                d.set_home_point(std::stod(a[0]), std::stod(a[1]));
                cli.set_msg("home -> " + a[0] + "," + a[1]);
            } else {
                cli.set_msg("usage: home here | home <lat> <lon>");
            }
        } else if (c == "setalt" || c == "maxalt") {
            d.set_max_altitude(to_int(a[0]));
            cli.set_msg("max alt " + a[0] + " m");
        } else if (c == "setdist" || c == "maxdist") {
            d.set_max_distance(to_int(a[0]));
            cli.set_msg("max dist " + a[0] + " m");
        } else if (c == "rthalt") {
            d.set_rth_altitude(to_int(a[0]));
            cli.set_msg("RTH alt " + a[0] + " m");
        } else if (c == "fmode" || c == "flightmode") {
            d.set_flight_mode(a[0]);
            cli.set_msg("flight mode " + a[0]);
        } else if (c == "hspeed" || c == "speed") {
            d.set_horizontal_speed(std::stod(a[0]));
            cli.set_msg("horiz speed ~" + a[0] + " m/s");
        } else if (c == "photo") {
            d.take_photo();
            cli.set_msg("photo");
        } else if (c == "rec") {
            if (!a.empty() && a[0] == "start")
                d.start_record();
            else
                d.stop_record();
            cli.set_msg("rec " + (a.empty() ? std::string() : a[0]));
        } else if (c == "zoom") {
            d.set_zoom(std::stod(a[0]));
            cli.set_msg("zoom " + a[0] + "x");
        } else if (c == "mode") {
            d.set_camera_mode(a[0] == "photo" ? 0 : 1);
            cli.set_msg("camera mode " + a[0]);
        } else if (c == "iso") {
            d.set_iso(to_int(a[0]));
            cli.set_msg("iso " + a[0]);
        } else if (c == "shutter") {
            if (!a.empty() && a[0] == "auto") {
                d.set_shutter_auto();
                cli.set_msg("shutter AUTO");
            } else {
                d.set_shutter(to_int(a[0]));
                cli.set_msg("shutter 1/" + a[0]);
            }
        } else if (c == "ev") {
            d.set_ev(to_int(a[0]));
            cli.set_msg("ev " + a[0]);
        } else if (c == "videofmt") {
            d.set_video_format(to_int(a[0]), to_int(a[1]));
            cli.set_msg("video format");
        } else if (c == "codec") {
            d.set_video_codec(!a.empty() && a[0] == "h265");
            cli.set_msg("codec");
        } else if (c == "keyframe" || c == "k") {
            d.request_i_frame();
            cli.set_msg("keyframe requested");
        } else if (c == "unlock" || c == "u") {
            d.unlock_no_gps(true);
            cli.set_msg("no-GPS takeoff unlock sent");
        } else if (c == "tele" || c == "status") {
            cli.set_msg(cli.tele().state().summary());
        } else if (c == "raw") {
            int cs = to_int(a[0], 0), cid = to_int(a[1], 0);
            Bytes pl;
            if (a.size() > 2 && a[2] != "-") {
                if (auto b = from_hex(a[2]))
                    pl = *b;
            }
            std::uint8_t recv = a.size() > 3 ? static_cast<std::uint8_t>(to_int(a[3], 0)) : DEV_FC;
            d.send_raw(static_cast<std::uint8_t>(cs), static_cast<std::uint8_t>(cid), pl, recv);
            cli.set_msg("raw sent");
        } else if (c == "help") {
            cli.set_msg(
                "arm disarm takeoff land rth control on|off gs on|off home here|<lat> <lon> "
                "setalt setdist rthalt rp gimbal <deg>|speed recenter photo rec start|stop zoom "
                "mode iso shutter ev videofmt codec keyframe unlock tele raw <set> <id> <hex>");
        } else if (c == "quit" || c == "exit") {
            cli.set_msg("quitting");
        } else {
            cli.set_msg("unknown command: " + c + " (help)");
        }
    } catch (const std::exception& e) {
        cli.set_msg(std::string("error: ") + e.what());
    }
}

} // namespace djilink
