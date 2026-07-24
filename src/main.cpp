// dji-link — PC client for the DJI Mavic Mini 1 (WM160), C++ port of
// dji_link_beta/pc_client.py.
//
// PHASE 1 (this file): the protocol core wired into a runnable CONSOLE client —
// connect (Pi / serial / sim), RX/TX threads, telemetry read-out, and the same
// console commands as pc_client.py's run_console_cmd (flight, gimbal, camera,
// limits, home, raw). The pygame window (video, HUD, settings panel, preflight
// menu, auto-updater button) is the next phase; see memory: cpp-client-goal.
//
// Excluded for now (project scope): all media, and all GPS parsing.

#include "core/applog.hpp"
#include "core/bytes.hpp"
#include "core/composite.hpp"
#include "core/drone.hpp"
#include "core/duml.hpp"
#include "core/netfind.hpp"
#include "core/param_hash.hpp"
#include "core/telemetry.hpp"
#include "core/transport.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <ctime>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

using namespace djilink;

namespace {

bool g_verbose = false;

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
    if (g_verbose) {
        std::printf("[%s] %s\n", hms().c_str(), s.c_str());
        std::fflush(stdout);
    }
    applog::debug(s);
}

// ---------------------------------------------------------------- Client
class Client {
public:
    Client(std::unique_ptr<Transport> t, std::string mode, bool live)
        : transport_(std::move(t)), mode_(std::move(mode)), live_(live), drone_(transport_.get()),
          demux_([this](const Bytes& p) { on_duml_payload(p); },
                 [this](const Bytes& p) { on_video_payload(p); },
                 [this](std::uint16_t typ, const Bytes& p) { on_unit(typ, p); }) {}

    ~Client() {
        close();
    }

    Drone& drone() {
        return drone_;
    }
    Telemetry& tele() {
        return tele_;
    }
    const std::string& mode() const {
        return mode_;
    }
    bool live() const {
        return live_;
    }

    // flags used by the console flight gate
    std::atomic<bool> armed{false};
    std::atomic<bool> control{false};
    std::atomic<bool> gs{false};
    std::atomic<bool> auto_c{true};
    std::atomic<bool> recording{false};
    std::uint8_t stick_flag = 0x4A;
    std::atomic<bool> stick_mobilerc{false};

