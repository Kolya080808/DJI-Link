#include "gui/gui.hpp"

#include "core/applog.hpp"
#include "core/client.hpp"
#include "core/ffmpeg.hpp"
#include "core/netfind.hpp"
#include "core/transport.hpp"
#include "core/updater.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <functional>
#include <future>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <SDL.h>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#else
#include <csignal>
#include <fcntl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace djilink::gui {
namespace {

constexpr int kWinW = 1100;
constexpr int kWinH = 720;
constexpr int kVideoW = 640;
constexpr int kVideoH = 360;
constexpr double kMouseYawSens = 0.030;
constexpr double kMouseGimbalSens = 0.15;

struct Color {
    std::uint8_t r, g, b, a = 255;
};

constexpr Color BG{18, 20, 26, 255};
constexpr Color PANEL{28, 31, 40, 240};
constexpr Color PANEL_HI{38, 42, 54, 255};
constexpr Color ACCENT{90, 160, 255, 255};
constexpr Color ACCENT_HI{120, 190, 255, 255};
constexpr Color TEXT{222, 228, 236, 255};
constexpr Color MUTED{140, 150, 165, 255};
constexpr Color GOOD{120, 220, 140, 255};
constexpr Color WARN{255, 180, 110, 255};
constexpr Color BAD{255, 120, 120, 255};

void set_color(SDL_Renderer* r, Color c) {
    SDL_SetRenderDrawColor(r, c.r, c.g, c.b, c.a);
}

void fill(SDL_Renderer* r, const SDL_Rect& rc, Color c) {
    set_color(r, c);
    SDL_RenderFillRect(r, &rc);
}

void outline(SDL_Renderer* r, const SDL_Rect& rc, Color c) {
    set_color(r, c);
    SDL_RenderDrawRect(r, &rc);
}

std::string upper(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    return s;
}

const char* const* glyph(char ch) {
    static const char* blank[] = {"00000", "00000", "00000", "00000", "00000", "00000", "00000"};
    static const char* unknown[] = {"11111", "10001", "00110", "00100", "00100", "00000", "00100"};
    static const std::map<char, std::array<const char*, 7>> table = {
        {'A', {"01110", "10001", "10001", "11111", "10001", "10001", "10001"}},
        {'B', {"11110", "10001", "10001", "11110", "10001", "10001", "11110"}},
        {'C', {"01111", "10000", "10000", "10000", "10000", "10000", "01111"}},
        {'D', {"11110", "10001", "10001", "10001", "10001", "10001", "11110"}},
        {'E', {"11111", "10000", "10000", "11110", "10000", "10000", "11111"}},
        {'F', {"11111", "10000", "10000", "11110", "10000", "10000", "10000"}},
        {'G', {"01111", "10000", "10000", "10011", "10001", "10001", "01110"}},
        {'H', {"10001", "10001", "10001", "11111", "10001", "10001", "10001"}},
        {'I', {"11111", "00100", "00100", "00100", "00100", "00100", "11111"}},
        {'J', {"00111", "00010", "00010", "00010", "10010", "10010", "01100"}},
        {'K', {"10001", "10010", "10100", "11000", "10100", "10010", "10001"}},
        {'L', {"10000", "10000", "10000", "10000", "10000", "10000", "11111"}},
        {'M', {"10001", "11011", "10101", "10101", "10001", "10001", "10001"}},
        {'N', {"10001", "11001", "10101", "10011", "10001", "10001", "10001"}},
        {'O', {"01110", "10001", "10001", "10001", "10001", "10001", "01110"}},
        {'P', {"11110", "10001", "10001", "11110", "10000", "10000", "10000"}},
        {'Q', {"01110", "10001", "10001", "10001", "10101", "10010", "01101"}},
        {'R', {"11110", "10001", "10001", "11110", "10100", "10010", "10001"}},
        {'S', {"01111", "10000", "10000", "01110", "00001", "00001", "11110"}},
        {'T', {"11111", "00100", "00100", "00100", "00100", "00100", "00100"}},
        {'U', {"10001", "10001", "10001", "10001", "10001", "10001", "01110"}},
        {'V', {"10001", "10001", "10001", "10001", "10001", "01010", "00100"}},
        {'W', {"10001", "10001", "10001", "10101", "10101", "10101", "01010"}},
        {'X', {"10001", "10001", "01010", "00100", "01010", "10001", "10001"}},
        {'Y', {"10001", "10001", "01010", "00100", "00100", "00100", "00100"}},
        {'Z', {"11111", "00001", "00010", "00100", "01000", "10000", "11111"}},
        {'0', {"01110", "10001", "10011", "10101", "11001", "10001", "01110"}},
        {'1', {"00100", "01100", "00100", "00100", "00100", "00100", "01110"}},
        {'2', {"01110", "10001", "00001", "00010", "00100", "01000", "11111"}},
        {'3', {"11110", "00001", "00001", "01110", "00001", "00001", "11110"}},
        {'4', {"00010", "00110", "01010", "10010", "11111", "00010", "00010"}},
        {'5', {"11111", "10000", "10000", "11110", "00001", "00001", "11110"}},
        {'6', {"01110", "10000", "10000", "11110", "10001", "10001", "01110"}},
        {'7', {"11111", "00001", "00010", "00100", "01000", "01000", "01000"}},
        {'8', {"01110", "10001", "10001", "01110", "10001", "10001", "01110"}},
        {'9', {"01110", "10001", "10001", "01111", "00001", "00001", "01110"}},
        {' ', {"00000", "00000", "00000", "00000", "00000", "00000", "00000"}},
        {'.', {"00000", "00000", "00000", "00000", "00000", "01100", "01100"}},
        {',', {"00000", "00000", "00000", "00000", "01100", "00100", "01000"}},
        {':', {"00000", "01100", "01100", "00000", "01100", "01100", "00000"}},
        {';', {"00000", "01100", "01100", "00000", "01100", "00100", "01000"}},
        {'-', {"00000", "00000", "00000", "11111", "00000", "00000", "00000"}},
        {'_', {"00000", "00000", "00000", "00000", "00000", "00000", "11111"}},
        {'+', {"00000", "00100", "00100", "11111", "00100", "00100", "00000"}},
        {'/', {"00001", "00010", "00010", "00100", "01000", "01000", "10000"}},
        {'\\', {"10000", "01000", "01000", "00100", "00010", "00010", "00001"}},
        {'|', {"00100", "00100", "00100", "00100", "00100", "00100", "00100"}},
        {'(', {"00010", "00100", "01000", "01000", "01000", "00100", "00010"}},
        {')', {"01000", "00100", "00010", "00010", "00010", "00100", "01000"}},
        {'[', {"01110", "01000", "01000", "01000", "01000", "01000", "01110"}},
        {']', {"01110", "00010", "00010", "00010", "00010", "00010", "01110"}},
        {'<', {"00010", "00100", "01000", "10000", "01000", "00100", "00010"}},
        {'>', {"01000", "00100", "00010", "00001", "00010", "00100", "01000"}},
        {'=', {"00000", "11111", "00000", "11111", "00000", "00000", "00000"}},
        {'?', {"01110", "10001", "00001", "00010", "00100", "00000", "00100"}},
        {'!', {"00100", "00100", "00100", "00100", "00100", "00000", "00100"}},
        {'%', {"11001", "11010", "00010", "00100", "01000", "01011", "10011"}},
        {'#', {"01010", "01010", "11111", "01010", "11111", "01010", "01010"}},
        {'*', {"00000", "10101", "01110", "11111", "01110", "10101", "00000"}},
        {'@', {"01110", "10001", "10111", "10101", "10111", "10000", "01110"}},
        {'\'', {"01100", "00100", "01000", "00000", "00000", "00000", "00000"}},
        {'\"', {"01010", "01010", "01010", "00000", "00000", "00000", "00000"}},
    };
    if (ch >= 'a' && ch <= 'z')
        ch = static_cast<char>(ch - 'a' + 'A');
    auto it = table.find(ch);
    if (it == table.end())
        return (ch == ' ') ? blank : unknown;
    return it->second.data();
}

void text(SDL_Renderer* r, int x, int y, const std::string& s, int scale, Color c) {
    set_color(r, c);
    int cx = x;
    const int char_w = 6 * scale;
    for (char ch : s) {
        if (ch == '\n') {
            y += 9 * scale;
            cx = x;
            continue;
        }
        const char* const* g = glyph(ch);
        for (int gy = 0; gy < 7; ++gy) {
            for (int gx = 0; gx < 5; ++gx) {
                if (g[gy][gx] == '1') {
                    SDL_Rect px{cx + gx * scale, y + gy * scale, scale, scale};
                    SDL_RenderFillRect(r, &px);
                }
            }
        }
        cx += char_w;
    }
}

void text_center(SDL_Renderer* r, const SDL_Rect& rc, const std::string& s, int scale, Color c) {
    const int w = static_cast<int>(s.size()) * 6 * scale - scale;
    const int h = 7 * scale;
    text(r, rc.x + (rc.w - w) / 2, rc.y + (rc.h - h) / 2, s, scale, c);
}

bool inside(const SDL_Rect& r, int x, int y) {
    return x >= r.x && y >= r.y && x < r.x + r.w && y < r.y + r.h;
}

struct Button {
    SDL_Rect rect{};
    std::string label;
    bool primary = false;
    bool enabled = true;
    std::function<void()> click;

