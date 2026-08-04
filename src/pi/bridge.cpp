// Bridge on the Pi Zero: AOA channel to the remote controller  <->  TCP to the laptop.
// Ported from dji_link_beta/pi/bridge.py.
//
//     [Laptop: keyboard/Drone API] --TCP--> [bridge on the Pi] --USB(AOA)--> [Remote controller]
//     ))) [Drone]
//
// The laptop sends ready-made DUML frames over TCP; we hand them to the remote controller via
// bulk IN; everything the remote controller sends (bulk OUT) we push back into TCP. We do not
// parse the bytes — a transparent transport (DUML parsing lives on the laptop, in drone.py).
//
// Run on the Pi (after setup_gadget.sh):
//     ./dji-bridge --udc 20980000.usb
// Default port is 9910.
#include "pi/aoa_device.hpp"

#include <algorithm>
#include <arpa/inet.h>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <dirent.h>
#include <fcntl.h>
#include <mutex>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <signal.h>
#include <stdexcept>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>

using djilink::pi::Bytes;

namespace {

const char* LOG_DIR = "/var/log/dji-link";
const char* LOG_FILE = "/var/log/dji-link/bridge.log";

// ---- Tee logging ---------------------------------------------------------
// Python setup_logging(): every stdout/stderr line goes to the console (journal when
// run under systemd) AND is appended to /var/log/dji-link/bridge.log. Fallback to
// ./bridge.log when /var/log is not writable.
FILE* log_fp = nullptr;
std::mutex log_mutex;
// logging.basicConfig wrote timestamps like
// "2026-06-01 20:12:03,466 [INFO MainThread] bridge starting" — we keep the
// datetime,msecs part; the level/thread name are replaced by the level letter + thread tag.

void log_open() {
    // mkdir -p the log dir; on failure (permissions) fall back to ./bridge.log.
    if (::mkdir(LOG_DIR, 0755) != 0 && errno != EEXIST) {
        // /var/log not writable — fall through to the file fallback below
    }
    const char* path = LOG_FILE;
    FILE* f = std::fopen(path, "a");
    if (!f) {
        f = std::fopen("bridge.log", "a");
        path = "bridge.log";
    } else {
        path = LOG_FILE;
    }
    log_fp = f;
    if (log_fp)
        setvbuf(log_fp, nullptr, _IOLBF, 0); // line buffering = Python buffering=1
}

void logf(const char* level, const char* thread_tag, const char* fmt, ...) {
    char body[2048];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(body, sizeof(body), fmt, ap);
    va_end(ap);

    time_t t = std::time(nullptr);
    struct tm tmv {};
    localtime_r(&t, &tmv);
    long ms = 0;
    {
        timespec ts{};
        clock_gettime(CLOCK_REALTIME, &ts);
        ms = ts.tv_nsec / 1000000L;
    }

    std::lock_guard<std::mutex> lk(log_mutex);
    char line[2560];
    std::snprintf(line, sizeof(line), "%04d-%02d-%02d %02d:%02d:%02d,%03ld [%s %s] %s",
                  tmv.tm_year + 1900, tmv.tm_mon + 1, tmv.tm_mday, tmv.tm_hour, tmv.tm_min,
                  tmv.tm_sec, ms, level, thread_tag, body);
    // Tee: stdout (journal) + log file, like the Python Tee of sys.stdout/sys.stderr.
    std::fprintf(stdout, "%s\n", line);
    std::fflush(stdout);
    if (log_fp) {
        std::fprintf(log_fp, "%s\n", line);
        std::fflush(log_fp);
    }
}

// Python print() — goes to stdout AND the log file through the same Tee.
void print_tee(const char* fmt, ...) {
    char body[2048];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(body, sizeof(body), fmt, ap);
    va_end(ap);
    std::lock_guard<std::mutex> lk(log_mutex);
    std::fprintf(stdout, "%s\n", body);
    std::fflush(stdout);
    if (log_fp) {
        std::fprintf(log_fp, "%s\n", body);
        std::fflush(log_fp);
    }
}

// ---- SIGINT/SIGTERM -------------------------------------------------------
// Python: setup_logging/install_crash_handlers have no SIGINT handler, so Ctrl-C kills
// the process mid-`serve` via KeyboardInterrupt; we instead raise the flag and let main
// unwind like the Python finally does (state.stop / dev.stop).
std::atomic<bool> g_stop{false};
void sig_handler(int) {
    g_stop = true;
}

std::vector<std::string> g_argv;
std::string detect_udc() {
    // Return the single available UDC name. The name is board-specific
    // (Pi Zero 1: 20980000.usb, Pi Zero 2 W: 3f980000.usb), so autodetect instead of
    // hardcoding a default.
    std::vector<std::string> udcs;
    if (DIR* dir = ::opendir("/sys/class/udc")) {
        while (dirent* e = readdir(dir))
            if (e->d_name[0] != '.')
                udcs.emplace_back(e->d_name);
        closedir(dir);
    }
    if (udcs.empty())
        throw std::runtime_error("no UDC found in /sys/class/udc — run setup_gadget.sh and reboot "
                                 "(the dwc2 overlay must be active in peripheral mode)");
    std::sort(udcs.begin(), udcs.end());
    if (udcs.size() > 1) {
        std::string all;
        for (auto& u : udcs) {
            if (!all.empty())
                all += ", ";
            all += u;
        }
        print_tee("[bridge] several UDCs [%s], using %s (override with --udc)", all.c_str(),
                  udcs[0].c_str());
    }
    return udcs[0];
}

struct BridgeState {
    std::mutex lock;
    djilink::pi::AoaDevice* dev = nullptr; // guarded by lock
    std::string status = "AOA not started yet";
    std::atomic<bool> stop{false};

