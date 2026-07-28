// dji-link — PC client for the DJI Mavic Mini 1 (WM160), C++ port of
// dji_link_beta/pc_client.py.
//
// Entry point. By default it launches the SDL2 GUI (preflight menu -> connect ->
// in-flight window with video/HUD/settings, and a "Check for updates" button).
// The GUI + the Client session object live in src/gui and src/core respectively.
//
// Flags mirror pc_client.py and also allow a headless CONSOLE client (handy for
// the Pi / debugging / no display): --console forces it, and it is the fallback
// when the app is built without the GUI (DJI_LINK_GUI=OFF).

#include "core/applog.hpp"
#include "core/client.hpp"
#include "core/netfind.hpp"
#include "core/transport.hpp"
#include "core/updater.hpp"

#include <algorithm>
#include <cstdio>
#include <iostream>
#include <memory>
#include <string>

#if DJI_LINK_HAVE_GUI
#include "gui/gui.hpp"
#endif

using namespace djilink;

namespace {

struct Args {
    std::string pi;
    std::string serial;
    bool sim = false;
    bool dry = false;
    bool verbose = false;
    bool no_video = false;
    bool console = false;
    bool windowed = false;
    bool wifi = false;
    std::string wifi_ssid;
    std::string wifi_psk;
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
        else if (s == "--console")
            a.console = true;
        else if (s == "--windowed")
            a.windowed = true;
        else if (s == "--wifi")
            a.wifi = true;
        else if (s == "--wifi-ssid")
            a.wifi_ssid = next();
        else if (s == "--wifi-psk")
            a.wifi_psk = next();
    }
    return a;
}

// --wifi: put the Pi on a Wi-Fi network without a display. Same netctl API the GUI
// screen drives, so a headless box (or the Pi itself over SSH) can do the setup, and
// --wifi-ssid/--wifi-psk make it scriptable for an unattended install.
int run_wifi(const Args& args) {
    std::string host = args.pi;
    if (auto pos = host.find(':'); pos != std::string::npos)
        host = host.substr(0, pos);
    if (host.empty()) {
        std::printf("[pi] discovering the Pi...\n");
        auto r = netfind::discover();
        if (!r.host) {
            std::printf("[pi] not found. Pass --pi HOST.\n");
            return 2;
        }
        host = *r.host;
    }
    if (auto st = netfind::pi_status(host)) {
        std::printf("[pi] %s  AP '%s' %s, uplink %s, internet %s\n", host.c_str(),
                    st->ap_ssid.c_str(), st->ap_active ? "up" : "down",
                    st->uplink_name.empty() ? "none" : st->uplink_name.c_str(),
                    st->internet ? "yes" : "no");
    } else {
        std::printf("[pi] %s does not answer on port %d.\n", host.c_str(), netfind::NETCTL_PORT);
        return 2;
    }

    std::string ssid = args.wifi_ssid, psk = args.wifi_psk;
    if (ssid.empty()) {
        std::printf("[pi] scanning (the Pi's radio, takes a few seconds)...\n");
        auto nets = netfind::pi_scan_wifi(host);
        if (nets.empty()) {
            std::printf("[pi] the Pi sees no networks.\n");
            return 1;
        }
        for (std::size_t i = 0; i < nets.size(); ++i) {
            std::printf("  %2zu) %3d%%  %-12s %s%s\n", i + 1, nets[i].signal,
                        nets[i].open() ? "open" : nets[i].security.c_str(), nets[i].ssid.c_str(),
                        nets[i].in_use ? "  (in use)" : "");
        }
        std::printf("number (or SSID), empty to quit: ");
        std::fflush(stdout);
        std::string pick;
        if (!std::getline(std::cin, pick) || pick.empty())
            return 0;
        try {
            const int idx = std::stoi(pick);
            if (idx >= 1 && idx <= static_cast<int>(nets.size()))
                ssid = nets[static_cast<std::size_t>(idx) - 1].ssid;
        } catch (const std::exception&) {
            ssid = pick; // not a number — treat it as the SSID itself
        }
        if (ssid.empty())
            ssid = pick;
        bool open = false;
        for (const auto& n : nets)
            if (n.ssid == ssid)
                open = n.open();
        if (!open && psk.empty()) {
            std::printf("password (empty for none): ");
            std::fflush(stdout);
            std::getline(std::cin, psk);
        }
    }

    auto res = netfind::pi_connect_wifi(host, ssid, psk);
    std::printf("[pi] %s\n", res.output.empty() ? (res.ok ? "connect ok" : "connect failed")
                                                : res.output.c_str());
    // The AP retunes to the uplink's channel, so this machine's own link to the Pi drops
    // for a few seconds when it was connected over that AP.
    std::printf("[pi] waiting for the Pi to answer again...\n");
    if (!netfind::wait_for_pi(host)) {
        std::printf("[pi] still unreachable — re-join the Pi's access point and re-check.\n");
        return 1;
    }
    auto st = netfind::pi_status(host);
    const bool net = st && st->internet;
    std::printf("[pi] uplink %s, internet %s\n",
                st && !st->uplink_name.empty() ? st->uplink_name.c_str() : "none",
                net ? "yes" : "no");
    return (res.ok && net) ? 0 : 1;
}