    void draw(SDL_Renderer* r, int mx, int my) const {
        Color base = primary ? ACCENT : PANEL_HI;
        if (!enabled)
            base = PANEL;
        else if (inside(rect, mx, my))
            base = primary ? ACCENT_HI : Color{52, 57, 72, 255};
        fill(r, rect, base);
        outline(r, rect, primary ? ACCENT_HI : Color{70, 76, 92, 255});
        text_center(r, rect, upper(label), 2, primary ? Color{12, 16, 22, 255} : TEXT);
    }

    bool handle(const SDL_Event& e) const {
        if (!enabled || e.type != SDL_MOUSEBUTTONDOWN || e.button.button != SDL_BUTTON_LEFT)
            return false;
        if (!inside(rect, e.button.x, e.button.y))
            return false;
        if (click)
            click();
        return true;
    }
};

struct TextInput {
    SDL_Rect rect{};
    std::string value;
    std::string placeholder;
    bool focused = false;

    void draw(SDL_Renderer* r) const {
        fill(r, rect, Color{14, 16, 21, 255});
        outline(r, rect, focused ? ACCENT : PANEL_HI);
        std::string shown = value.empty() ? placeholder : value;
        if (focused && (SDL_GetTicks() / 500) % 2 == 0)
            shown += "|";
        text(r, rect.x + 10, rect.y + 12, shown.substr(0, 80), 2, value.empty() ? MUTED : TEXT);
    }

    bool handle(const SDL_Event& e) {
        if (e.type == SDL_MOUSEBUTTONDOWN && e.button.button == SDL_BUTTON_LEFT) {
            focused = inside(rect, e.button.x, e.button.y);
            return focused;
        }
        if (!focused)
            return false;
        if (e.type == SDL_TEXTINPUT) {
            value += e.text.text;
            return true;
        }
        if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_BACKSPACE) {
            if (!value.empty())
                value.pop_back();
            return true;
        }
        return false;
    }
};

struct ConnectionSpec {
    std::string mode;
    std::string host;
    int port = netfind::BRIDGE_PORT;
    std::string serial;
};

std::unique_ptr<Transport> make_transport(const ConnectionSpec& s) {
    if (s.mode == "sim")
        return std::make_unique<LogTransport>(true);
    if (s.mode == "serial")
        return std::make_unique<SerialTransport>(s.serial);
    return std::make_unique<CompositeTransport>(std::make_unique<NetTransport>(s.host, s.port));
}

struct ScreenBase {
    SDL_Window* win = nullptr;
    SDL_Renderer* ren = nullptr;
    bool done = false;
    int result = 0;
    int mx = 0, my = 0;

    void clear() {
        set_color(ren, BG);
        SDL_RenderClear(ren);
    }

    void mouse() {
        SDL_GetMouseState(&mx, &my);
    }
};

enum class MenuResult { Pi, Serial, Sim, Quit };

struct UpdateUi {
    enum class State { Idle, Checking, Ready, Error };
    std::atomic<State> state{State::Idle};
    std::string msg;
    std::string installer;
    std::mutex mu;
    std::thread worker;

    ~UpdateUi() {
        if (worker.joinable())
            worker.join();
    }

    void set(State s, std::string m) {
        std::lock_guard<std::mutex> lk(mu);
        state.store(s);
        msg = std::move(m);
    }

    std::string message() {
        std::lock_guard<std::mutex> lk(mu);
        return msg;
    }

    void check_async() {
        if (state.load() == State::Checking)
            return;
        if (worker.joinable())
            worker.join();
        set(State::Checking, "Checking GitHub releases...");
        worker = std::thread([this] {
            std::string err;
            auto rel = updater::check(err);
            if (!rel) {
                set(State::Error, err.empty() ? "No update found." : err);
                return;
            }
            auto local = updater::download(*rel, err);
            if (!local) {
                set(State::Error, err.empty() ? "Download failed." : err);
                return;
            }
            {
                std::lock_guard<std::mutex> lk(mu);
                installer = *local;
                msg = "Found new version: " + rel->tag + ". Downloaded installer. Start?";
            }
            state.store(State::Ready);
        });
    }
};

