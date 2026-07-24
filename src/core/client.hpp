// Client — the session object shared by the console entry and the GUI, factored
// out of pc_client.py's Client. Owns the transport, Drone, telemetry, the RX/TX/
// stats threads, the flight-arming/control state, and the HEVC parameter-set
// caching that keeps the video decoder fed. SDL-free on purpose: the GUI plugs a
// VideoOut in for the picture and drives the stick axes.
#pragma once

#include "core/bytes.hpp"
#include "core/composite.hpp"
#include "core/control.hpp"
#include "core/drone.hpp"
#include "core/duml.hpp"
#include "core/telemetry.hpp"
#include "core/transport.hpp"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace djilink {

// The GUI implements this to receive decoded-ready HEVC payloads (ffmpeg sink).
struct VideoOut {
    virtual ~VideoOut() = default;
    virtual void on_frame(const Bytes& frame, bool is_key) = 0;
};

class Client {
public:
    Client(std::unique_ptr<Transport> t, std::string mode, bool live);
    ~Client();

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

    // flags shared with the UI (atomics: touched from UI + sender threads)
    std::atomic<bool> armed{false};
    std::atomic<bool> control{false};
    std::atomic<bool> gs{false};
    std::atomic<bool> auto_c{true};
    std::atomic<bool> recording{false};
    std::atomic<bool> show_hud{true};
    std::atomic<bool> mouse_look{true};
    std::atomic<bool> stick_mobilerc{false};
    std::atomic<bool> return_to_menu{false};
    std::uint8_t stick_flag = 0x4A;

    void set_video_out(VideoOut* v) {
        video_out_ = v;
    }

    // stick axes, written by the GUI each frame, read by the sender loop.
    void set_axes(const Sticks& a);
    Sticks axes() const;

    // one-line status message for the HUD (thread-safe).
    void set_msg(const std::string& s);
    std::string msg() const;

    void start();
    void start_video();
    void close();

    bool flight_ok();
    bool airborne();
    void note_takeoff();
    void cancel_auto_c();

    // rx/stats counters (for the stats line + HUD)
    std::size_t n_duml() const {
        return n_duml_;
    }
    std::size_t n_video() const {
        return n_video_;
    }
    std::size_t video_bytes() const {
        return video_bytes_;
    }
    int decoded_frames() const {
        return decoded_frames_;
    }
    void bump_decoded() {
        ++decoded_frames_;
    }
    std::string stats() const;

private:
    void on_unit(std::uint16_t typ, const Bytes& payload);
    void on_video_payload(const Bytes& pl);
    void on_duml_payload(const Bytes& payload);
    void cache_param_sets(const Bytes& pl);
    void apply_limit(std::uint32_t rhash, double value);
    void rx_loop();
    void sender_loop();
    void stats_loop();

    std::unique_ptr<Transport> transport_;
    std::string mode_;
    bool live_;
    Drone drone_;
    Telemetry tele_;
    DumlStream duml_;
    CompositeDemux demux_;
    VideoOut* video_out_ = nullptr;

    std::atomic<bool> running_{false};
    std::thread rx_, sender_, stats_;
    std::size_t n_duml_ = 0, n_video_ = 0, video_bytes_ = 0;
    std::atomic<int> decoded_frames_{0};

    bool pending_auto_c_ = false;
    std::chrono::steady_clock::time_point takeoff_t_{};

    mutable std::mutex axes_mu_;
    Sticks axes_;
    mutable std::mutex msg_mu_;
    std::string msg_;

    // HEVC parameter-set caching (VPS/SPS/PPS re-injection before each IRAP).
    Bytes tail_;
    std::map<int, Bytes> param_sets_;
    bool params_logged_ = false;
};

// One console line -> command (flight/gimbal/camera/limits/home/raw), shared by the
// terminal entry and the in-flight console. Mirrors pc_client.run_console_cmd
// (media commands intentionally absent).
void run_console_cmd(Client& cli, const std::string& line);

} // namespace djilink