// Build the transport for a connection spec (used by the console path; the GUI
// builds its own after the preflight menu). Returns nullptr on failure.
std::unique_ptr<Transport> make_transport(const std::string& mode, const std::string& host,
                                          int port, const std::string& serial) {
    if (mode == "sim")
        return std::make_unique<LogTransport>(true);
    if (mode == "serial")
        return std::make_unique<SerialTransport>(serial);
    return std::make_unique<CompositeTransport>(std::make_unique<NetTransport>(host, port));
}

int run_console(const Args& args) {
    const bool base_live = !args.dry;
    std::string mode, host = args.pi, serial = args.serial;
    int port = 9910;
    bool live = base_live;

    if (args.sim) {
        mode = "sim";
        live = false;
        applog::info("[sim] loopback — commands are printed, no hardware");
    } else if (!serial.empty()) {
        mode = "serial";
    } else {
        mode = "pi";
        if (host.empty()) {
            std::printf("[pi] discovering the Pi on the LAN...\n");
            auto r = netfind::discover();
            if (!r.host) {
                std::printf("[pi] not found. Pass --pi HOST[:PORT], --serial PORT, or --sim.\n");
                return 2;
            }
            host = *r.host;
            std::printf("[pi] found the Pi at %s (via %s)\n", host.c_str(), r.via.c_str());
        } else if (auto pos = host.find(':'); pos != std::string::npos) {
            port = std::stoi(host.substr(pos + 1));
            host = host.substr(0, pos);
        }
    }

    std::unique_ptr<Transport> t;
    try {
        t = make_transport(mode, host, port, serial);
    } catch (const std::exception& e) {
        std::printf("[connect] %s\n", e.what());
        return 2;
    }

    Client cli(std::move(t), mode, live);
    cli.drone().encrypt_config = false; // plaintext link (see pc_client.py note)
    cli.start();
    if (!args.no_video)
        cli.start_video();

    std::printf("Ready. Type 'help' for commands, 'quit' to exit.\n");
    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty())
            continue;
        run_console_cmd(cli, line);
        std::string c = line.substr(0, line.find(' '));
        std::transform(c.begin(), c.end(), c.begin(),
                       [](unsigned char ch) { return static_cast<char>(std::tolower(ch)); });
        if (c == "quit" || c == "exit")
            break;
    }
    cli.close();
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    Args args = parse_args(argc, argv);
    applog::setup(args.verbose);
    applog::info(std::string("[log] logging to ") + applog::latest_path());
    // Also on stdout: the log file is no longer next to the exe, so print where it went.
    std::printf("log file: %s\n", applog::latest_path().c_str());
    // Clean up any installer left over from a previous update attempt.
    updater::wipe_temp();

    // Wi-Fi setup is a standalone job: it never opens a window or a drone session, so it
    // is handled before the GUI branch and works in a build without the GUI.
    if (args.wifi)
        return run_wifi(args);

#if DJI_LINK_HAVE_GUI
    if (!args.console) {
        gui::AppOptions opt;
        opt.pi = args.pi;
        opt.serial = args.serial;
        opt.sim = args.sim;
        opt.dry = args.dry;
        opt.verbose = args.verbose;
        opt.no_video = args.no_video;
        opt.windowed = args.windowed;
        return gui::run_app(opt);
    }
#endif
    return run_console(args);
}

#if defined(_WIN32) && DJI_LINK_HAVE_GUI
// The GUI build is linked as a Windows-subsystem app (WIN32_EXECUTABLE), so the CRT
// entry point is WinMain rather than main. Forward to the real main() using the
// CRT-provided argc/argv globals from <cstdlib>. NEVER redeclare __argc/__argv: on
// MSVC they are function-like macros ((*__p___argc())), so an `extern "C" int __argc;`
// redeclaration is a hard compile error that breaks the entire Windows build.
#include <cstdlib>
extern "C" int __stdcall WinMain(void*, void*, char*, int) {
    return main(__argc, __argv);
}
#endif