MenuResult menu_screen(SDL_Window* win, SDL_Renderer* r) {
    UpdateUi update;
    bool quit = false;
    MenuResult out = MenuResult::Quit;
    SDL_StartTextInput();
    while (!quit) {
        SDL_Event e;
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        const int bw = 390, bh = 52, gap = 14;
        const int cx = w / 2 - bw / 2;
        const int y0 = h / 2 - 135;
        std::vector<Button> buttons;
        buttons.push_back({{cx, y0, bw, bh}, "Connect via Raspberry Pi", true, true, [&] {
                               out = MenuResult::Pi;
                               quit = true;
                           }});
        buttons.push_back({{cx, y0 + (bh + gap), bw, bh}, "Connect via serial", false, true, [&] {
                               out = MenuResult::Serial;
                               quit = true;
                           }});
        buttons.push_back({{cx, y0 + 2 * (bh + gap), bw, bh}, "Simulator", false, true, [&] {
                               out = MenuResult::Sim;
                               quit = true;
                           }});
        buttons.push_back(
            {{cx, y0 + 3 * (bh + gap), bw, bh}, "Check for updates", false, true, [&] {
                 update.check_async();
             }});
        buttons.push_back({{cx, y0 + 4 * (bh + gap), bw, bh}, "Quit", false, true, [&] {
                               out = MenuResult::Quit;
                               quit = true;
                           }});

        std::vector<Button> update_buttons;
        if (update.state.load() == UpdateUi::State::Ready) {
            update_buttons.push_back({{cx, y0 + 5 * (bh + gap), 185, 44}, "Start", true, true, [&] {
                                          std::string err;
                                          std::string installer;
                                          {
                                              std::lock_guard<std::mutex> lk(update.mu);
                                              installer = update.installer;
                                          }
                                          if (updater::install_and_relaunch(installer, err)) {
                                              out = MenuResult::Quit;
                                              quit = true;
                                          } else {
                                              update.set(UpdateUi::State::Error, err);
                                          }
                                      }});
            update_buttons.push_back(
                {{cx + 205, y0 + 5 * (bh + gap), 185, 44}, "No", false, true, [&] {
                     std::string installer;
                     {
                         std::lock_guard<std::mutex> lk(update.mu);
                         installer = update.installer;
                     }
                     updater::discard(installer);
                     update.set(UpdateUi::State::Idle, "Update discarded.");
                 }});
        }

        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                out = MenuResult::Quit;
                quit = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
                out = MenuResult::Quit;
                quit = true;
            }
            for (const auto& b : buttons)
                if (b.handle(e))
                    break;
            for (const auto& b : update_buttons)
                if (b.handle(e))
                    break;
        }

        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 34, "DJI MAVIC MINI 1 - PC CONTROL", 3, TEXT);
        text(r, 40, 72, "Pick how to connect. Flight GUI is the default app path.", 2, MUTED);
        for (const auto& b : buttons)
            b.draw(r, mx, my);
        for (const auto& b : update_buttons)
            b.draw(r, mx, my);
        const std::string m = update.message();
        if (!m.empty()) {
            Color c = update.state.load() == UpdateUi::State::Error ? WARN : MUTED;
            text(r, 40, h - 76, m.substr(0, 130), 2, c);
        }
        text(r, 40, h - 40, "MEDIA AND GPS PARSING ARE INTENTIONALLY NOT IN THIS C++ PHASE.", 1,
             MUTED);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
    return out;
}

std::optional<ConnectionSpec> serial_screen(SDL_Window* win, SDL_Renderer* r,
                                            const std::string& def) {
    TextInput input{{40, 140, 560, 46}, def, "COM3 OR /DEV/TTYACM0", true};
    SDL_StartTextInput();
    bool done = false;
    std::optional<ConnectionSpec> result;
    while (!done) {
        SDL_Event e;
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        Button connect{{40, 210, 200, 46}, "Connect", true, true, [&] {
                           if (!input.value.empty())
                               result = ConnectionSpec{"serial", "", 0, input.value};
                           done = true;
                       }};
        Button back{{260, 210, 150, 46}, "Back", false, true, [&] { done = true; }};
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                result = ConnectionSpec{"quit"};
                done = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
                done = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_RETURN) {
                if (!input.value.empty())
                    result = ConnectionSpec{"serial", "", 0, input.value};
                done = true;
            }
            input.handle(e);
            connect.handle(e);
            back.handle(e);
        }
        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 40, "CONNECT VIA SERIAL", 3, TEXT);
        text(r, 40, 88, "Enter the RC/drone serial port.", 2, MUTED);
        input.draw(r);
        connect.draw(r, mx, my);
        back.draw(r, mx, my);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
    return result;
}

std::optional<ConnectionSpec> discovery_screen(SDL_Window* win, SDL_Renderer* r,
                                               const std::optional<std::string>& saved_host) {
    std::atomic<bool> scanning{true};
    netfind::DiscoverResult disc;
    std::thread worker([&] {
        disc = netfind::discover(saved_host);
        scanning.store(false);
    });
    bool done = false;
    std::optional<ConnectionSpec> result;
    while (!done) {
        SDL_Event e;
        int w, h;
        SDL_GetWindowSize(win, &w, &h);
        Button start{{w - 270, h - 110, 230, 48},
                     "Start flying",
                     true,
                     !scanning.load() && disc.host.has_value(),
                     [&] {
                         if (disc.host) {
                             result = ConnectionSpec{"pi", *disc.host, netfind::BRIDGE_PORT, ""};
                             done = true;
                         }
                     }};
        Button retry{{w - 270, h - 170, 230, 44}, "Retry", false, !scanning.load(), [&] {
                         if (worker.joinable())
                             worker.join();
                         scanning.store(true);
                         disc = {};
                         worker = std::thread([&] {
                             disc = netfind::discover(saved_host);
                             scanning.store(false);
                         });
                     }};
        Button back{{40, h - 110, 160, 44}, "Back", false, true, [&] { done = true; }};
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                result = ConnectionSpec{"quit"};
                done = true;
            } else if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
                done = true;
            }
            start.handle(e);
            retry.handle(e);
            back.handle(e);
        }
        int mx, my;
        SDL_GetMouseState(&mx, &my);
        set_color(r, BG);
        SDL_RenderClear(r);
        text(r, 40, 40, "FINDING THE RASPBERRY PI", 3, TEXT);
        if (scanning.load()) {
            text(r, 40, 96, "Looking on LAN, mDNS, AP gateway and local /24...", 2, ACCENT);
        } else if (disc.host) {
            text(r, 40, 96, "Found Pi at " + *disc.host + " via " + disc.via + ".", 2, GOOD);
            if (disc.needs_internet_prompt)
                text(r, 40, 132,
                     "Pi AP was joined. If it needs internet, configure Wi-Fi on the Pi.", 2, WARN);
            text(r, 40, 180,
                 "Now turn on RC, plug RC into Pi, power the drone, wait for link, then start.", 2,
                 MUTED);
        } else {
            text(r, 40, 96, "Pi not found. Power it on or pass --pi HOST[:PORT].", 2, WARN);
        }
        start.draw(r, mx, my);
        retry.draw(r, mx, my);
        back.draw(r, mx, my);
        auto lines = applog::tail();
        int y = h - 260;
        text(r, 40, y - 26, "LOG TAIL", 2, MUTED);
        for (auto it = lines.size() > 7 ? lines.end() - 7 : lines.begin(); it != lines.end();
             ++it) {
            text(r, 40, y, it->substr(0, 115), 1, MUTED);
            y += 16;
        }
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    if (worker.joinable())
        worker.join();
    return result;
}

std::optional<ConnectionSpec> preflight(SDL_Window* win, SDL_Renderer* r, const AppOptions& opt,
                                        const std::optional<std::string>& saved_host) {
    if (opt.sim)
        return ConnectionSpec{"sim"};
    if (!opt.serial.empty())
        return ConnectionSpec{"serial", "", 0, opt.serial};
    if (!opt.pi.empty()) {
        ConnectionSpec s{"pi", opt.pi, netfind::BRIDGE_PORT, ""};
        if (auto pos = s.host.find(':'); pos != std::string::npos) {
            s.port = std::stoi(s.host.substr(pos + 1));
            s.host = s.host.substr(0, pos);
        }
        return s;
    }
    while (true) {
        MenuResult m = menu_screen(win, r);
        if (m == MenuResult::Quit)
            return ConnectionSpec{"quit"};
        if (m == MenuResult::Sim)
            return ConnectionSpec{"sim"};
        if (m == MenuResult::Serial) {
            auto s = serial_screen(win, r, opt.serial);
            if (s && s->mode == "quit")
                return s;
            if (s)
                return s;
            continue;
        }
        if (m == MenuResult::Pi) {
            auto s = discovery_screen(win, r, saved_host);
            if (s && s->mode == "quit")
                return s;
            if (s)
                return s;
        }
    }
}