    void start() {
        running_ = true;
        rx_ = std::thread([this] { rx_loop(); });
        sender_ = std::thread([this] { sender_loop(); });
        stats_ = std::thread([this] { stats_loop(); });
        // read the flight limits shortly after RX is up (no OSD push carries them here)
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

    void start_video() {
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
            log("[video] keyframe requested");
        }).detach();
    }

    bool flight_ok() {
        if (!live_) {
            log("flight commands are blocked (run with --dry off / not --sim)");
            return false;
        }
        if (!armed.load()) {
            log("not ARMED — type 'arm' before takeoff");
            return false;
        }
        return true;
    }

    bool airborne() {
        const auto& st = tele_.state();
        if (st.altitude_m && *st.altitude_m > 0.5)
            return true;
        return (st.is_flying && *st.is_flying) || (st.motors_on && *st.motors_on);
    }

    void note_takeoff() {
        pending_auto_c_ = true;
        takeoff_t_ = std::chrono::steady_clock::now();
    }
    void cancel_auto_c() {
        pending_auto_c_ = false;
    }

    void close() {
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

private:
    void on_unit(std::uint16_t typ, const Bytes& payload) {
        if (typ == 0x574B && g_verbose) {
            std::string s(payload.begin(), payload.end());
            log("[rc-log] " + s);
        }
    }

    void on_video_payload(const Bytes& pl) {
        ++n_video_;
        video_bytes_ += pl.size();
        if (n_video_ <= 3 || n_video_ % 200 == 0) {
            vlog("[video] payload #" + std::to_string(n_video_) + " " + std::to_string(pl.size()) +
                 "B");
        }
    }

    void on_duml_payload(const Bytes& payload) {
        for (const auto& p : duml_.feed(payload)) {
            ++n_duml_;
            vlog("[duml] rx sender=" + std::to_string(p.sender) +
                 " set=" + std::to_string(p.cmd_set) + " id=" + std::to_string(p.cmd_id) +
                 " len=" + std::to_string(p.payload.size()));
            // limit-param readback (0x03/0xF8): [ret u8][hash u32 LE][value]
            if (p.cmd_set == 0x03 && p.cmd_id == 0xF8 && p.sender != 0x02 &&
                p.payload.size() >= 7) {
                if (auto rhash = get_u32(p.payload, 1)) {
                    auto v = get_u16(p.payload, 5);
                    if (v)
                        apply_limit(*rhash, static_cast<double>(*v));
                }
            }
            tele_.feed_packet(p);
        }
    }

    void apply_limit(std::uint32_t rhash, double value) {
        // map the three known limit-param hashes onto the telemetry fields
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

    void rx_loop() {
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
            if (mode_ == "pi") {
                demux_.feed(data);
            } else {
                on_duml_payload(data);
            }
        }
    }

    void sender_loop() {
        auto last_diag = std::chrono::steady_clock::now();
        while (running_) {
            // auto-enable control once auto-takeoff has settled
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
                    log("control auto-ON (takeoff settled)");
                }
            }
            if (live_ && armed.load() && control.load()) {
                const auto now = std::chrono::steady_clock::now();
                if (std::chrono::duration<double>(now - last_diag).count() >= 1.0) {
                    last_diag = now;
                    const auto& st = tele_.state();
                    std::string owner =
                        st.ctrl_device ? sdk_ctrl_device_name(*st.ctrl_device) : "?";
                    log("[stick] mode=" +
                        (st.flight_mode_name ? *st.flight_mode_name : std::string("?")) +
                        " FC-owner=" + owner);
                }
                // console client has no key-hold; stream neutral sticks to hold authority
                try {
                    if (stick_mobilerc.load()) {
                        drone_.set_sticks_mobilerc(0, 0, 0, 0);
                    } else {
                        drone_.set_sticks_velocity(0, 0, 0, 0, stick_flag);
                    }
                } catch (...) {
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
    }

    void stats_loop() {
        while (running_) {
            for (int i = 0; i < 50 && running_; ++i)
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            if (!running_)
                break;
            std::ostringstream os;
            os << "[stats] rx: duml=" << n_duml_ << " video_pl=" << n_video_ << " ("
               << (video_bytes_ / 1024) << "KB)  | " << tele_.state().summary();
            log(os.str());
        }
    }

    std::unique_ptr<Transport> transport_;
    std::string mode_;
    bool live_;
    Drone drone_;
    Telemetry tele_;
    DumlStream duml_;
    CompositeDemux demux_;
    std::atomic<bool> running_{false};
    std::thread rx_, sender_, stats_;
    std::size_t n_duml_ = 0, n_video_ = 0, video_bytes_ = 0;
    bool pending_auto_c_ = false;
    std::chrono::steady_clock::time_point takeoff_t_{};
};

// ---------------------------------------------------------------- console
std::vector<std::string> split(const std::string& line) {
    std::vector<std::string> out;
    std::istringstream is(line);
    std::string tok;
    while (is >> tok)
        out.push_back(tok);
    return out;
}

int to_int(const std::string& s, int base = 10) {
    return static_cast<int>(std::stol(s, nullptr, base));
}

// One console line -> command, mirroring pc_client.run_console_cmd (flight/camera
// subset; media commands are intentionally absent).
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
                log("  ARM unavailable without a live link");
                return;
            }
            cli.armed.store(true);
            log("  ARMED=1");
        } else if (c == "disarm") {
            cli.armed.store(false);
            log("  ARMED=0");
        } else if (c == "readparam" || c == "rp" || c == "param") {
            if (a.empty()) {
                log("  usage: rp <name|height|radius|tilt>");
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
            log("  read " + name);
        } else if ((c == "takeoff" || c == "to") && cli.flight_ok()) {
            d.takeoff();
            cli.note_takeoff();
            log("  takeoff");
        } else if (c == "land") {
            d.land();
            cli.cancel_auto_c();
            if (cli.control.load()) {
                cli.control.store(false);
                cli.gs.store(false);
                d.enable_virtual_stick(false);
                log("  land (control auto-OFF, returned to RC)");
            } else {
                log("  land");
            }
        } else if (c == "rth" || c == "gohome") {
            d.return_to_home();
            log("  RTH");
        } else if (c == "control") {
            bool want = !a.empty() && a[0] == "on";
            if (want && !cli.airborne()) {
                log("  control on blocked: take off first");
            } else {
                cli.control.store(want);
                if (want)
                    d.request_control();
                else
                    d.release_control();
                log("  control=" + std::string(want ? "1" : "0"));
            }
        } else if (c == "gs" || c == "groundstation") {
            bool on = !a.empty() && a[0] == "on";
            cli.gs.store(on);
            d.set_ground_station_mode(on);
            log("  ground_station=" + std::string(on ? "1" : "0"));
        } else if (c == "gimbal") {
            if (!a.empty() && a[0] == "speed") {
                d.gimbal_speed(std::stod(a[1]));
                log("  gimbal speed");
            } else {
                d.gimbal_angle(std::stod(a[0]));
                log("  gimbal angle " + a[0]);
            }
        } else if (c == "recenter") {
            d.gimbal_recenter();
            log("  gimbal recenter");
        } else if (c == "home") {
            if (!a.empty() && a[0] == "here") {
                d.set_home_to_current_location();
                log("  home -> current location (needs GPS)");
            } else if (a.size() >= 2) {
                d.set_home_point(std::stod(a[0]), std::stod(a[1]));
                log("  home -> " + a[0] + "," + a[1]);
            } else {
                log("  usage: home here | home <lat> <lon>");
            }
        } else if (c == "setalt" || c == "maxalt") {
            d.set_max_altitude(to_int(a[0]));
            log("  max alt " + a[0] + " m");
        } else if (c == "setdist" || c == "maxdist") {
            d.set_max_distance(to_int(a[0]));
            log("  max dist " + a[0] + " m");
        } else if (c == "rthalt") {
            d.set_rth_altitude(to_int(a[0]));
            log("  RTH alt " + a[0] + " m");
        } else if (c == "fmode" || c == "flightmode") {
            d.set_flight_mode(a[0]);
            log("  flight mode " + a[0]);
        } else if (c == "hspeed" || c == "speed") {
            d.set_horizontal_speed(std::stod(a[0]));
            log("  horiz speed ~" + a[0] + " m/s");
        } else if (c == "photo") {
            d.take_photo();
            log("  photo");
        } else if (c == "rec") {
            if (!a.empty() && a[0] == "start")
                d.start_record();
            else
                d.stop_record();
            log("  rec " + (a.empty() ? std::string() : a[0]));
        } else if (c == "zoom") {
            d.set_zoom(std::stod(a[0]));
            log("  zoom " + a[0] + "x");
        } else if (c == "mode") {
            d.set_camera_mode(a[0] == "photo" ? 0 : 1);
            log("  camera mode " + a[0]);
        } else if (c == "iso") {
            d.set_iso(to_int(a[0]));
            log("  iso " + a[0]);
        } else if (c == "shutter") {
            if (!a.empty() && a[0] == "auto") {
                d.set_shutter_auto();
                log("  shutter AUTO");
            } else {
                d.set_shutter(to_int(a[0]));
                log("  shutter 1/" + a[0]);
            }
        } else if (c == "ev") {
            d.set_ev(to_int(a[0]));
            log("  ev " + a[0]);
        } else if (c == "videofmt") {
            d.set_video_format(to_int(a[0]), to_int(a[1]));
            log("  video format");
        } else if (c == "codec") {
            d.set_video_codec(!a.empty() && a[0] == "h265");
            log("  codec");
        } else if (c == "keyframe" || c == "k") {
            d.request_i_frame();
            log("  keyframe requested");
        } else if (c == "unlock" || c == "u") {
            d.unlock_no_gps(true);
            log("  no-GPS takeoff unlock sent");
        } else if (c == "tele" || c == "status") {
            log("  " + cli.tele().state().summary());
        } else if (c == "raw") {
            int cs = to_int(a[0], 0), cid = to_int(a[1], 0);
            Bytes pl;
            if (a.size() > 2 && a[2] != "-") {
                if (auto b = from_hex(a[2]))
                    pl = *b;
            }
            std::uint8_t recv = a.size() > 3 ? static_cast<std::uint8_t>(to_int(a[3], 0)) : DEV_FC;
            d.send_raw(static_cast<std::uint8_t>(cs), static_cast<std::uint8_t>(cid), pl, recv);
            log("  raw sent");
        } else if (c == "help") {
            log("  arm disarm takeoff land rth control on|off gs on|off home here|<lat> <lon> "
                "setalt <m> setdist <m> rthalt <m> rp height|radius gimbal <deg>|speed <dps> "
                "recenter photo rec start|stop zoom <x> mode photo|video iso <n> shutter <N>|auto "
                "ev <n> videofmt <r> <f> codec [h265] keyframe unlock tele raw <set> <id> <hex> "
                "[recv]  |  quit");
        } else if (c == "quit" || c == "exit") {
            log("  quitting");
        } else {
            log("  unknown command: " + c + " (help)");
        }
    } catch (const std::exception& e) {
        log(std::string("  error: ") + e.what());
    }
}