    void set_dev(djilink::pi::AoaDevice* d, const std::string& s) {
        {
            std::lock_guard<std::mutex> lk(lock);
            dev = d;
            status = s;
        }
        logf("INFO", "aoa-worker", "%s", s.c_str());
    }
    djilink::pi::AoaDevice* get_dev() {
        std::lock_guard<std::mutex> lk(lock);
        return dev;
    }
    djilink::pi::AoaDevice* ready_dev() {
        std::lock_guard<std::mutex> lk(lock);
        return (dev && dev->ready()) ? dev : nullptr;
    }
};

void aoa_worker(BridgeState* state, const char* udc_arg, const char* udc_driver_arg) {
    while (!state->stop && !g_stop) {
        djilink::pi::AoaDevice* dev = nullptr;
        try {
            std::string udc = udc_arg ? udc_arg : detect_udc();
            std::string udc_driver = udc_driver_arg ? udc_driver_arg : udc;
            logf("INFO", "aoa-worker", "using UDC %s (driver %s)", udc.c_str(), udc_driver.c_str());
            auto* d = new djilink::pi::AoaDevice(udc_driver, udc);
            dev = d;
            state->set_dev(d, "AOA worker running on UDC " + udc);
            d->run_forever();
            state->set_dev(nullptr, "AOA worker stopped; retrying");
        } catch (const std::exception& e) {
            state->set_dev(nullptr, "AOA worker crashed; retrying in 2s");
            logf("ERROR", "aoa-worker", "AOA worker exception: %s", e.what());
            std::this_thread::sleep_for(std::chrono::seconds(2));
        }
        if (dev) {
            try {
                dev->stop();
            } catch (const std::exception& e) {
                logf("ERROR", "aoa-worker", "dev.stop failed: %s", e.what());
            }
            delete dev;
        }
    }
}

// remote controller -> TCP
void usb_to_tcp(BridgeState* state, int conn, std::atomic<bool>* stop) {
    djilink::pi::AoaDevice* active = nullptr;
    while (!*stop && !g_stop) {
        auto* dev = state->ready_dev();
        if (!dev) {
            if (active) {
                print_tee("[bridge] AOA went away; keeping TCP client connected");
                active = nullptr;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
            continue;
        }
        if (dev != active) {
            active = dev;
            int drained = 0;
            Bytes junk;
            while (!dev->rx_queue.empty()) {
                if (!dev->rx_queue.try_get(junk))
                    break;
                drained += 1;
            }
            print_tee("[bridge] AOA available for TCP client; drained %d stale frames", drained);
        }
        Bytes data;
        if (!dev->rx_queue.get_for(data, 0.5))
            continue; // queue.Empty
        // conn.sendall(data)
        std::size_t off = 0;
        while (off < data.size()) {
            ssize_t n = ::send(conn, data.data() + off, data.size() - off, 0);
            if (n < 0) {
                if (errno == EINTR)
                    continue;
                stop->store(true);
                return;
            }
            off += static_cast<std::size_t>(n);
        }
    }
}

void arg_parse_error(const char* prog, const char* fmt, ...) {
    char msg[1024];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(msg, sizeof(msg), fmt, ap);
    va_end(ap);
    std::fprintf(stderr,
                 "usage: %s [-h] [--udc UDC] [--udc-driver UDC_DRIVER] [--host HOST]\n"
                 "          [--port PORT] [--model MODEL]\n"
                 "%s: error: %s\n",
                 prog, prog, msg);
    std::exit(2);
}

void print_help(const char* prog) {
    std::printf("usage: %s [-h] [--udc UDC] [--udc-driver UDC_DRIVER] [--host HOST]\n"
                "          [--port PORT] [--model MODEL]\n"
                "\n"
                "Pi AOA<->TCP bridge\n"
                "\n"
                "options:\n"
                "  -h, --help            show this help message and exit\n"
                "  --udc UDC             UDC name (see /sys/class/udc/); autodetected if omitted\n"
                "  --udc-driver UDC_DRIVER\n"
                "                        UDC driver name (defaults to the same as --udc)\n"
                "  --host HOST\n"
                "  --port PORT\n"
                "  --model MODEL         expected model from the remote controller (for logs)\n",
                prog);
}

} // namespace

int main(int argc, char** argv) {
    // save argv first so aoa_device can execv("/proc/self/exe", ...) on SUSPEND
    djilink::pi::set_saved_argv(argc, argv);

    log_open();

    ::signal(SIGINT, sig_handler);
    ::signal(SIGTERM, sig_handler);
    ::signal(SIGPIPE, SIG_IGN); // Python sockets raise instead; ignoring keeps ::send alive

    // ---- argument parsing: argparse equivalent of bridge.py ----
    const char* prog = argc > 0 ? argv[0] : "dji-bridge";
    const char* udc = nullptr;
    const char* udc_driver = nullptr;
    const char* host = "0.0.0.0";
    long port = 9910;
    const char* model = "com.dji.logiclink";

    for (int i = 1; i < argc; i++) {
        const char* a = argv[i];
        auto need_value = [&](const char* name) -> const char* {
            if (i + 1 >= argc)
                arg_parse_error(prog, "argument %s: expected one argument", name);
            return argv[++i];
        };
        auto match_opt = [&](const char* name, const char*& dst) {
            // supports both `--opt v` and `--opt=v`
            std::size_t n = std::strlen(name);
            if (std::strcmp(a, name) == 0) {
                dst = need_value(name);
                return true;
            }
            if (std::strncmp(a, name, n) == 0 && a[n] == '=') {
                dst = a + n + 1;
                return true;
            }
            return false;
        };
        if (std::strcmp(a, "-h") == 0 || std::strcmp(a, "--help") == 0) {
            print_help(prog);
            return 0;
        } else if (match_opt("--udc", udc)) {
        } else if (match_opt("--udc-driver", udc_driver)) {
        } else if (match_opt("--host", host)) {
        } else if (std::strcmp(a, "--port") == 0) {
            const char* v = need_value("--port");
            char* end = nullptr;
            port = std::strtol(v, &end, 10);
            if (!end || *end != '\0' || end == v)
                arg_parse_error(prog, "argument --port: invalid int value: '%s'", v);
        } else if (std::strncmp(a, "--port=", 7) == 0) {
            const char* v = a + 7;
            char* end = nullptr;
            port = std::strtol(v, &end, 10);
            if (!end || *end != '\0' || end == v)
                arg_parse_error(prog, "argument --port: invalid int value: '%s'", v);
        } else if (match_opt("--model", model)) {
        } else {
            arg_parse_error(prog, "unrecognized arguments: %s", a);
        }
    }
    if (port < 0 || port > 65535)
        arg_parse_error(prog, "argument --port: invalid port: %ld", port);

    (void) model; // Python kept it purely for logs

    BridgeState state;

    std::thread worker(aoa_worker, &state, udc, udc_driver);
    worker.detach();

    // ---- serve(): TCP listener towards the laptop ----
    int srv = ::socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) {
        logf("CRITICAL", "MainThread", "socket() failed: %s", std::strerror(errno));
        return 1;
    }
    int one = 1;
    ::setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<std::uint16_t>(port));
    if (::inet_pton(AF_INET, host, &addr.sin_addr) != 1) {
        print_tee("[bridge] cannot parse --host %s", host);
        return 1;
    }
    if (::bind(srv, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 ||
        ::listen(srv, 1) != 0) {
        logf("CRITICAL", "MainThread", "bind/listen on %s:%ld failed: %s", host, port,
             std::strerror(errno));
        return 1;
    }
    print_tee("[bridge] listening for laptop on %s:%ld", host, port);

    while (!g_stop) {
        sockaddr_in peer{};
        socklen_t plen = sizeof(peer);
        int conn = ::accept(srv, reinterpret_cast<sockaddr*>(&peer), &plen);
        if (conn < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        char ip[64] = "?";
        ::inet_ntop(AF_INET, &peer.sin_addr, ip, sizeof(ip));
        print_tee("[bridge] connected (%s, %u)", ip, ntohs(peer.sin_port));
        one = 1;
        ::setsockopt(conn, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));

        std::atomic<bool> stop{false};
        std::thread t(usb_to_tcp, &state, conn, &stop);
        t.detach();

        // TCP -> remote controller
        bool warned_no_aoa = false;
        while (!stop && !g_stop) {
            char buf[4096];
            ssize_t n = ::recv(conn, buf, sizeof(buf), 0);
            if (n < 0) {
                if (errno == EINTR)
                    continue;
                break;
            }
            if (n == 0)
                break;
            auto* dev = state.ready_dev();
            if (!dev) {
                if (!warned_no_aoa) {
                    print_tee("[bridge] dropping laptop frames until AOA is ready");
                    warned_no_aoa = true;
                }
                continue;
            }
            dev->send(Bytes(reinterpret_cast<std::uint8_t*>(buf),
                            reinterpret_cast<std::uint8_t*>(buf) + n));
        }
        stop = true;
        ::shutdown(conn, SHUT_RDWR);
        ::close(conn);
        print_tee("[bridge] laptop disconnected, waiting again");
    }

    // Python finally: state.stop.set(); dev.stop()
    state.stop = true;
    if (auto* dev = state.get_dev())
        dev->stop();
    ::close(srv);
    return 0;
}