class FfmpegVideoSink final : public VideoOut {
public:
    explicit FfmpegVideoSink(Client* cli) : cli_(cli) {
        start();
    }
    ~FfmpegVideoSink() override {
        close();
    }

    void on_frame(const Bytes& frame, bool) override {
        if (!ok_.load())
            return;
#ifdef _WIN32
        DWORD written = 0;
        if (!WriteFile(in_write_, frame.data(), static_cast<DWORD>(frame.size()), &written,
                       nullptr))
            ok_.store(false);
#else
        const std::uint8_t* p = frame.data();
        std::size_t left = frame.size();
        while (left > 0) {
            ssize_t n = ::write(in_write_, p, left);
            if (n <= 0) {
                ok_.store(false);
                return;
            }
            p += n;
            left -= static_cast<std::size_t>(n);
        }
#endif
    }

    std::optional<Bytes> take_frame() {
        std::lock_guard<std::mutex> lk(mu_);
        if (latest_.empty())
            return std::nullopt;
        return latest_;
    }

    bool ok() const {
        return ok_.load();
    }

private:
    void start() {
        const std::string ffmpeg_exe = ffmpeg::executable();
#ifndef _WIN32
        std::signal(SIGPIPE, SIG_IGN);
#endif
        const std::vector<std::string> args = {ffmpeg_exe,
                                               "-hide_banner",
                                               "-loglevel",
                                               "error",
                                               "-fflags",
                                               "nobuffer",
                                               "-flags",
                                               "low_delay",
                                               "-flags2",
                                               "+showall",
                                               "-err_detect",
                                               "ignore_err",
                                               "-avioflags",
                                               "direct",
                                               "-fpsprobesize",
                                               "0",
                                               "-f",
                                               "hevc",
                                               "-i",
                                               "-",
                                               "-flush_packets",
                                               "1",
                                               "-fps_mode",
                                               "passthrough",
                                               "-vf",
                                               "scale=640:360",
                                               "-pix_fmt",
                                               "rgb24",
                                               "-f",
                                               "rawvideo",
                                               "-"};
#ifdef _WIN32
        SECURITY_ATTRIBUTES sa{};
        sa.nLength = sizeof(sa);
        sa.bInheritHandle = TRUE;
        HANDLE in_read = nullptr, out_write = nullptr;
        if (!CreatePipe(&in_read, &in_write_, &sa, 0) ||
            !CreatePipe(&out_read_, &out_write, &sa, 0)) {
            ok_.store(false);
            return;
        }
        SetHandleInformation(in_write_, HANDLE_FLAG_INHERIT, 0);
        SetHandleInformation(out_read_, HANDLE_FLAG_INHERIT, 0);
        std::string cmd;
        for (const auto& a : args) {
            if (!cmd.empty())
                cmd += ' ';
            cmd += '"' + a + '"';
        }
        STARTUPINFOA si{};
        si.cb = sizeof(si);
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = in_read;
        si.hStdOutput = out_write;
        si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
        std::vector<char> mut(cmd.begin(), cmd.end());
        mut.push_back('\0');
        if (!CreateProcessA(nullptr, mut.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                            nullptr, &si, &pi_)) {
            CloseHandle(in_read);
            CloseHandle(out_write);
            ok_.store(false);
            applog::info("[video] bundled ffmpeg failed to start - no picture");
            return;
        }
        CloseHandle(in_read);
        CloseHandle(out_write);
#else
        int inpipe[2] = {-1, -1}, outpipe[2] = {-1, -1};
        if (::pipe(inpipe) != 0 || ::pipe(outpipe) != 0) {
            ok_.store(false);
            return;
        }
        pid_ = ::fork();
        if (pid_ == 0) {
            ::dup2(inpipe[0], STDIN_FILENO);
            ::dup2(outpipe[1], STDOUT_FILENO);
            ::close(inpipe[0]);
            ::close(inpipe[1]);
            ::close(outpipe[0]);
            ::close(outpipe[1]);
            std::vector<char*> argv;
            for (const auto& a : args)
                argv.push_back(const_cast<char*>(a.c_str()));
            argv.push_back(nullptr);
            ::execvp(ffmpeg_exe.c_str(), argv.data());
            _exit(127);
        }
        ::close(inpipe[0]);
        ::close(outpipe[1]);
        in_write_ = inpipe[1];
        out_read_ = outpipe[0];
        if (pid_ < 0) {
            ok_.store(false);
            return;
        }
#endif
        ok_.store(true);
        reader_ = std::thread([this] { read_loop(); });
        applog::info("[video] decoder started");
    }

    void read_loop() {
        constexpr std::size_t n = kVideoW * kVideoH * 3;
        Bytes buf(n);
        while (ok_.load()) {
            std::size_t got = 0;
            while (got < n) {
#ifdef _WIN32
                DWORD rd = 0;
                if (!ReadFile(out_read_, buf.data() + got, static_cast<DWORD>(n - got), &rd,
                              nullptr) ||
                    rd == 0) {
                    ok_.store(false);
                    return;
                }
                got += rd;
#else
                ssize_t rd = ::read(out_read_, buf.data() + got, n - got);
                if (rd <= 0) {
                    ok_.store(false);
                    return;
                }
                got += static_cast<std::size_t>(rd);
#endif
            }
            {
                std::lock_guard<std::mutex> lk(mu_);
                latest_ = buf;
            }
            if (cli_)
                cli_->bump_decoded();
        }
    }

    void close() {
        ok_.store(false);
#ifdef _WIN32
        if (in_write_)
            CloseHandle(in_write_);
        if (out_read_)
            CloseHandle(out_read_);
        if (pi_.hProcess)
            TerminateProcess(pi_.hProcess, 0);
        if (reader_.joinable())
            reader_.join();
        if (pi_.hProcess)
            CloseHandle(pi_.hProcess);
        if (pi_.hThread)
            CloseHandle(pi_.hThread);
#else
        if (in_write_ >= 0)
            ::close(in_write_);
        if (out_read_ >= 0)
            ::close(out_read_);
        if (reader_.joinable())
            reader_.join();
        if (pid_ > 0) {
            ::kill(pid_, SIGTERM);
            int st = 0;
            ::waitpid(pid_, &st, 0);
        }
#endif
    }

    Client* cli_ = nullptr;
    std::atomic<bool> ok_{false};
    std::thread reader_;
    std::mutex mu_;
    Bytes latest_;
#ifdef _WIN32
    HANDLE in_write_ = nullptr;
    HANDLE out_read_ = nullptr;
    PROCESS_INFORMATION pi_{};
#else
    int in_write_ = -1;
    int out_read_ = -1;
    pid_t pid_ = -1;
#endif
};