struct Args {
    std::string pi;
    std::string serial;
    bool sim = false;
    bool dry = false;
    bool verbose = false;
    bool no_video = false;
};

Args parse_args(int argc, char** argv) {
    Args a;
    for (int i = 1; i < argc; ++i) {
        std::string s = argv[i];
        auto next = [&]() -> std::string { return (i + 1 < argc) ? argv[++i] : std::string(); };
        if (s == "--pi")
            a.pi = next();
        else if (s == "--serial")
            a.serial = next();
        else if (s == "--sim")
            a.sim = true;
        else if (s == "--dry")
            a.dry = true;
        else if (s == "-v" || s == "--verbose")
            a.verbose = true;
        else if (s == "--no-video")
            a.no_video = true;
    }
    return a;
}

} // namespace

int main(int argc, char** argv) {
    Args args = parse_args(argc, argv);
    g_verbose = args.verbose;
    applog::setup(args.verbose);
    log("[log] logging to " + applog::latest_path());

    const bool base_live = !args.dry;

    std::unique_ptr<Transport> t;
    std::string mode;
    bool live = base_live;

    if (args.sim) {
        t = std::make_unique<LogTransport>(true);
        mode = "sim";
        live = false;
        log("[sim] loopback — commands are printed, no hardware");
    } else if (!args.serial.empty()) {
        try {
            t = std::make_unique<SerialTransport>(args.serial);
        } catch (const std::exception& e) {
            log(std::string("[serial] ") + e.what());
            return 2;
        }
        mode = "serial";
    } else {
        std::string host = args.pi;
        int port = 9910;
        if (host.empty()) {
            log("[pi] discovering the Pi on the LAN...");
            auto r = netfind::discover();
            if (!r.host) {
                log("[pi] not found. Pass --pi HOST[:PORT], or --serial PORT, or --sim.");
                return 2;
            }
            host = *r.host;
            log("[pi] found the Pi at " + host + " (via " + r.via + ")");
        } else if (auto pos = host.find(':'); pos != std::string::npos) {
            port = std::stoi(host.substr(pos + 1));
            host = host.substr(0, pos);
        }
        try {
            t = std::make_unique<CompositeTransport>(std::make_unique<NetTransport>(host, port));
        } catch (const std::exception& e) {
            log(std::string("[pi] ") + e.what());
            return 2;
        }
        mode = "pi";
    }

    Client cli(std::move(t), mode, live);
    // Our RC/AOA link is plaintext; keep FC config frames plaintext too (serial and
    // the current AOA path). See pc_client.py's encrypt_config note.
    cli.drone().encrypt_config = (mode == "pi") ? false : false;
    cli.start();
    if (!args.no_video)
        cli.start_video();

    log("Ready. Type 'help' for commands, 'quit' to exit.");
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty())
            continue;
        run_console_cmd(cli, line);
        auto parts = split(line);
        if (!parts.empty()) {
            std::string c = parts[0];
            std::transform(c.begin(), c.end(), c.begin(),
                           [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
            if (c == "quit" || c == "exit")
                break;
        }
    }

    cli.close();
    return 0;
}