struct Settings {
    bool open = false;
    int max_alt = 120;
    int max_dist = 500;
    int rth_alt = 30;
    int ev = 0;
    int iso_i = 0;
    int shutter_i = 0;
    int mode_i = 1;
    std::vector<int> isos{0, 100, 200, 400, 800, 1600, 3200};
    std::vector<int> shutters{0, 1000, 500, 250, 125, 60, 30, 15, 8, 4};

    void call(Client& cli, const std::function<void()>& fn, const std::string& msg) {
        try {
            fn();
            cli.set_msg(msg);
        } catch (const std::exception& e) {
            cli.set_msg(msg + ": " + e.what());
        }
    }

    void draw(SDL_Renderer* r, Client& cli, int sw, int sh, int mx, int my) {
        SDL_Rect p{std::max(24, (sw - 700) / 2), std::max(20, (sh - 560) / 2),
                   std::min(700, sw - 48), std::min(560, sh - 40)};
        fill(r, p, PANEL);
        outline(r, p, PANEL_HI);
        text(r, p.x + 24, p.y + 24, "FLIGHT SETTINGS", 3, TEXT);
        text(r, p.x + 24, p.y + p.h - 28, "ESC CLOSES. MEDIA IS NOT PORTED YET.", 1, MUTED);

        std::vector<Button> b;
        int y = p.y + 78;
        auto row = [&](const std::string& label, const std::string& val, auto minus, auto plus) {
            text(r, p.x + 26, y + 12, label, 2, TEXT);
            text(r, p.x + 395, y + 12, val, 2, ACCENT_HI);
            b.push_back({{p.x + p.w - 150, y, 46, 38}, "-", false, true, minus});
            b.push_back({{p.x + p.w - 92, y, 46, 38}, "+", false, true, plus});
            y += 48;
        };
        row(
            "MAX ALTITUDE", std::to_string(max_alt) + " M",
            [&] {
                max_alt = std::max(15, max_alt - 5);
                call(
                    cli, [&] { cli.drone().set_max_altitude(max_alt); },
                    "max alt " + std::to_string(max_alt) + " m");
            },
            [&] {
                max_alt = std::min(500, max_alt + 5);
                call(
                    cli, [&] { cli.drone().set_max_altitude(max_alt); },
                    "max alt " + std::to_string(max_alt) + " m");
            });
        row(
            "MAX DISTANCE", std::to_string(max_dist) + " M",
            [&] {
                max_dist = std::max(15, max_dist - 50);
                call(
                    cli, [&] { cli.drone().set_max_distance(max_dist); },
                    "max dist " + std::to_string(max_dist) + " m");
            },
            [&] {
                max_dist = std::min(5000, max_dist + 50);
                call(
                    cli, [&] { cli.drone().set_max_distance(max_dist); },
                    "max dist " + std::to_string(max_dist) + " m");
            });
        row(
            "RTH ALTITUDE", std::to_string(rth_alt) + " M",
            [&] {
                rth_alt = std::max(20, rth_alt - 5);
                call(
                    cli, [&] { cli.drone().set_rth_altitude(rth_alt); },
                    "RTH alt " + std::to_string(rth_alt) + " m");
            },
            [&] {
                rth_alt = std::min(500, rth_alt + 5);
                call(
                    cli, [&] { cli.drone().set_rth_altitude(rth_alt); },
                    "RTH alt " + std::to_string(rth_alt) + " m");
            });
        row(
            "EV", (ev > 0 ? "+" : "") + std::to_string(ev),
            [&] {
                ev = std::max(-3, ev - 1);
                call(cli, [&] { cli.drone().set_ev(ev); }, "EV " + std::to_string(ev));
            },
            [&] {
                ev = std::min(3, ev + 1);
                call(cli, [&] { cli.drone().set_ev(ev); }, "EV " + std::to_string(ev));
            });
        row(
            "ISO", isos[iso_i] == 0 ? "AUTO" : std::to_string(isos[iso_i]),
            [&] {
                iso_i = (iso_i + static_cast<int>(isos.size()) - 1) % static_cast<int>(isos.size());
                call(
                    cli,
                    [&] {
                        if (isos[iso_i] == 0)
                            cli.drone().set_iso_auto();
                        else
                            cli.drone().set_iso(isos[iso_i]);
                    },
                    isos[iso_i] == 0 ? "ISO auto" : "ISO " + std::to_string(isos[iso_i]));
            },
            [&] {
                iso_i = (iso_i + 1) % static_cast<int>(isos.size());
                call(
                    cli,
                    [&] {
                        if (isos[iso_i] == 0)
                            cli.drone().set_iso_auto();
                        else
                            cli.drone().set_iso(isos[iso_i]);
                    },
                    isos[iso_i] == 0 ? "ISO auto" : "ISO " + std::to_string(isos[iso_i]));
            });
        row(
            "SHUTTER",
            shutters[shutter_i] == 0 ? "AUTO" : "1/" + std::to_string(shutters[shutter_i]),
            [&] {
                shutter_i = (shutter_i + static_cast<int>(shutters.size()) - 1) %
                            static_cast<int>(shutters.size());
                call(
                    cli,
                    [&] {
                        if (shutters[shutter_i] == 0)
                            cli.drone().set_shutter_auto();
                        else
                            cli.drone().set_shutter(shutters[shutter_i]);
                    },
                    shutters[shutter_i] == 0 ? "shutter AUTO"
                                             : "shutter 1/" + std::to_string(shutters[shutter_i]));
            },
            [&] {
                shutter_i = (shutter_i + 1) % static_cast<int>(shutters.size());
                call(
                    cli,
                    [&] {
                        if (shutters[shutter_i] == 0)
                            cli.drone().set_shutter_auto();
                        else
                            cli.drone().set_shutter(shutters[shutter_i]);
                    },
                    shutters[shutter_i] == 0 ? "shutter AUTO"
                                             : "shutter 1/" + std::to_string(shutters[shutter_i]));
            });

        b.push_back({{p.x + 26, y + 8, 190, 42}, "Flight normal", false, true, [&] {
                         call(cli, [&] { cli.drone().set_flight_mode("normal"); }, "mode normal");
                     }});
        b.push_back({{p.x + 226, y + 8, 190, 42}, "Flight cinema", false, true, [&] {
                         call(cli, [&] { cli.drone().set_flight_mode("cinema"); }, "mode cinema");
                     }});
        b.push_back({{p.x + 426, y + 8, 190, 42}, "Flight sport", false, true, [&] {
                         call(cli, [&] { cli.drone().set_flight_mode("sport"); }, "mode sport");
                     }});
        y += 58;
        b.push_back({{p.x + 26, y, 190, 42}, "Recenter gimbal", false, true, [&] {
                         call(cli, [&] { cli.drone().gimbal_recenter(); }, "gimbal recenter");
                     }});
        b.push_back({{p.x + 226, y, 190, 42}, "Set home here", false, true, [&] {
                         call(cli, [&] { cli.drone().set_home_to_current_location(); }, "home set");
                     }});
        b.push_back({{p.x + 426, y, 190, 42}, "Exit to menu", false, true, [&] {
                         cli.return_to_menu.store(true);
                         cli.set_msg("returning to menu");
                     }});
        for (const auto& x : b)
            x.draw(r, mx, my);
    }

    bool handle(const SDL_Event& e, Client& cli, int sw, int sh) {
        if (!open)
            return false;
        if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE) {
            open = false;
            return true;
        }
        if (e.type != SDL_MOUSEBUTTONDOWN || e.button.button != SDL_BUTTON_LEFT)
            return true;
        int mx = e.button.x, my = e.button.y;
        SDL_Rect p{std::max(24, (sw - 700) / 2), std::max(20, (sh - 560) / 2),
                   std::min(700, sw - 48), std::min(560, sh - 40)};
        int y = p.y + 78;
        auto in_minus = [&](int row) {
            SDL_Rect rc{p.x + p.w - 150, p.y + 78 + row * 48, 46, 38};
            return inside(rc, mx, my);
        };
        auto in_plus = [&](int row) {
            SDL_Rect rc{p.x + p.w - 92, p.y + 78 + row * 48, 46, 38};
            return inside(rc, mx, my);
        };
        auto row_action = [&](int row, auto minus, auto plus) {
            if (in_minus(row)) {
                minus();
                return true;
            }
            if (in_plus(row)) {
                plus();
                return true;
            }
            return false;
        };
        if (row_action(
                0,
                [&] {
                    max_alt = std::max(15, max_alt - 5);
                    cli.drone().set_max_altitude(max_alt);
                    cli.set_msg("max alt " + std::to_string(max_alt) + " m");
                },
                [&] {
                    max_alt = std::min(500, max_alt + 5);
                    cli.drone().set_max_altitude(max_alt);
                    cli.set_msg("max alt " + std::to_string(max_alt) + " m");
                }))
            return true;
        if (row_action(
                1,
                [&] {
                    max_dist = std::max(15, max_dist - 50);
                    cli.drone().set_max_distance(max_dist);
                    cli.set_msg("max dist " + std::to_string(max_dist) + " m");
                },
                [&] {
                    max_dist = std::min(5000, max_dist + 50);
                    cli.drone().set_max_distance(max_dist);
                    cli.set_msg("max dist " + std::to_string(max_dist) + " m");
                }))
            return true;
        if (row_action(
                2,
                [&] {
                    rth_alt = std::max(20, rth_alt - 5);
                    cli.drone().set_rth_altitude(rth_alt);
                    cli.set_msg("RTH alt " + std::to_string(rth_alt) + " m");
                },
                [&] {
                    rth_alt = std::min(500, rth_alt + 5);
                    cli.drone().set_rth_altitude(rth_alt);
                    cli.set_msg("RTH alt " + std::to_string(rth_alt) + " m");
                }))
            return true;
        if (row_action(
                3,
                [&] {
                    ev = std::max(-3, ev - 1);
                    cli.drone().set_ev(ev);
                    cli.set_msg("EV " + std::to_string(ev));
                },
                [&] {
                    ev = std::min(3, ev + 1);
                    cli.drone().set_ev(ev);
                    cli.set_msg("EV " + std::to_string(ev));
                }))
            return true;
        if (row_action(
                4,
                [&] {
                    iso_i =
                        (iso_i + static_cast<int>(isos.size()) - 1) % static_cast<int>(isos.size());
                    if (isos[iso_i] == 0)
                        cli.drone().set_iso_auto();
                    else
                        cli.drone().set_iso(isos[iso_i]);
                    cli.set_msg(isos[iso_i] == 0 ? "ISO auto"
                                                 : "ISO " + std::to_string(isos[iso_i]));
                },
                [&] {
                    iso_i = (iso_i + 1) % static_cast<int>(isos.size());
                    if (isos[iso_i] == 0)
                        cli.drone().set_iso_auto();
                    else
                        cli.drone().set_iso(isos[iso_i]);
                    cli.set_msg(isos[iso_i] == 0 ? "ISO auto"
                                                 : "ISO " + std::to_string(isos[iso_i]));
                }))
            return true;
        if (row_action(
                5,
                [&] {
                    shutter_i = (shutter_i + static_cast<int>(shutters.size()) - 1) %
                                static_cast<int>(shutters.size());
                    if (shutters[shutter_i] == 0)
                        cli.drone().set_shutter_auto();
                    else
                        cli.drone().set_shutter(shutters[shutter_i]);
                    cli.set_msg(shutters[shutter_i] == 0
                                    ? "shutter AUTO"
                                    : "shutter 1/" + std::to_string(shutters[shutter_i]));
                },
                [&] {
                    shutter_i = (shutter_i + 1) % static_cast<int>(shutters.size());
                    if (shutters[shutter_i] == 0)
                        cli.drone().set_shutter_auto();
                    else
                        cli.drone().set_shutter(shutters[shutter_i]);
                    cli.set_msg(shutters[shutter_i] == 0
                                    ? "shutter AUTO"
                                    : "shutter 1/" + std::to_string(shutters[shutter_i]));
                }))
            return true;
        y += 6 * 48 + 8;
        struct Click {
            SDL_Rect r;
            std::function<void()> fn;
        };
        std::vector<Click> clicks = {
            {{p.x + 26, y, 190, 42},
             [&] {
                 cli.drone().set_flight_mode("normal");
                 cli.set_msg("mode normal");
             }},
            {{p.x + 226, y, 190, 42},
             [&] {
                 cli.drone().set_flight_mode("cinema");
                 cli.set_msg("mode cinema");
             }},
            {{p.x + 426, y, 190, 42},
             [&] {
                 cli.drone().set_flight_mode("sport");
                 cli.set_msg("mode sport");
             }},
            {{p.x + 26, y + 58, 190, 42},
             [&] {
                 cli.drone().gimbal_recenter();
                 cli.set_msg("gimbal recenter");
             }},
            {{p.x + 226, y + 58, 190, 42},
             [&] {
                 cli.drone().set_home_to_current_location();
                 cli.set_msg("home set");
             }},
            {{p.x + 426, y + 58, 190, 42}, [&] { cli.return_to_menu.store(true); }},
        };
        for (const auto& c : clicks) {
            if (inside(c.r, mx, my)) {
                c.fn();
                return true;
            }
        }
        return true;
    }
};

void draw_hud(SDL_Renderer* r, Client& cli, int sw, int sh) {
    if (!cli.show_hud.load())
        return;
    const auto& st = cli.tele().state();
    SDL_Rect card{16, sh - 190, std::min(sw - 32, 760), 174};
    fill(r, card, Color{18, 20, 26, 210});
    outline(r, card, Color{48, 53, 66, 255});
    int y = card.y + 16;
    text(r, card.x + 16, y, "MODE " + cli.mode() + "  " + cli.stats(), 2, MUTED);
    y += 24;
    std::ostringstream flags;
    flags << "ARM " << (cli.armed.load() ? "ON" : "OFF") << "  CONTROL "
          << (cli.control.load() ? "ON" : "OFF") << "  GS " << (cli.gs.load() ? "ON" : "OFF");
    text(r, card.x + 16, y, flags.str(), 2, cli.armed.load() ? GOOD : WARN);
    y += 24;
    text(r, card.x + 16, y, st.summary().substr(0, 120), 1, TEXT);
    y += 20;
    if (st.max_height_m || st.max_distance_m || st.rth_altitude_m) {
        std::ostringstream os;
        os << "LIMITS ALT="
           << (st.max_height_m ? std::to_string(static_cast<int>(*st.max_height_m)) : "?")
           << "M DIST="
           << (st.max_distance_m ? std::to_string(static_cast<int>(*st.max_distance_m)) : "?")
           << "M RTH="
           << (st.rth_altitude_m ? std::to_string(static_cast<int>(*st.rth_altitude_m)) : "?")
           << "M";
        text(r, card.x + 16, y, os.str(), 1, MUTED);
        y += 18;
    }
    std::string msg = cli.msg();
    if (!msg.empty())
        text(r, card.x + 16, y, msg.substr(0, 120), 1, WARN);
    text(r, card.x + 16, card.y + card.h - 20,
         "F1 HELP  ESC SETTINGS  TAB CONSOLE  F3 HUD  F11 FULLSCREEN", 1, MUTED);
}

void draw_help(SDL_Renderer* r, int sw, int sh) {
    SDL_Rect p{std::max(20, (sw - 760) / 2), std::max(20, (sh - 470) / 2), std::min(760, sw - 40),
               std::min(470, sh - 40)};
    fill(r, p, PANEL);
    outline(r, p, PANEL_HI);
    text(r, p.x + 24, p.y + 24, "CONTROLS", 3, TEXT);
    std::vector<std::string> lines = {
        "W/S PITCH  A/D ROLL  SPACE/SHIFT THROTTLE  Q/E YAW",
        "MOUSE X YAW  MOUSE Y GIMBAL  [ ] OR UP/DOWN GIMBAL",
        "ENTER ARM/DISARM  T TAKEOFF  C CONTROL  L LAND  H RTH",
        "V GROUND STATION  N RECENTER  P PHOTO  R RECORD TOGGLE",
        "K KEYFRAME  U NO-GPS UNLOCK  M MOBILE-RC STICKS  G STICK FLAG",
        "TAB CONSOLE COMMANDS  ESC SETTINGS  F3 HUD  F11 FULLSCREEN",
        "MEDIA AND GPS PARSING ARE NOT PORTED IN THIS PHASE.",
        "ESC OR F1 CLOSES THIS HELP.",
    };
    int y = p.y + 82;
    for (const auto& l : lines) {
        text(r, p.x + 24, y, l, 2, y == p.y + 82 ? ACCENT_HI : TEXT);
        y += 34;
    }
}

void draw_console(SDL_Renderer* r, int sw, int sh, const std::string& buf) {
    SDL_Rect p{18, sh - 74, sw - 36, 56};
    fill(r, p, Color{8, 10, 14, 235});
    outline(r, p, ACCENT);
    text(r, p.x + 14, p.y + 18, "> " + buf + "_", 2, TEXT);
}

int flight_screen(SDL_Window* win, SDL_Renderer* r, const ConnectionSpec& spec,
                  const AppOptions& opt) {
    std::unique_ptr<Transport> t;
    try {
        t = make_transport(spec);
    } catch (const std::exception& e) {
        applog::info(std::string("[connect] ") + e.what());
        return 1;
    }
    const bool live = spec.mode != "sim" && !opt.dry;
    Client cli(std::move(t), spec.mode, live);
    cli.drone().encrypt_config = false;
    std::unique_ptr<FfmpegVideoSink> video;
    if (spec.mode == "pi" && !opt.no_video) {
        video = std::make_unique<FfmpegVideoSink>(&cli);
        cli.set_video_out(video.get());
    }
    cli.start();
    if (spec.mode == "pi" && !opt.no_video)
        cli.start_video();

    bool running = true, help = false, console = false, fullscreen = !opt.windowed;
    std::string cbuf;
    Settings settings;
    SDL_Texture* tex =
        SDL_CreateTexture(r, SDL_PIXELFORMAT_RGB24, SDL_TEXTUREACCESS_STREAMING, kVideoW, kVideoH);
    std::set<std::string> held;
    double gimbal_pitch = 0.0;
    double frame_dx = 0.0;
    auto set_grab = [&](bool on) {
        SDL_SetWindowGrab(win, on ? SDL_TRUE : SDL_FALSE);
        SDL_SetRelativeMouseMode(on ? SDL_TRUE : SDL_FALSE);
    };
    set_grab(true);
    SDL_StartTextInput();
    while (running && !cli.return_to_menu.load()) {
        SDL_Event e;
        int sw, sh;
        SDL_GetWindowSize(win, &sw, &sh);
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) {
                running = false;
            } else if (help) {
                if (e.type == SDL_KEYDOWN &&
                    (e.key.keysym.sym == SDLK_ESCAPE || e.key.keysym.sym == SDLK_F1)) {
                    help = false;
                    set_grab(!console && !settings.open);
                }
            } else if (settings.open) {
                settings.handle(e, cli, sw, sh);
                if (!settings.open)
                    set_grab(!console);
            } else if (console) {
                if (e.type == SDL_TEXTINPUT) {
                    cbuf += e.text.text;
                } else if (e.type == SDL_KEYDOWN) {
                    if (e.key.keysym.sym == SDLK_RETURN) {
                        run_console_cmd(cli, cbuf);
                        std::string cmd = cbuf.substr(0, cbuf.find(' '));
                        cbuf.clear();
                        if (cmd == "quit" || cmd == "exit")
                            running = false;
                    } else if (e.key.keysym.sym == SDLK_ESCAPE) {
                        console = false;
                        set_grab(true);
                    } else if (e.key.keysym.sym == SDLK_BACKSPACE) {
                        if (!cbuf.empty())
                            cbuf.pop_back();
                    }
                }
            } else if (e.type == SDL_MOUSEMOTION && SDL_GetRelativeMouseMode()) {
                frame_dx += e.motion.xrel;
                gimbal_pitch = std::max(
                    -90.0, std::min(30.0, gimbal_pitch - e.motion.yrel * kMouseGimbalSens));
                try {
                    cli.drone().gimbal_angle(gimbal_pitch);
                } catch (...) {
                }
            } else if (e.type == SDL_KEYDOWN && !e.key.repeat) {
                const SDL_Keycode k = e.key.keysym.sym;
                if (k == SDLK_ESCAPE) {
                    settings.open = true;
                    set_grab(false);
                } else if (k == SDLK_F1) {
                    help = true;
                    set_grab(false);
                } else if (k == SDLK_F3) {
                    cli.show_hud.store(!cli.show_hud.load());
                    cli.set_msg(cli.show_hud.load() ? "HUD shown" : "HUD hidden - F3 to show");
                } else if (k == SDLK_F11) {
                    fullscreen = !fullscreen;
                    SDL_SetWindowFullscreen(win, fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : 0);
                } else if (k == SDLK_TAB) {
                    console = true;
                    cbuf.clear();
                    set_grab(false);
                } else if (k == SDLK_RETURN) {
                    cli.armed.store(!cli.armed.load());
                    cli.set_msg(cli.armed.load() ? "ARMED=1" : "ARMED=0");
                } else if (k == SDLK_t && cli.flight_ok()) {
                    cli.drone().takeoff();
                    cli.note_takeoff();
                    cli.set_msg("takeoff");
                } else if (k == SDLK_l) {
                    cli.drone().land();
                    cli.cancel_auto_c();
                    cli.set_msg("land");
                } else if (k == SDLK_h) {
                    cli.drone().return_to_home();
                    cli.set_msg("RTH (emergency)");
                } else if (k == SDLK_c) {
                    bool want = !cli.control.load();
                    if (want && !cli.airborne()) {
                        cli.set_msg("control on blocked: take off first");
                    } else {
                        cli.control.store(want);
                        if (want)
                            cli.drone().request_control();
                        else
                            cli.drone().release_control();
                        cli.set_msg(std::string("control=") + (want ? "1" : "0"));
                    }
                } else if (k == SDLK_v) {
                    bool on = !cli.gs.load();
                    cli.gs.store(on);
                    cli.drone().set_ground_station_mode(on);
                    cli.set_msg(std::string("ground_station=") + (on ? "1" : "0"));
                } else if (k == SDLK_n) {
                    cli.drone().gimbal_recenter();
                    cli.set_msg("gimbal recenter");
                } else if (k == SDLK_p) {
                    cli.drone().take_photo();
                    cli.set_msg("photo");
                } else if (k == SDLK_r) {
                    bool rec = !cli.recording.load();
                    if (rec)
                        cli.drone().start_record();
                    else
                        cli.drone().stop_record();
                    cli.recording.store(rec);
                    cli.set_msg(rec ? "rec start" : "rec stop");
                } else if (k == SDLK_k) {
                    cli.drone().request_i_frame();
                    cli.set_msg("keyframe requested");
                } else if (k == SDLK_u) {
                    cli.drone().unlock_no_gps(true);
                    cli.set_msg("no-GPS takeoff unlock sent");
                } else if (k == SDLK_m) {
                    cli.stick_mobilerc.store(!cli.stick_mobilerc.load());
                    cli.set_msg(cli.stick_mobilerc.load() ? "stick mobile-RC" : "stick flyc");
                } else if (k == SDLK_g) {
                    static const std::vector<std::uint8_t> flags{0x4A, 0x48, 0x0A, 0x08};
                    auto it = std::find(flags.begin(), flags.end(), cli.stick_flag);
                    std::size_t idx =
                        it == flags.end() ? 0 : (it - flags.begin() + 1) % flags.size();
                    cli.stick_flag = flags[idx];
                    std::ostringstream os;
                    os << "stick flag 0x" << std::hex << static_cast<int>(cli.stick_flag);
                    cli.set_msg(os.str());
                }
            }
        }

        const Uint8* keys = SDL_GetKeyboardState(nullptr);
        held.clear();
        if (keys[SDL_SCANCODE_W])
            held.insert("w");
        if (keys[SDL_SCANCODE_A])
            held.insert("a");
        if (keys[SDL_SCANCODE_S])
            held.insert("s");
        if (keys[SDL_SCANCODE_D])
            held.insert("d");
        if (keys[SDL_SCANCODE_Q])
            held.insert("q");
        if (keys[SDL_SCANCODE_E])
            held.insert("e");
        if (keys[SDL_SCANCODE_SPACE])
            held.insert("space");
        if (keys[SDL_SCANCODE_LSHIFT] || keys[SDL_SCANCODE_RSHIFT])
            held.insert("shift");
        Sticks axes = keys_to_sticks(held);
        axes.yaw = std::max(-1.0, std::min(1.0, axes.yaw + frame_dx * kMouseYawSens));
        frame_dx = 0.0;
        cli.set_axes(axes);
        if (keys[SDL_SCANCODE_RIGHTBRACKET] || keys[SDL_SCANCODE_UP]) {
            gimbal_pitch = std::min(30.0, gimbal_pitch + 1.5);
            try {
                cli.drone().gimbal_angle(gimbal_pitch);
            } catch (...) {
            }
        }
        if (keys[SDL_SCANCODE_LEFTBRACKET] || keys[SDL_SCANCODE_DOWN]) {
            gimbal_pitch = std::max(-90.0, gimbal_pitch - 1.5);
            try {
                cli.drone().gimbal_angle(gimbal_pitch);
            } catch (...) {
            }
        }

        set_color(r, Color{9, 11, 16, 255});
        SDL_RenderClear(r);
        if (video && tex) {
            if (auto f = video->take_frame())
                SDL_UpdateTexture(tex, nullptr, f->data(), kVideoW * 3);
            double k =
                std::min(sw / static_cast<double>(kVideoW), sh / static_cast<double>(kVideoH));
            SDL_Rect dst{(sw - static_cast<int>(kVideoW * k)) / 2,
                         (sh - static_cast<int>(kVideoH * k)) / 2, static_cast<int>(kVideoW * k),
                         static_cast<int>(kVideoH * k)};
            SDL_RenderCopy(r, tex, nullptr, &dst);
        } else {
            text(r, sw / 2 - 190, sh / 2 - 16,
                 spec.mode == "pi" ? "VIDEO DISABLED OR FFMPEG MISSING" : "NO VIDEO IN THIS MODE",
                 2, MUTED);
        }
        draw_hud(r, cli, sw, sh);
        if (settings.open) {
            int mx = 0, my = 0;
            SDL_GetMouseState(&mx, &my);
            settings.draw(r, cli, sw, sh, mx, my);
        }
        if (help)
            draw_help(r, sw, sh);
        if (console)
            draw_console(r, sw, sh, cbuf);
        SDL_RenderPresent(r);
        SDL_Delay(16);
    }
    SDL_StopTextInput();
    set_grab(false);
    if (tex)
        SDL_DestroyTexture(tex);
    cli.close();
    return running ? 0 : 1;
}

} // namespace

int run_app(const AppOptions& opt) {
    // We link without SDL2main and define SDL_MAIN_HANDLED, so SDL's own entry-point
    // shim never runs — announce that main() is ready before touching any SDL API.
    SDL_SetMainReady();
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS | SDL_INIT_TIMER) != 0) {
        std::fprintf(stderr, "SDL_Init failed: %s\n", SDL_GetError());
        return 2;
    }
    Uint32 flags = SDL_WINDOW_RESIZABLE;
    if (!opt.windowed)
        flags |= SDL_WINDOW_FULLSCREEN_DESKTOP;
    SDL_Window* win = SDL_CreateWindow("DJI Mavic Mini 1 - PC control", SDL_WINDOWPOS_CENTERED,
                                       SDL_WINDOWPOS_CENTERED, kWinW, kWinH, flags);
    if (!win) {
        std::fprintf(stderr, "SDL_CreateWindow failed: %s\n", SDL_GetError());
        SDL_Quit();
        return 2;
    }
    SDL_Renderer* r =
        SDL_CreateRenderer(win, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!r)
        r = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    if (!r) {
        std::fprintf(stderr, "SDL_CreateRenderer failed: %s\n", SDL_GetError());
        SDL_DestroyWindow(win);
        SDL_Quit();
        return 2;
    }
    SDL_SetRenderDrawBlendMode(r, SDL_BLENDMODE_BLEND);

    std::optional<std::string> saved_host;
    int rc = 0;
    while (true) {
        auto spec = preflight(win, r, opt, saved_host);
        if (!spec || spec->mode == "quit")
            break;
        if (spec->mode == "pi")
            saved_host = spec->host;
        rc = flight_screen(win, r, *spec, opt);
        if (rc != 0)
            break;
    }
    SDL_DestroyRenderer(r);
    SDL_DestroyWindow(win);
    SDL_Quit();
    return rc;
}

} // namespace djilink::gui
