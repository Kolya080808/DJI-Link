// netctl.cpp — Wi-Fi control for the Pi jump-host: access point + optional internet uplink.
// Ported 1:1 (same mechanics, same quirks) from dji_link_beta/pi/netctl.py.
//
// Two jobs, and they are deliberately independent of each other:
//
//   1. ALWAYS serve an access point the laptop can join. This is the control path to the
//      Pi (10.42.0.1: netctl on :9911, the AOA bridge on :9910). It must survive every
//      uplink change, every failed join and every reboot, because when it is down there
//      is no way left to talk to the Pi in the field.
//   2. Optionally join an existing Wi-Fi network as an uplink. AP clients are NATed out
//      through it, so the laptop gets internet over the same association it uses to reach
//      the Pi — but on a different route: the Pi itself is on-link at 10.42.0.1 and is
//      never NATed, so it stays reachable whether or not the uplink exists.
//
// The Pi Zero 2 W has ONE radio. A second virtual interface (uap0) carries the AP while
// wlan0 stays the client. The AP itself is run by hostapd + dnsmasq (the dji-ap systemd
// unit, see pi/ap.sh) rather than NetworkManager: NM's AP goes through wpa_supplicant,
// which advertises WPS and makes Windows demand a PIN instead of the passphrase. hostapd
// with wps_state=0 is a plain WPA2 network every OS joins with just the password. When
// dji-ap is absent (a Pi not yet re-set-up) we fall back to NM's ipv4.method=shared AP.
//
// The chip requires AP and client to share a channel, so joining an uplink can retune the
// AP and clients then reconnect — hardware, not a bug. Every successful uplink join ends
// with one delayed, clean AP restart. That gives Windows a predictable disconnect event;
// the PC client then explicitly re-associates instead of relying on Windows auto-reconnect.
// Disconnecting or failing to join an uplink still leaves a healthy AP untouched.
//
// Usage (on the Pi):
//     sudo dji-netctl status
//     sudo dji-netctl doctor           # full diagnosis, run this first when stuck
//     sudo dji-netctl scan
//     sudo dji-netctl connect "MySSID" "password"
//     sudo dji-netctl disconnect
//     sudo dji-netctl hotspot on|off
//     sudo dji-netctl serve            # HTTP API on :9911 for the PC client
//
// HTTP API (used by pc_client's network panel):
//     GET  /status            -> {"ap": {...}, "uplink": {...}, "internet": bool, ...}
//     GET  /scan              -> {"networks": [{"ssid","signal","security","in_use"}]}
//     GET  /doctor            -> {"checks": [...], "ok": bool}
//     POST /connect           <- {"ssid": "...", "psk": "..."}
//     POST /disconnect
//     POST /hotspot           <- {"on": true}
//
// Delivered with no arguments, or an unknown one, this prints the usage text below, which
// is kept in the same shape as the Python module docstring it was ported from.

#include <algorithm>
#include <arpa/inet.h>
#include <array>
#include <atomic>
#include <cctype>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <fstream>
#include <memory>
#include <mutex>
#include <netinet/in.h>
#include <optional>
#include <poll.h>
#include <sstream>
#include <string>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <thread>
#include <unistd.h>
#include <vector>

namespace {

const char* const USAGE =
    R"(netctl — Wi-Fi control for the Pi jump-host: access point + optional internet uplink.

Two jobs, and they are deliberately independent of each other:

  1. ALWAYS serve an access point the laptop can join. This is the control path to the
     Pi (10.42.0.1: netctl on :9911, the AOA bridge on :9910). It must survive every
     uplink change, every failed join and every reboot, because when it is down there
     is no way left to talk to the Pi in the field.
  2. Optionally join an existing Wi-Fi network as an uplink. AP clients are NATed out
     through it, so the laptop gets internet over the same association it uses to reach
     the Pi — but on a different route: the Pi itself is on-link at 10.42.0.1 and is
     never NATed, so it stays reachable whether or not the uplink exists.

The Pi Zero 2 W has ONE radio. A second virtual interface (uap0) carries the AP while
wlan0 stays the client. The AP itself is run by hostapd + dnsmasq (the dji-ap systemd
unit, see pi/ap.sh) rather than NetworkManager: NM's AP goes through wpa_supplicant,
which advertises WPS and makes Windows demand a PIN instead of the passphrase. hostapd
with wps_state=0 is a plain WPA2 network every OS joins with just the password. When
dji-ap is absent (a Pi not yet re-set-up) we fall back to NM's ipv4.method=shared AP.

The chip requires AP and client to share a channel, so joining an uplink can retune the
AP and clients then reconnect — hardware, not a bug. Every successful uplink join ends
with one delayed, clean AP restart. That gives Windows a predictable disconnect event;
the PC client then explicitly re-associates instead of relying on Windows auto-reconnect.
Disconnecting or failing to join an uplink still leaves a healthy AP untouched.

Usage (on the Pi):
    sudo dji-netctl status
    sudo dji-netctl doctor           # full diagnosis, run this first when stuck
    sudo dji-netctl scan
    sudo dji-netctl connect "MySSID" "password"
    sudo dji-netctl disconnect
    sudo dji-netctl hotspot on|off
    sudo dji-netctl serve            # HTTP API on :9911 for the PC client

HTTP API (used by pc_client's network panel):
    GET  /status            -> {"ap": {...}, "uplink": {...}, "internet": bool, ...}
    GET  /scan              -> {"networks": [{"ssid","signal","security","in_use"}]}
    GET  /doctor            -> {"checks": [...], "ok": bool}
    POST /connect           <- {"ssid": "...", "psk": "..."}
    POST /disconnect
    POST /hotspot           <- {"on": true}
)";

const char* const AP_CON = "dji-link-ap"; // legacy NetworkManager AP profile (fallback path)
const char* const AP_SERVICE =
    "dji-ap"; // hostapd+dnsmasq AP unit created by setup_pi.sh (pi/ap.sh)
const char* const AP_UNIT_PATH = "/etc/systemd/system/dji-ap.service";
const char* const AP_IFACE = "uap0";
const char* const STA_IFACE = "wlan0";
const char* const AP_PSK = "raspberry"; // default; >= 8 chars for WPA2
const char* const AP_ADDR = "10.42.0.1";
constexpr int PORT = 9911;
// In Python: os.path.dirname(os.path.abspath(__file__)); here: argv[0]'s directory.
std::string AP_SH;
const char* const HOSTAPD_CONF = "/run/dji-ap/hostapd.conf";
// "the operator asked for the AP to be off" — the one thing that must stop the watchdog
// from putting it back. Under /run on purpose: a reboot clears it, so a hotspot switched
// off during an experiment can never turn into a Pi that comes up unreachable.
// Deliberately outside RuntimeDirectory=dji-ap: systemd removes that directory when the
// AP unit stops, which used to erase the operator's "hotspot off" request and let the
// watchdog turn it straight back on. /run still clears the request on the next reboot.
const char* const AP_OFF_FLAG = "/run/dji-link-hotspot-off";
const char* const UPLINK_PREFIX = "dji-uplink-";
constexpr int AP_AUTO_RESTART_LIMIT = 3;

// ---------------------------------------------------------------- small shims

bool file_exists(const char* path) {
    struct stat st;
    return ::stat(path, &st) == 0;
}

std::string join_args(const std::vector<std::string>& args) {
    std::string s;
    for (size_t i = 0; i < args.size(); ++i) {
        if (i)
            s += ' ';
        s += args[i];
    }
    return s;
}

// Python str.splitlines() — splits on \n, \r and \r\n, no trailing empty element.
std::vector<std::string> py_splitlines(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (size_t i = 0; i < s.size(); ++i) {
        char c = s[i];
        if (c == '\n' || c == '\r') {
            out.push_back(cur);
            cur.clear();
            if (c == '\r' && i + 1 < s.size() && s[i + 1] == '\n')
                ++i; // CRLF is one line break
        } else {
            cur += c;
        }
    }
    if (!cur.empty())
        out.push_back(cur);
    return out;
}

// Python str.split() — runs of whitespace separate, leading/trailing runs ignored.
std::vector<std::string> py_split_ws(const std::string& s) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) {
        if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f') {
            if (!cur.empty()) {
                out.push_back(cur);
                cur.clear();
            }
        } else {
            cur += c;
        }
    }
    if (!cur.empty())
        out.push_back(cur);
    return out;
}

// Python str.strip() for the ASCII/whitespace cases this program actually hits.
std::string py_strip(const std::string& s) {
    size_t b = s.find_first_not_of(" \t\n\r\v\f");
    if (b == std::string::npos)
        return "";
    return s.substr(b, s.find_last_not_of(" \t\n\r\v\f") - b + 1);
}

std::string to_upper(std::string s) {
    for (char& c : s)
        c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    return s;
}

bool contains(const std::string& hay, const std::string& needle) {
    return hay.find(needle) != std::string::npos;
}

bool iequals(const std::string& a, const std::string& b) {
    return to_upper(a) == to_upper(b);
}

// \b semantics of the two regexes netctl.py takes from `re`: a position is a word
// boundary when a [0-9A-Za-z_] character sits on exactly one side of it.
bool is_word_char(char c) {
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
}

std::string strerror_str(int e) {
    char buf[128];
#if defined(__GLIBC__) && defined(_GNU_SOURCE)
    return ::strerror_r(e, buf, sizeof(buf));
#else
    if (::strerror_r(e, buf, sizeof(buf)) != 0)
        return "unknown error";
    return buf;
#endif
}

// ---------------------------------------------------------------- run(): subprocess.run([...],
// capture_output=True, text=True, timeout=...)
//
// The timeout follows subprocess.run semantics: the child (and its process group, so
// children it spawned go with it) is killed and the call reports rc=124 with the same
// message Python produced. Everything here is called from an HTTP handler, so a command
// that hangs (nmcli waiting on a supplicant that never answers) would otherwise pin a
// worker thread forever. A timeout turns that into an ordinary failure.

int kill_process_group(pid_t pgid) {
    // poll() CAN return at the timeout with the process still alive; Python kills the
    // whole process tree then, we take the whole process group.
    ::kill(-pgid, SIGKILL);
    int status = 0;
    if (::waitpid(pgid, &status, 0) < 0 && errno == EINTR)
        ::waitpid(pgid, &status, 0);
    return status;
}

struct CmdResult {
    int rc;
    std::string out;
};

// Never fails the caller with an exception; mirrors netctl.py's run(check=False).
CmdResult run_argv(const std::vector<std::string>& args, double timeout_s = 90.0) {
    int pipe_out[2];
    if (::pipe(pipe_out) < 0)
        return {127, join_args(args) + ": " + strerror_str(errno)};
    for (int fd : pipe_out)
        ::fcntl(fd, F_SETFD, FD_CLOEXEC);

    pid_t pid = ::fork();
    if (pid < 0) {
        ::close(pipe_out[0]);
        ::close(pipe_out[1]);
        return {127, join_args(args) + ": " + strerror_str(errno)};
    }
    if (pid == 0) {
        ::setsid(); // own process group, so the timeout kill reaches grandchildren
        ::dup2(pipe_out[1], STDOUT_FILENO);
        ::dup2(pipe_out[1], STDERR_FILENO); // stdout + stderr, like py's out+err concat
        ::close(pipe_out[0]);
        ::close(pipe_out[1]);
        std::vector<char*> argv;
        argv.reserve(args.size() + 1);
        for (const auto& a : args)
            argv.push_back(const_cast<char*>(a.c_str()));
        argv.push_back(nullptr);
        ::execvp(argv[0], argv.data());
        std::string msg = join_args(args) + ": " + strerror_str(errno);
        [[maybe_unused]] ssize_t nw = ::write(STDOUT_FILENO, msg.data(), msg.size());
        _exit(127);
    }
    ::close(pipe_out[1]);

    std::string captured;
    std::array<char, 4096> buf;
    int status = 0;
    bool reaped = false;
    auto deadline = std::chrono::steady_clock::now() +
                    std::chrono::duration<double>(timeout_s < 0 ? 0 : timeout_s);
    for (;;) {
        // Read whatever is available until EOF; then poll with a timeout while the
        // child is still running, or exit once it is reaped and the pipe is drained.
        struct pollfd p = {pipe_out[0], POLLIN | POLLHUP, 0};
        int to_ms;
        if (reaped) {
            to_ms = 0; // child is gone: one last non-blocking drain, then out
        } else {
            auto now = std::chrono::steady_clock::now();
            if (now >= deadline) {
                ::close(pipe_out[0]);
                kill_process_group(pid);
                return {124, join_args(args) + ": timed out after " +
                                 std::to_string(static_cast<long long>(timeout_s)) + "s"};
            }
            to_ms =
                static_cast<int>(
                    std::chrono::duration_cast<std::chrono::milliseconds>(deadline - now).count()) +
                1;
        }
        int nready = ::poll(&p, 1, to_ms);
        if (nready < 0) {
            if (errno == EINTR)
                continue;
            break;
        }
        if (p.revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t n = ::read(pipe_out[0], buf.data(), buf.size());
            if (n > 0)
                captured.append(buf.data(), static_cast<size_t>(n));
            else {
                ::close(pipe_out[0]);
                break; // EOF: the write end is fully closed
            }
        }
        if (!reaped) {
            pid_t w = ::waitpid(pid, &status, WNOHANG);
            if (w == pid)
                reaped = true;
            else if (w < 0 && errno != EINTR)
                break;
        }
    }
    if (!reaped) {
        while (::waitpid(pid, &status, 0) < 0 && errno == EINTR) {
        }
    }

    int rc;
    if (WIFEXITED(status))
        rc = WEXITSTATUS(status);
    else if (WIFSIGNALED(status))
        rc = 128 + WTERMSIG(status);
    else
        rc = 127;
    return {rc, py_strip(captured)};
}

[[noreturn]] void check_failed(const std::vector<std::string>& args, const std::string& out) {
    std::fprintf(stderr, "%s -> %s\n", join_args(args).c_str(), out.c_str());
    std::exit(1);
}

CmdResult run_cmd(const std::vector<std::string>& args, bool check = false, double timeout = 90.0) {
    CmdResult r = run_argv(args, timeout);
    if (check && r.rc != 0)
        check_failed(args, r.out);
    return r;
}

CmdResult nmcli(std::vector<std::string> args, bool check = false, double timeout = 90.0) {
    args.insert(args.begin(), "nmcli");
    return run_cmd(args, check, timeout);
}

CmdResult systemctl(std::vector<std::string> args) {
    args.insert(args.begin(), "systemctl");
    return run_cmd(args, false, 60.0);
}

CmdResult ap_sh(std::vector<std::string> args) {
    args.insert(args.begin(), {"bash", AP_SH});
    return run_cmd(args, false, 60.0);
}

// ---------------------------------------------------------------- minimal JSON
//
// Writer: emits exactly what Python's json.dumps(obj) emits — default separators
// ", " and ": ", insertion-ordered keys, ensure_ascii=True (non-ASCII as hhhh, control
// characters as \uXXXX or the short escapes), None->null, True->true.
// Reader: a small tolerant recursive-descent parser good enough for the flat request
// bodies our own PC client POSTs ({ssid, psk, on}).

// A JSON object entry: key by value, value behind unique_ptr. Declared BEFORE
// Json (not as a std::pair<std::string, Json>) because a std::pair of an
// incomplete type is not valid inside std::vector at the point of the member
// declaration — GCC happens to accept it, Clang rejects it, and the result is
// not portable.
struct Json;
struct JsonMember {
    std::string first;
    std::unique_ptr<Json> second;
    JsonMember() = default;
    JsonMember(const std::string& k, Json&& v);
    ~JsonMember();
    JsonMember(JsonMember&&) noexcept;
    JsonMember& operator=(JsonMember&&) noexcept;
    JsonMember(const JsonMember& o);
    JsonMember& operator=(const JsonMember& o);
};

struct Json {
    enum Type { Null, Bool, Int, Str, Arr, Obj } type = Null;
    bool boolean = false;
    long long number = 0;
    std::string str;
    std::vector<Json> arr;
    std::vector<JsonMember> obj;

    static Json object() {
        Json j;
        j.type = Obj;
        return j;
    }
    static Json array() {
        Json j;
        j.type = Arr;
        return j;
    }
    static Json string(std::string s) {
        Json j;
        j.type = Str;
        j.str = std::move(s);
        return j;
    }
    static Json boolean_of(bool b) {
        Json j;
        j.type = Bool;
        j.boolean = b;
        return j;
    }
    static Json integer(long long n) {
        Json j;
        j.type = Int;
        j.number = n;
        return j;
    }
    void set(const std::string& key, Json v) {
        obj.emplace_back(key, std::move(v));
    }
    const Json* get(const std::string& key) const {
        if (type != Obj)
            return nullptr;
        for (const auto& kv : obj)
            if (kv.first == key)
                return kv.second.get();
        return nullptr;
    }
};

// Out-of-line so Json is complete here (it is not at JsonMember's declaration).
JsonMember::JsonMember(const std::string& k, Json&& v)
    : first(k), second(std::make_unique<Json>(std::move(v))) {}
JsonMember::~JsonMember() = default;
JsonMember::JsonMember(JsonMember&&) noexcept = default;
JsonMember& JsonMember::operator=(JsonMember&&) noexcept = default;
JsonMember::JsonMember(const JsonMember& o)
    : first(o.first), second(o.second ? std::make_unique<Json>(*o.second) : nullptr) {}
JsonMember& JsonMember::operator=(const JsonMember& o) {
    if (this != &o) {
        first = o.first;
        second = o.second ? std::make_unique<Json>(*o.second) : nullptr;
    }
    return *this;
}

void json_escape_into(std::string& out, const std::string& s) {
    static const char* const hex = "0123456789abcdef";
    out += '"';
    for (size_t i = 0; i < s.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        switch (c) {
            case '"':
                out += "\\\"";
                break;
            case '\\':
                out += "\\\\";
                break;
            case '\n':
                out += "\\n";
                break;
            case '\r':
                out += "\\r";
                break;
            case '\t':
                out += "\\t";
                break;
            case '\b':
                out += "\\b";
                break;
            case '\f':
                out += "\\f";
                break;
            default:
                if (c < 0x20) {
                    // json.dumps escapes C0 controls as \uXXXX; DEL (0x7f) stays raw.
                    out += "\\u00";
                    out += hex[c >> 4];
                    out += hex[c & 0xf];
                } else if (c < 0x80) {
                    out += static_cast<char>(c);
                } else {
                    // ensure_ascii=True: decode UTF-8 and emit \uXXXX (surrogate pair
                    // for astral planes).
                    unsigned cp;
                    size_t len;
                    if ((c & 0xe0) == 0xc0 && i + 1 < s.size()) {
                        cp = c & 0x1f;
                        len = 1;
                    } else if ((c & 0xf0) == 0xe0 && i + 2 < s.size()) {
                        cp = c & 0x0f;
                        len = 2;
                    } else if ((c & 0xf8) == 0xf0 && i + 3 < s.size()) {
                        cp = c & 0x07;
                        len = 3;
                    } else {
                        cp = 0xfffd; // U+FFFD for the lone byte, like Python's error handling
                        len = 0;
                    }
                    for (size_t k = 0; k < len; ++k)
                        cp = (cp << 6) | (static_cast<unsigned char>(s[++i]) & 0x3f);
                    auto emit = [&](unsigned u) {
                        out += "\\u";
                        out += hex[(u >> 12) & 0xf];
                        out += hex[(u >> 8) & 0xf];
                        out += hex[(u >> 4) & 0xf];
                        out += hex[u & 0xf];
                    };
                    if (cp > 0xffff) {
                        cp -= 0x10000;
                        emit(0xd800 + (cp >> 10));
                        emit(0xdc00 + (cp & 0x3ff));
                    } else {
                        emit(cp);
                    }
                }
        }
    }
    out += '"';
}

void json_dump_into(std::string& out, const Json& j, int indent, int level) {
    // json.dumps separators: compact (', ', ': '), indented (',', ': ').
    const char* item_sep = indent ? "," : ", ";
    auto pad = [&](int n) {
        out.append(static_cast<size_t>(n) * static_cast<size_t>(indent), ' ');
    };
    switch (j.type) {
        case Json::Null:
            out += "null";
            break;
        case Json::Bool:
            out += j.boolean ? "true" : "false";
            break;
        case Json::Int:
            out += std::to_string(j.number);
            break;
        case Json::Str:
            json_escape_into(out, j.str);
            break;
        case Json::Arr:
            if (j.arr.empty()) {
                out += "[]";
                break;
            }
            out += '[';
            for (size_t i = 0; i < j.arr.size(); ++i) {
                if (i)
                    out += item_sep;
                if (indent)
                    out += '\n', pad(level + 1);
                json_dump_into(out, j.arr[i], indent, level + 1);
            }
            if (indent) {
                out += '\n';
                pad(level);
            }
            out += ']';
            break;
        case Json::Obj:
            if (j.obj.empty()) {
                out += "{}";
                break;
            }
            out += '{';
            for (size_t i = 0; i < j.obj.size(); ++i) {
                if (i)
                    out += item_sep;
                if (indent)
                    out += '\n', pad(level + 1);
                json_escape_into(out, j.obj[i].first);
                out += indent ? ": " : ": ";
                json_dump_into(out, *j.obj[i].second, indent, level + 1);
            }
            if (indent) {
                out += '\n';
                pad(level);
            }
            out += '}';
            break;
    }
}

std::string json_dumps(const Json& j, int indent = 0) {
    std::string out;
    json_dump_into(out, j, indent, 0);
    return out;
}

struct JsonParse {
    const char* p;
    const char* end;
    bool ok = true;

    void ws() {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r'))
            ++p;
    }
    bool eat(char c) {
        ws();
        if (p < end && *p == c) {
            ++p;
            return true;
        }
        ok = false;
        return false;
    }
    Json string() {
        Json j = Json::string("");
        if (!eat('"'))
            return j;
        while (p < end && *p != '"') {
            char c = *p++;
            if (c == '\\' && p < end) {
                char e = *p++;
                switch (e) {
                    case '"':
                        j.str += '"';
                        break;
                    case '\\':
                        j.str += '\\';
                        break;
                    case '/':
                        j.str += '/';
                        break;
                    case 'b':
                        j.str += '\b';
                        break;
                    case 'f':
                        j.str += '\f';
                        break;
                    case 'n':
                        j.str += '\n';
                        break;
                    case 'r':
                        j.str += '\r';
                        break;
                    case 't':
                        j.str += '\t';
                        break;
                    case 'u': {
                        if (end - p < 4) {
                            ok = false;
                            return j;
                        }
                        unsigned cp = 0;
                        for (int k = 0; k < 4; ++k) {
                            char h = *p++;
                            cp <<= 4;
                            cp |= h >= '0' && h <= '9'   ? static_cast<unsigned>(h - '0')
                                  : h >= 'a' && h <= 'f' ? static_cast<unsigned>(h - 'a' + 10)
                                  : h >= 'A' && h <= 'F' ? static_cast<unsigned>(h - 'A' + 10)
                                                         : 0;
                        }
                        if (cp < 0x80) {
                            j.str += static_cast<char>(cp);
                        } else if (cp < 0x800) {
                            j.str += static_cast<char>(0xc0 | (cp >> 6));
                            j.str += static_cast<char>(0x80 | (cp & 0x3f));
                        } else {
                            // Surrogate halves arrive as two \u escapes; only BMP chars
                            // show up in {ssid, psk, on} bodies, and a lone half maps to
                            // '?' — the bodies come from our own client.
                            j.str += static_cast<char>(0xe0 | (cp >> 12));
                            j.str += static_cast<char>(0x80 | ((cp >> 6) & 0x3f));
                            j.str += static_cast<char>(0x80 | (cp & 0x3f));
                        }
                        break;
                    }
                    default:
                        j.str += e;
                        break;
                }
            } else {
                j.str += c;
            }
        }
        if (p >= end) {
            ok = false;
            return j;
        }
        ++p; // closing quote
        return j;
    }
    Json value() {
        ws();
        if (p >= end) {
            ok = false;
            return {};
        }
        if (*p == '"')
            return string();
        if (*p == '{') {
            ++p;
            Json j = Json::object();
            ws();
            if (p < end && *p == '}') {
                ++p;
                return j;
            }
            for (;;) {
                Json key = string();
                if (!ok)
                    return {};
                if (!eat(':'))
                    return {};
                j.set(key.str, value());
                if (!ok)
                    return {};
                ws();
                if (p < end && *p == ',') {
                    ++p;
                    continue;
                }
                break;
            }
            if (!eat('}'))
                return {};
            return j;
        }
        if (*p == '[') {
            ++p;
            Json j = Json::array();
            ws();
            if (p < end && *p == ']') {
                ++p;
                return j;
            }
            for (;;) {
                j.arr.push_back(value());
                if (!ok)
                    return {};
                ws();
                if (p < end && *p == ',') {
                    ++p;
                    continue;
                }
                break;
            }
            if (!eat(']'))
                return {};
            return j;
        }
        const char* start = p;
        while (p < end && *p != ',' && *p != '}' && *p != ']' && *p != ' ' && *p != '\t' &&
               *p != '\n' && *p != '\r')
            ++p;
        std::string tok(start, p);
        if (tok == "true")
            return Json::boolean_of(true);
        if (tok == "false")
            return Json::boolean_of(false);
        if (tok == "null")
            return {};
        char* stop = nullptr;
        long long n = std::strtoll(tok.c_str(), &stop, 10);
        if (stop && *stop == '\0' && !tok.empty())
            return Json::integer(n);
        ok = false;
        return {};
    }
};

std::optional<Json> json_loads(const std::string& s) {
    JsonParse ps{s.data(), s.data() + s.size()};
    Json j = ps.value();
    ps.ws();
    if (!ps.ok)
        return std::nullopt;
    return j;
}

// ---------------------------------------------------------------- device identity

std::string ap_ssid() {
    // Stable, per-device AP name: PI_DJI_LINK-<4 hex>. The suffix (from machine-id)
    // lets the PC client recognise a Pi AP by prefix while staying unique per board.
    std::string suffix = "0000";
    std::ifstream f("/etc/machine-id");
    if (f) {
        std::string mid;
        std::getline(f, mid);
        mid = py_strip(mid);
        if (mid.size() >= 4)
            suffix = mid.substr(mid.size() - 4);
    }
    return "PI_DJI_LINK-" + suffix;
}

const std::string AP_SSID = ap_ssid();

// ---------------------------------------------------------------- hostapd mode

std::optional<bool> g_hostapd_mode;

bool hostapd_mode() {
    // True when the hostapd AP unit (dji-ap.service) is installed — the normal path on a
    // Pi set up by the current setup_pi.sh. False on an older Pi, or once the unit file is
    // removed, where we fall back to the NetworkManager ipv4.method=shared AP so nothing
    // regresses. Detected by the unit file itself so the switch is unambiguous.
    if (!g_hostapd_mode.has_value())
        g_hostapd_mode = file_exists(AP_UNIT_PATH) && file_exists(AP_SH.c_str());
    return *g_hostapd_mode;
}

// Split an nmcli -t line on unescaped ':' (nmcli escapes a literal one as '\:').
std::vector<std::string> split_nmcli(const std::string& line) {
    std::vector<std::string> out;
    std::string cur;
    bool esc = false;
    for (char ch : line) {
        if (esc) {
            cur += ch != ':' ? std::string("\\") + ch : std::string(":");
            esc = false;
        } else if (ch == '\\') {
            esc = true;
        } else if (ch == ':') {
            out.push_back(cur);
            cur.clear();
        } else {
            cur += ch;
        }
    }
    out.push_back(cur);
    return out;
}

// One property value. `-g` prints the bare value; older nmcli needs `-t -f` and
// the 'field:value' line taken apart.
template <typename... A> std::string nmcli_get(const std::string& field, A... rest) {
    std::vector<std::string> extra = {rest...};
    std::vector<std::string> args = {"-g", field};
    args.insert(args.end(), extra.begin(), extra.end());
    CmdResult r = nmcli(args, false, 30.0);
    if (r.rc == 0)
        return py_strip(r.out);
    args = {"-t", "-f", field};
    args.insert(args.end(), extra.begin(), extra.end());
    r = nmcli(args, false, 30.0);
    if (r.rc != 0)
        return "";
    for (const std::string& line : py_splitlines(r.out)) {
        std::vector<std::string> parts = split_nmcli(line);
        if (parts.size() >= 2 && iequals(parts[0], field)) {
            std::string v;
            for (size_t i = 1; i < parts.size(); ++i) {
                if (i > 1)
                    v += ':';
                v += parts[i];
            }
            return py_strip(v);
        }
    }
    return "";
}

// ---------------------------------------------------------------- AP (hostapd path)

std::string ap_unit_state() {
    return py_strip(systemctl({"is-active", AP_SERVICE}).out);
}

bool ap_active() {
    return ap_unit_state() == "active";
}

bool ap_recovering() {
    std::string s = ap_unit_state();
    return s == "activating" || s == "reloading";
}

bool ap_should_run() {
    return !file_exists(AP_OFF_FLAG);
}

void set_ap_off_flag(bool off) {
    if (off) {
        // /run always exists on the Pi; keep the makedirs equivalent trivial.
        std::ofstream f(AP_OFF_FLAG, std::ios::trunc);
        if (f)
            f << "hotspot turned off through the API\n";
        else
            std::fprintf(stderr, "[netctl] could not update %s: %s\n", AP_OFF_FLAG,
                         strerror_str(errno).c_str());
        return;
    }
    if (file_exists(AP_OFF_FLAG) && ::unlink(AP_OFF_FLAG) != 0)
        std::fprintf(stderr, "[netctl] could not update %s: %s\n", AP_OFF_FLAG,
                     strerror_str(errno).c_str());
}

// (healthy, reason). Covers the whole AP, not just 'is the process alive': the
// interface, its address, hostapd, dnsmasq and the NAT rule.
std::pair<bool, std::string> ap_health() {
    if (!hostapd_mode())
        return {true, "nm-fallback"};
    if (!ap_should_run())
        return {true, "turned off on request"};
    if (!ap_active())
        return {false, std::string(AP_SERVICE) + " is not active"};
    CmdResult r = ap_sh({"health"});
    std::string out = r.out;
    if (out.empty())
        out = r.rc == 0 ? "ok" : "unhealthy";
    return {r.rc == 0, out};
}

// The channel hostapd was last started with.
std::string ap_conf_channel() {
    std::ifstream f(HOSTAPD_CONF);
    std::string line;
    while (std::getline(f, line)) {
        if (line.rfind("channel=", 0) == 0) {
            std::string v = py_strip(line);
            return v.substr(v.find('=') + 1);
        }
    }
    return "";
}

// The channel ap.sh would pick right now — i.e. after the uplink changed.
std::string ap_wanted_channel() {
    CmdResult r = ap_sh({"chan"});
    if (r.rc != 0)
        return "";
    // "<hw_mode> <channel>". Taking the last token rather than index 1 so a stray line
    // on the way through cannot silently turn into a channel number.
    std::vector<std::string> parts = py_split_ws(r.out);
    if (!parts.empty() && !parts.back().empty() &&
        std::all_of(parts.back().begin(), parts.back().end(),
                    [](char c) { return c >= '0' && c <= '9'; }))
        return parts.back();
    return "";
}

// The channel of a fully associated wlan0, never an AP fallback channel.
//
// NetworkManager can keep GENERAL.STATE at 100 while wpa_supplicant briefly moves
// through disconnected/scanning/associating. During that window `ap.sh chan` returns
// its no-uplink fallback (normally channel 6), which the watchdog used to mistake for
// a real channel change and restart the otherwise healthy field AP. `iw link` is the
// authoritative extra check: it only contains an SSID/frequency while STA is actually
// associated.
std::string live_uplink_channel() {
    std::string state = nmcli_get("GENERAL.STATE", "dev", "show", STA_IFACE);
    auto starts_ci = [&](const char* prefix) {
        return state.size() >= std::strlen(prefix) &&
               iequals(state.substr(0, std::strlen(prefix)), prefix);
    };
    // re.match(r"^(?:100\b|connected\b)", state, re.I): the \b after the word fires only
    // at end-of-string or before a non-word character.
    auto word_then_boundary = [&](size_t n, const char* word) {
        return starts_ci(word) && (state.size() == n || !is_word_char(state[n]));
    };
    bool associated_state = word_then_boundary(3, "100") || word_then_boundary(9, "connected");
    if (!associated_state)
        return "";
    CmdResult r = run_cmd({"iw", "dev", STA_IFACE, "link"}, false, 15.0);
    if (r.rc != 0)
        return "";
    std::string freq_str;
    bool connected = false, has_ssid = false;
    for (const std::string& line : py_splitlines(r.out)) {
        if (line.rfind("Connected to ", 0) == 0 || line.rfind("Connected to\t", 0) == 0)
            connected = true;
        std::string t = py_strip(line);
        if (t.rfind("SSID: ", 0) == 0 && t.size() > 6 && t[6] != ' ')
            has_ssid = true;
        if (t.rfind("freq: ", 0) == 0) {
            std::string v = t.substr(6);
            // ^\s*freq:\s*(\d+)(?:\.\d+)?\s*$  — digits with an optional fraction only.
            size_t i = 0;
            while (i < v.size() && v[i] >= '0' && v[i] <= '9')
                ++i;
            std::string digits = v.substr(0, i);
            bool tail_ok = true;
            if (i < v.size() && v[i] == '.') {
                ++i;
                size_t frac = 0;
                while (i < v.size() && v[i] >= '0' && v[i] <= '9') {
                    ++i;
                    ++frac;
                }
                tail_ok = frac > 0;
            }
            if (tail_ok && i == v.size() && !digits.empty())
                freq_str = digits;
        }
    }
    if (!connected || !has_ssid || freq_str.empty())
        return "";
    long long freq = std::strtoll(freq_str.c_str(), nullptr, 10);
    if (freq >= 2412 && freq <= 2472)
        return std::to_string((freq - 2407) / 5);
    if (freq == 2484)
        return "14";
    if (freq >= 5000 && freq < 5950)
        return std::to_string((freq - 5000) / 5);
    if (freq >= 5955 && freq <= 7115)
        return std::to_string((freq - 5950) / 5);
    return "";
}

// A live uplink channel that stayed unchanged across two observations.
std::string confirmed_uplink_channel(double delay_s = 2.0) {
    std::string first = live_uplink_channel();
    if (first.empty())
        return "";
    std::this_thread::sleep_for(std::chrono::duration<double>(delay_s));
    std::string second = live_uplink_channel();
    return second == first ? first : "";
}

std::string ap_live_channel() {
    CmdResult r = run_cmd({"iw", "dev", AP_IFACE, "info"}, false, 15.0);
    if (r.rc != 0)
        return "";
    std::string key = "channel";
    size_t pos = r.out.find(key);
    while (pos != std::string::npos) {
        size_t i = pos + key.size();
        if (i < r.out.size() && (r.out[i] == ' ' || r.out[i] == '\t')) {
            while (i < r.out.size() && (r.out[i] == ' ' || r.out[i] == '\t'))
                ++i;
            size_t start = i;
            while (i < r.out.size() && r.out[i] >= '0' && r.out[i] <= '9')
                ++i;
            if (i > start)
                return r.out.substr(start, i - start);
        }
        pos = r.out.find(key, pos + 1);
    }
    return "";
}

int ap_clients() {
    CmdResult r = run_cmd({"iw", "dev", AP_IFACE, "station", "dump"}, false, 15.0);
    if (r.rc != 0)
        return 0;
    int n = 0;
    size_t pos = 0;
    while ((pos = r.out.find("Station ", pos)) != std::string::npos) {
        ++n;
        pos += 8;
    }
    return n;
}

// Consecutive short hostapd runs recorded by ap.sh.
int ap_failures() {
    CmdResult r = ap_sh({"failures"});
    if (r.rc != 0)
        return 0;
    try {
        return std::max(0, std::stoi(py_strip(r.out)));
    } catch (...) {
        return 0;
    }
}

void reset_ap_failures() {
    ap_sh({"reset-failures"});
}

// Restart dji-ap off the request thread.
//
// `systemctl restart` takes the AP down before it returns, tearing down the very TCP
// connection this reply has to travel over: without the thread the PC client always
// sees "the Pi did not answer", even on a successful join. The delay lets the response
// flush first AND gives the client (Windows) time to notice the AP is really gone
// before the fresh one appears — an immediate bounce leaves Windows glued to the
// old association while the Pi is already mid-restart, so the client's explicit
// reconnect either runs against nothing or fights a half-dead link.
void restart_ap_async(const std::string& reason, double delay = 2.5) {
    std::thread([reason, delay] {
        std::this_thread::sleep_for(std::chrono::duration<double>(delay));
        std::printf("[netctl] restarting %s: %s\n", AP_SERVICE, reason.c_str());
        std::fflush(stdout);
        systemctl({"restart", AP_SERVICE});
    }).detach();
}

// Bring the AP back if anything about it is wrong. Never lets a failed uplink
// operation leave the Pi with no way in.
void ensure_ap(const std::string& reason = "") {
    if (!hostapd_mode())
        return;
    auto [ok, why] = ap_health();
    if (ok)
        return;
    if (ap_recovering())
        return;
    int failures = ap_failures();
    if (failures >= AP_AUTO_RESTART_LIMIT) {
        std::printf("[netctl] AP unhealthy (%s); watchdog restart suppressed after "
                    "%d short failures — systemd recovery remains active\n",
                    why.c_str(), failures);
        std::fflush(stdout);
        return;
    }
    std::printf("[netctl] AP unhealthy (%s); restarting%s\n", why.c_str(),
                reason.empty() ? "" : (" — " + reason).c_str());
    std::fflush(stdout);
    restart_ap_async(!why.empty() ? why : reason);
}

// ---------------------------------------------------------------- AP (NM fallback)

// Create the uap0 virtual interface if the driver allows AP+STA concurrency.
bool ensure_ap_iface() {
    CmdResult r = run_cmd({"iw", "dev"});
    if (contains(r.out, AP_IFACE))
        return true;
    CmdResult combos = run_cmd({"iw", "list"});
    if (contains(combos.out, "valid interface combinations") && !contains(combos.out, "AP")) {
        std::printf("[netctl] this radio reports no AP capability:\n%s\n",
                    combos.out.substr(0, 400).c_str());
        return false;
    }
    r = run_cmd({"iw", "dev", STA_IFACE, "interface", "add", AP_IFACE, "type", "__ap"});
    if (r.rc != 0) {
        std::printf("[netctl] could not create %s: %s\n", AP_IFACE, r.out.c_str());
        return false;
    }
    run_cmd({"ip", "link", "set", AP_IFACE, "up"});
    return true;
}

// Define the AP connection. ipv4.method=shared gives DHCP + NAT for free, which is
// what routes the laptop's traffic out through whatever uplink wlan0 has.
void ensure_ap_profile() {
    CmdResult r = nmcli({"-t", "-f", "NAME", "con", "show"});
    for (const std::string& line : py_splitlines(r.out))
        if (line == AP_CON)
            return;
    nmcli({"con", "add", "type", "wifi", "ifname", AP_IFACE, "con-name", AP_CON, "autoconnect",
           "yes", "ssid", AP_SSID},
          true);
    nmcli({"con", "modify", AP_CON, "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
           "ipv4.method", "shared", "ipv4.addresses", std::string(AP_ADDR) + "/24",
           "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", AP_PSK,
           "connection.autoconnect-priority", "10"},
          true);
}

// Make the AP actually route to the uplink.
//
// ipv4.method=shared is supposed to set up ip_forward + NAT itself, but it does that
// through iptables/nftables and dnsmasq — on a Lite image where those are missing NM
// still brings the AP up, so a laptop associates, gets an address and has no way out.
// Re-asserting the two pieces here is idempotent and costs nothing when NM did its job.
void ensure_forwarding() {
    run_cmd({"sysctl", "-w", "net.ipv4.ip_forward=1"});
    CmdResult r = run_cmd({"iptables", "-t", "nat", "-S", "POSTROUTING"});
    if (r.rc != 0) {
        std::printf("[netctl] iptables unavailable; cannot verify NAT for the AP\n");
        return;
    }
    if (contains(r.out, "MASQUERADE"))
        return; // NM (or a previous run) already set it up
    std::printf("[netctl] no NAT rule found; adding masquerade %s -> %s\n", AP_IFACE, STA_IFACE);
    run_cmd({"iptables", "-t", "nat", "-A", "POSTROUTING", "-o", STA_IFACE, "-j", "MASQUERADE"});
    run_cmd({"iptables", "-A", "FORWARD", "-i", STA_IFACE, "-o", AP_IFACE, "-m", "state", "--state",
             "RELATED,ESTABLISHED", "-j", "ACCEPT"});
    run_cmd({"iptables", "-A", "FORWARD", "-i", AP_IFACE, "-o", STA_IFACE, "-j", "ACCEPT"});
}

Json hotspot(bool on) {
    Json res = Json::object();
    if (hostapd_mode()) {
        if (on) {
            set_ap_off_flag(false);
            // This endpoint is an explicit operator request, unlike the watchdog. Clear
            // the diagnostic latch and request an immediate attempt; systemd continues
            // low-rate attempts if the firmware is still settling.
            reset_ap_failures();
            systemctl({"reset-failed", AP_SERVICE});
            systemctl({"start", AP_SERVICE});
            for (int i = 0; i < 10; ++i) { // the unit is Type=simple; give hostapd a moment
                auto [ok, why] = ap_health();
                if (ok) {
                    res.set("ok", Json::boolean_of(true));
                    res.set("output", Json::string("ap up"));
                    res.set("mode", Json::string("hostapd"));
                    return res;
                }
                std::this_thread::sleep_for(std::chrono::seconds(1));
            }
            auto [ok, why] = ap_health();
            res.set("ok", Json::boolean_of(ok));
            res.set("output", Json::string(why));
            res.set("mode", Json::string("hostapd"));
            return res;
        }
        // Flag first: the watchdog would otherwise see a stopped AP and put it back.
        set_ap_off_flag(true);
        CmdResult r = systemctl({"stop", AP_SERVICE});
        ap_sh({"down"}); // explicit off: silence uap0 and remove NAT
        res.set("ok", Json::boolean_of(r.rc == 0));
        res.set("output", Json::string(r.out));
        res.set("mode", Json::string("hostapd"));
        res.set("note",
                Json::string("the AP stays off until /hotspot on, or until the next reboot"));
        return res;
    }
    // Legacy NetworkManager AP fallback.
    if (on) {
        if (!ensure_ap_iface()) {
            res.set("ok", Json::boolean_of(false));
            res.set("error", Json::string("no AP-capable interface (uap0 could not be created)"));
            return res;
        }
        ensure_ap_profile();
        CmdResult r = nmcli({"con", "up", AP_CON});
        if (r.rc == 0)
            ensure_forwarding();
        res.set("ok", Json::boolean_of(r.rc == 0));
        res.set("output", Json::string(r.out));
        res.set("mode", Json::string("nm"));
        return res;
    }
    CmdResult r = nmcli({"con", "down", AP_CON});
    res.set("ok", Json::boolean_of(r.rc == 0));
    res.set("output", Json::string(r.out));
    res.set("mode", Json::string("nm"));
    return res;
}

// ---------------------------------------------------------------- scan

// Undo every way a radio can be off. A Pi that was rebooted mid-experiment can come
// up soft-blocked or with wlan0 left unmanaged, and then every connect fails with a
// message that says nothing about the real cause.
void wifi_radio_on() {
    run_cmd({"rfkill", "unblock", "wifi"}, false, 15.0);
    nmcli({"radio", "wifi", "on"}, false, 30.0);
    nmcli({"dev", "set", STA_IFACE, "managed", "yes"}, false, 30.0);
}

// Raw scan rows [in_use, ssid, signal, security, chan] from the client interface.
//
// Rescan on wlan0 only — the AP interface must not leave its channel or connected
// laptops drop. `--rescan yes` blocks until the scan finishes; on an nmcli too old for
// it, fall back to an explicit rescan plus a wait.
std::vector<std::vector<std::string>> scan_rows() {
    const char* fields = "IN-USE,SSID,SIGNAL,SECURITY,CHAN";
    CmdResult r =
        nmcli({"-t", "-f", fields, "dev", "wifi", "list", "--rescan", "yes", "ifname", STA_IFACE},
              false, 60.0);
    if (r.rc != 0) {
        nmcli({"dev", "wifi", "rescan", "ifname", STA_IFACE}, false, 45.0);
        std::this_thread::sleep_for(std::chrono::seconds(2));
        r = nmcli({"-t", "-f", fields, "dev", "wifi", "list", "ifname", STA_IFACE});
    }
    std::vector<std::vector<std::string>> rows;
    if (r.rc != 0)
        return rows;
    for (const std::string& line : py_splitlines(r.out)) { // py iterates out.split("\n")
        if (py_strip(line).empty())
            continue;
        std::vector<std::string> parts = split_nmcli(line);
        if (parts.size() >= 5)
            rows.emplace_back(parts.begin(), parts.begin() + 5);
    }
    return rows;
}

struct NetEntry {
    std::string ssid;
    int signal = 0;
    std::string security;
    bool in_use = false;
};

// Visible networks, strongest first.
std::vector<NetEntry> scan() {
    std::vector<NetEntry> nets; // dict keyed by ssid, insertion order kept
    for (const auto& row : scan_rows()) {
        const std::string& in_use = row[0];
        const std::string& ssid = row[1];
        if (ssid.empty())
            continue; // hidden network
        int sig = 0;
        try {
            sig = std::stoi(row[2]);
        } catch (...) {
        }
        // The same SSID appears once per band/AP; keep the strongest.
        auto it = std::find_if(nets.begin(), nets.end(),
                               [&](const NetEntry& n) { return n.ssid == ssid; });
        if (it == nets.end() || sig > it->signal) {
            NetEntry n{ssid, sig, row[3].empty() ? "open" : row[3], in_use == "*"};
            if (it == nets.end())
                nets.push_back(n);
            else
                *it = n;
        }
    }
    std::stable_sort(nets.begin(), nets.end(),
                     [](const NetEntry& a, const NetEntry& b) { return a.signal > b.signal; });
    return nets;
}

struct ScanEntry {
    int signal = 0;
    std::string security;
    std::string chan;
};

// The strongest scan row for `ssid`, or none when it is not in range/not broadcast.
std::optional<ScanEntry> scan_entry(const std::string& ssid) {
    std::optional<ScanEntry> best;
    for (const auto& row : scan_rows()) {
        if (row[1] != ssid)
            continue;
        int sig = 0;
        try {
            sig = std::stoi(row[2]);
        } catch (...) {
        }
        if (!best || sig > best->signal)
            best = ScanEntry{sig, row[3], row[4]};
    }
    return best;
}

// ---------------------------------------------------------------- saved profiles

struct Profile {
    std::string uuid, name, ssid, filename, iface;

    // Our own access point, under any of the names it can have. Deleting it would
    // take the Pi off the air, so it is excluded from every cleanup path.
    bool is_ap() const {
        return iface == AP_IFACE || name == AP_CON || ssid == AP_SSID;
    }
};

// Every saved Wi-Fi profile.
//
// Two steps on purpose. Setting properties like 802-11-wireless.ssid are NOT valid
// fields for the `con show` *list* — nmcli rejects them with rc=2 and prints
// "invalid field", which is easy to mistake for "no profiles matched". The SSID can
// only be read per profile, so list first (UUID/NAME/TYPE/FILENAME are all valid list
// fields), then query each Wi-Fi profile by UUID.
std::vector<Profile> wifi_profiles() {
    std::vector<Profile> out;
    CmdResult r = nmcli({"-t", "-f", "UUID,NAME,TYPE,FILENAME", "con", "show"});
    if (r.rc != 0) {
        std::printf("[netctl] could not list connections: %s\n", r.out.c_str());
        return out;
    }
    for (const std::string& line : py_splitlines(r.out)) {
        std::vector<std::string> parts = split_nmcli(line);
        if (parts.size() < 4)
            continue;
        const std::string& typ = parts[2];
        if (!contains(typ, "wireless") && !contains(typ, "wifi"))
            continue;
        Profile p;
        p.uuid = parts[0];
        p.name = parts[1];
        p.filename = parts[3];
        p.ssid = nmcli_get("802-11-wireless.ssid", "con", "show", "uuid", p.uuid);
        p.iface = nmcli_get("connection.interface-name", "con", "show", "uuid", p.uuid);
        out.push_back(std::move(p));
    }
    return out;
}

// The profile currently active on wlan0, so a failed join can put it back.
std::optional<Profile> active_uplink() {
    CmdResult r = nmcli({"-t", "-f", "NAME,UUID,DEVICE", "con", "show", "--active"});
    if (r.rc != 0)
        return std::nullopt;
    for (const std::string& line : py_splitlines(r.out)) {
        std::vector<std::string> parts = split_nmcli(line);
        if (parts.size() >= 3 && parts[2] == STA_IFACE) {
            Profile p;
            p.uuid = parts[1];
            p.name = parts[0];
            p.iface = STA_IFACE;
            return p;
        }
    }
    return std::nullopt;
}

// The name the new profile is built and tested under.
//
// Deliberately NOT the SSID: a profile named after the SSID is very likely the one
// the Pi is using right now, and creating ours from scratch starts by deleting the
// name it is going to take. Building under a staging name means nothing that works
// is destroyed before the replacement has proved itself; on success the profile is
// renamed to the SSID (see connect()), which is what nmcli's own `dev wifi connect`
// would have called it and what the PC client shows as the uplink.
std::string staging_name(const std::string& ssid) {
    std::string slug;
    for (unsigned char c : ssid) {
        if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
            c == '.' || c == '_' || c == '-')
            slug += static_cast<char>(c);
        else
            slug += '_';
    }
    // re.sub(...)[:48] — Python slices by code point; SSIDs that matter here are ASCII.
    if (slug.size() > 48)
        slug.resize(48);
    if (slug.empty())
        slug = "net";
    return std::string(UPLINK_PREFIX) + slug;
}

// The SSID wlan0 is actually associated with, straight from the driver — the NM
// profile name is only a label and need not match.
std::string uplink_ssid() {
    CmdResult r = run_cmd({"iw", "dev", STA_IFACE, "link"}, false, 15.0);
    if (r.rc != 0)
        return "";
    for (const std::string& line : py_splitlines(r.out)) {
        std::string t = py_strip(line);
        if (t.rfind("SSID: ", 0) == 0)
            return py_strip(t.substr(6));
    }
    return "";
}

std::string uplink_ip() {
    CmdResult r = run_cmd({"ip", "-4", "-o", "addr", "show", "dev", STA_IFACE}, false, 15.0);
    if (r.rc != 0)
        return "";
    size_t pos = r.out.find("inet ");
    if (pos == std::string::npos)
        return "";
    for (size_t i = pos + 5; i < r.out.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(r.out[i]);
        if (c == ' ' || c == '\t')
            continue;
        if (c >= 0x80)
            continue; // inet\s+(\S+): a non-space byte also terminates in py's re
        size_t end = i;
        while (end < r.out.size() && r.out[end] != ' ' && r.out[end] != '\t' &&
               r.out[end] != '\n' && r.out[end] != '\r' && r.out[end] != '\v' && r.out[end] != '\f')
            ++end;
        return r.out.substr(i, end - i);
    }
    return "";
}

// The nmcli properties for this network's security, or nullopt if we cannot do it.
//
// THE point of this function: `key-mgmt` is set here, explicitly, always. Relying on
// `nmcli dev wifi connect <ssid> password <psk>` instead is what produced
// "802-11-wireless-security.key-mgmt: property is missing" — that command hands NM a
// profile carrying only a PSK and expects the daemon to infer key-mgmt from the scan
// entry, which fails whenever the AP is not in the scan cache at that instant (right
// after a disconnect, a re-join, a hidden SSID) and has regressed outright in several
// NetworkManager releases. A profile that states its own key-mgmt has nothing to infer.
std::optional<std::vector<std::string>> security_args(const std::optional<ScanEntry>& entry,
                                                      const std::string* psk) {
    std::string sec = to_upper(entry ? entry->security : "");
    if (sec == "--" || sec == "OPEN" || sec == "NONE")
        sec = "";
    if (contains(sec, "802.1X") || contains(sec, "EAP"))
        return std::nullopt; // WPA-Enterprise: needs credentials we do not have
    if (contains(sec, "OWE"))
        return std::vector<std::string>{"802-11-wireless-security.key-mgmt",
                                        "owe"}; // "enhanced open", no password
    // An open network stays open even if a password was typed in: attaching a security
    // section to it makes the association fail outright, which reads as "wrong password"
    // on a network that has none.
    if (entry && sec.empty())
        return std::vector<std::string>{};
    if (!psk || psk->empty())
        return std::vector<std::string>{}; // open network (or a psk-less retry)
    if (contains(sec, "WEP") && !contains(sec, "WPA"))
        return std::vector<std::string>{"802-11-wireless-security.key-mgmt",      "none",
                                        "802-11-wireless-security.wep-key0",      *psk,
                                        "802-11-wireless-security.wep-key-flags", "0"};
    // 'wpa-psk' covers WPA2 and WPA2/WPA3-transition APs. Only a WPA3-ONLY AP needs
    // 'sae', and it rejects wpa-psk. An unknown/absent scan entry means a hidden network,
    // where wpa-psk is overwhelmingly the right guess.
    if (contains(sec, "WPA3") && !contains(sec, "WPA2") && !contains(sec, "WPA1"))
        return std::vector<std::string>{
            "802-11-wireless-security.key-mgmt", "sae", "802-11-wireless-security.psk", *psk,
            // 0 = system-owned. The default (agent-owned) makes NM wait for a secret
            // agent that does not exist on a headless Pi and fail with
            // "(7) Secrets were required, but not provided".
            "802-11-wireless-security.psk-flags", "0", "802-11-wireless-security.pmf", "2"};
    return std::vector<std::string>{"802-11-wireless-security.key-mgmt",  "wpa-psk",
                                    "802-11-wireless-security.psk",       *psk,
                                    "802-11-wireless-security.psk-flags", "0"};
}

// Create the uplink profile from scratch, fully specified, in ONE nmcli call.
//
// From scratch because a profile that already exists may carry exactly the half-filled
// security section we are trying to get away from. In one call because setting
// key-mgmt in a separate `con modify` from the properties it depends on can itself
// fail validation.
std::pair<bool, std::string> build_profile(const std::string& name, const std::string& ssid,
                                           const std::string* psk,
                                           const std::optional<ScanEntry>& entry) {
    std::optional<std::vector<std::string>> sec = security_args(entry, psk);
    if (!sec)
        return {false, "'" + ssid +
                           "' is a WPA-Enterprise (802.1X) network; "
                           "netctl can only join personal WPA/WPA2/WPA3 networks"};
    nmcli({"con", "delete", "id", name}); // ignore rc: usually "unknown connection"
    std::vector<std::string> args = {
        "con", "add", "type", "wifi", "con-name", name, "ifname", STA_IFACE, "ssid", ssid,
        "connection.autoconnect", "yes", "connection.autoconnect-priority", "5",
        "802-11-wireless.mode", "infrastructure",
        // Keep the station's hardware MAC: uap0's address is derived from it, and a
        // randomised one also breaks DHCP reservations and some routers' ACLs.
        "802-11-wireless.cloned-mac-address", "permanent",
        // 2 = disable. Wi-Fi power save is a documented cause of a Raspberry Pi
        // becoming unreachable minutes after it connects, and it makes the brcmfmac
        // AP+STA combination markedly less stable.
        "802-11-wireless.powersave", "2", "ipv4.method", "auto",
        // Without this, activation "succeeds" on a network where DHCP never answered
        // as long as IPv6 came up — and then the AP is NATed to nowhere.
        "ipv4.may-fail", "no", "ipv6.method", "auto", "ipv6.may-fail", "yes"};
    if (!entry) {
        // Not in the scan: either out of range, or the SSID is not broadcast. Marking it
        // hidden makes NM probe for it by name, which is the only way to join the latter.
        args.push_back("802-11-wireless.hidden");
        args.push_back("yes");
    }
    args.insert(args.end(), sec->begin(), sec->end());
    CmdResult r = nmcli(args);
    return {r.rc == 0, r.out};
}

// Leave the access point in the right state after the uplink changed; return the
// note the PC client shows the user.
//
// A successful join deliberately requests one clean restart even when the channel did
// not change. brcmfmac can retune the virtual AP without making Windows notice that its
// old association is unusable; an explicit AP cycle plus the PC-side reconnect avoids
// that half-connected state. Failed joins and uplink disconnects do not force a cycle.
std::string finish_ap_for_uplink(bool force_reconnect = false) {
    run_cmd({"iw", "dev", STA_IFACE, "set", "power_save", "off"}, false, 15.0);
    if (!hostapd_mode()) {
        nmcli({"con", "up", AP_CON});
        ensure_forwarding(); // the uplink is new — make sure it is NATed
        return "AP re-applied (NetworkManager fallback)";
    }
    auto [healthy, why] = ap_health();
    if (!healthy) {
        reset_ap_failures();
        systemctl({"reset-failed", AP_SERVICE});
        restart_ap_async(why);
        return "AP is restarting — reconnect the laptop if it dropped";
    }
    std::string wanted = ap_wanted_channel();
    std::string current = ap_conf_channel();
    if (!wanted.empty() && !current.empty() && wanted != current) {
        reset_ap_failures();
        systemctl({"reset-failed", AP_SERVICE});
        restart_ap_async("channel " + current + " -> " + wanted);
        return "AP retunes to the uplink channel — reconnect the laptop if it dropped";
    }
    if (force_reconnect) {
        reset_ap_failures();
        systemctl({"reset-failed", AP_SERVICE});
        restart_ap_async("uplink connected; refreshing AP for client reassociation");
        return "AP is refreshing — the PC client will reconnect to it";
    }
    return "AP unchanged — the laptop stays connected";
}

// Join a network as the uplink, keeping the AP up.
//
// Contract: whatever happens, this call never leaves the Pi worse off than it found
// it. The AP is up when it returns, and if the join fails the connection that was
// active before is put back — the previous version deleted the saved profiles up
// front, so a failed join could strand a Pi with no network at all and no way to
// reach it from either side.
Json connect(const std::string& ssid, const std::string* psk) {
    Json res = Json::object();
    if (ssid == AP_SSID) {
        res.set("ok", Json::boolean_of(false));
        res.set("output", Json::string("'" + ssid + "' is this Pi's own access point"));
        return res;
    }

    wifi_radio_on();
    std::optional<Profile> prev = active_uplink();
    std::optional<ScanEntry> entry = scan_entry(ssid);
    std::string name = staging_name(ssid);

    // No password given for a network that needs one: this is "reconnect to something I
    // already know", not "join with an empty password". Use the saved secret rather than
    // replacing the profile with a passwordless one that cannot possibly associate.
    bool secured = false;
    if (entry) {
        std::string s = entry->security;
        size_t b = s.find_first_not_of(" -");
        if (b != std::string::npos) {
            size_t e = s.find_last_not_of(" -");
            secured = e >= b; // any character left after strip(" -")
        }
    }
    if ((!psk || psk->empty()) && secured) {
        for (const Profile& p : wifi_profiles()) {
            if (p.ssid != ssid || p.is_ap())
                continue;
            CmdResult r = nmcli({"--wait", "45", "con", "up", "uuid", p.uuid});
            if (r.rc == 0) {
                std::string ap_note = finish_ap_for_uplink(true);
                res.set("ok", Json::boolean_of(true));
                res.set("output", Json::string(r.out));
                res.set("note", Json::string("reconnected using the saved password for '" + ssid +
                                             "'; " + ap_note));
                return res;
            }
        }
        ensure_ap("reconnect without a password failed");
        res.set("ok", Json::boolean_of(false));
        res.set("output", Json::string("'" + ssid + "' needs a password and none is saved for it"));
        return res;
    }

    auto [ok, out] = build_profile(name, ssid, psk, entry);
    if (!ok) {
        ensure_ap("profile creation failed");
        res.set("ok", Json::boolean_of(false));
        res.set("output", Json::string("could not create the profile for '" + ssid + "': " + out));
        return res;
    }

    // Other saved profiles for the same SSID would win the next autoconnect race with a
    // stale password. Park them instead of deleting them: if this join fails they are
    // the Pi's way back onto the network, and that is exactly when we must not have
    // thrown them away.
    std::vector<Profile> others;
    for (const Profile& p : wifi_profiles())
        if (p.ssid == ssid && p.name != name && !p.is_ap())
            others.push_back(p);
    for (const Profile& p : others)
        nmcli({"con", "modify", "uuid", p.uuid, "connection.autoconnect", "no"});

    CmdResult r = nmcli({"--wait", "45", "con", "up", "id", name});
    ok = r.rc == 0;
    out = r.out;

    if (!ok && hostapd_mode() && ap_active() && entry) {
        // One radio: associating on a channel the AP is not on is the fragile case on
        // brcmfmac. Give the station the radio to itself for the retry — the AP comes
        // back either way, and the PC client already re-probes after a join.
        const std::string& target = entry->chan;
        if (!target.empty() && target != ap_conf_channel()) {
            std::printf("[netctl] retrying the join with the AP stopped "
                        "(uplink is on channel %s, AP on %s)\n",
                        target.c_str(),
                        ap_conf_channel().empty() ? "?" : ap_conf_channel().c_str());
            std::fflush(stdout);
            systemctl({"stop", AP_SERVICE});
            std::this_thread::sleep_for(std::chrono::seconds(1));
            CmdResult r2 = nmcli({"--wait", "45", "con", "up", "id", name});
            ok = r2.rc == 0;
            out = ok ? r2.out : out + "\nretry without the AP: " + r2.out;
            systemctl({"start", AP_SERVICE});
        }
    }

    if (ok) {
        for (const Profile& p : others) { // proven redundant now, and only now
            nmcli({"con", "delete", "uuid", p.uuid});
            std::printf("[netctl] removed duplicate profile '%s' for '%s'\n", p.name.c_str(),
                        ssid.c_str());
            std::fflush(stdout);
        }
        // The duplicates are gone, so the SSID is free as a name. Take it: the PC client
        // shows the uplink by profile name and matches it against the SSID it asked for.
        if (nmcli({"con", "modify", "id", name, "connection.id", ssid}).rc == 0)
            name = ssid;
    } else {
        nmcli({"con", "delete", "id", name});
        for (const Profile& p : others) // undo the parking
            nmcli({"con", "modify", "uuid", p.uuid, "connection.autoconnect", "yes"});
        if (prev) // put the Pi back where it was
            nmcli({"--wait", "30", "con", "up", "uuid", prev->uuid});
        if (contains(out, "Secrets were required") || contains(to_upper(out), "NO SECRETS"))
            out += "  (wrong password?)";
    }

    res.set("ok", Json::boolean_of(ok));
    res.set("output", Json::string(out));
    res.set("note", Json::string(finish_ap_for_uplink(ok)));
    return res;
}

Json disconnect() {
    CmdResult r = nmcli({"--wait", "30", "dev", "disconnect", STA_IFACE});
    // Deliberately NOT restarting the AP here. The old code always did, which dropped
    // every laptop on the Pi's own network the moment the uplink went away — the exact
    // opposite of what the AP is for. With the uplink gone the radio is free and hostapd
    // keeps beaconing on its channel, so the only reason to restart is that the AP is
    // actually broken.
    std::string note = "uplink down; the access point is unchanged";
    if (hostapd_mode()) {
        auto [healthy, why] = ap_health();
        if (!healthy) {
            restart_ap_async(why);
            note = "uplink down; the access point is restarting (" + why + ")";
        }
    }
    Json res = Json::object();
    res.set("ok", Json::boolean_of(r.rc == 0));
    res.set("output", Json::string(r.out));
    res.set("note", Json::string(note));
    return res;
}

// ---------------------------------------------------------------- status

std::pair<double, bool> g_internet_cache{0.0, false};
std::mutex g_internet_lock;
std::atomic<bool> g_stop{false};

// Return the last probe result immediately; never block the local control API.
bool have_internet() {
    std::lock_guard<std::mutex> lk(g_internet_lock);
    return g_internet_cache.second;
}

// Probe the optional uplink and cache the result for future /status calls.
bool refresh_internet() {
    CmdResult r = run_cmd({"ping", "-c", "1", "-W", "1", "-I", STA_IFACE, "1.1.1.1"}, false, 4.0);
    std::lock_guard<std::mutex> lk(g_internet_lock);
    g_internet_cache = {
        std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(),
        r.rc == 0};
    return r.rc == 0;
}

void internet_monitor() {
    while (!g_stop.load()) {
        refresh_internet();
        for (int i = 0; i < 50 && !g_stop.load(); ++i)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
}

// Command-free Pi identity used for discovery when the uplink is offline.
Json healthz() {
    Json j = Json::object();
    j.set("ok", Json::boolean_of(true));
    j.set("service", Json::string("dji-link-netctl"));
    j.set("address", Json::string(AP_ADDR));
    j.set("ap_ssid", Json::string(AP_SSID));
    return j;
}

Json status() {
    CmdResult devs = nmcli({"-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev"}, false, 30.0);
    bool hostapd = hostapd_mode();
    Json ap_up;
    ap_up.type = Json::Null;
    Json uplink;
    uplink.type = Json::Null;
    for (const std::string& line : py_splitlines(devs.out)) { // py iterates split("\n")
        std::vector<std::string> parts = split_nmcli(line);
        if (parts.size() < 4)
            continue;
        const std::string& dev = parts[0];
        const std::string& state = parts[2];
        const std::string& con = parts[3];
        // In hostapd mode uap0 is NM-unmanaged; its state comes from the unit below.
        if (dev == AP_IFACE && !hostapd) {
            ap_up = Json::object();
            ap_up.set("iface", Json::string(dev));
            ap_up.set("state", Json::string(state));
            ap_up.set("connection", Json::string(con));
            ap_up.set("ssid", Json::string(AP_SSID));
            ap_up.set("address", Json::string(AP_ADDR));
        } else if (dev == STA_IFACE) {
            uplink = Json::object();
            uplink.set("iface", Json::string(dev));
            uplink.set("state", Json::string(state));
            uplink.set("connection", Json::string(con));
        }
    }
    if (hostapd) {
        std::string act = systemctl({"is-active", AP_SERVICE}).out;
        act = py_strip(act);
        ap_up = Json::object();
        ap_up.set("iface", Json::string(AP_IFACE));
        ap_up.set("state", Json::string(act.empty() ? "unknown" : act));
        ap_up.set("connection", Json::string(AP_SERVICE));
        ap_up.set("ssid", Json::string(AP_SSID));
        ap_up.set("address", Json::string(AP_ADDR));
        ap_up.set("mode", Json::string("hostapd"));
    }
    CmdResult addr = run_cmd({"hostname", "-I"}, false, 15.0);
    auto [healthy, why] = ap_health();
    std::string ap_channel = ap_live_channel();
    if (ap_channel.empty())
        ap_channel = ap_conf_channel();
    // Key order matters: the C++ client reads "state"/"connection" relative to the "ap"
    // and "uplink" keys, so those two objects stay first and keep their shape. Anything
    // new is appended.
    Json j = Json::object();
    j.set("ap", ap_up);
    j.set("uplink", uplink);
    j.set("internet", Json::boolean_of(have_internet()));
    Json addresses = Json::array();
    for (const std::string& a : py_split_ws(addr.out))
        addresses.arr.push_back(Json::string(a));
    j.set("addresses", addresses);
    j.set("ap_ssid", Json::string(AP_SSID));
    j.set("ap_psk", Json::string(AP_PSK));
    j.set("ap_healthy", Json::boolean_of(healthy));
    j.set("ap_detail", Json::string(why));
    j.set("ap_channel", Json::string(ap_channel));
    j.set("ap_clients", Json::integer(hostapd ? ap_clients() : 0));
    j.set("ap_failures", Json::integer(hostapd ? ap_failures() : 0));
    j.set("uplink_ssid", Json::string(uplink_ssid()));
    j.set("uplink_ip", Json::string(uplink_ip()));
    j.set("service", Json::string("dji-link-netctl"));
    return j;
}

struct DocCheck {
    std::string name;
    bool ok;
    std::string detail;
};

struct DoctorResult {
    Json json;
    std::vector<DocCheck> checks;
    bool ok;
};

// Everything needed to tell where the network went, in one place.
DoctorResult doctor() {
    DoctorResult d;
    Json checks_arr = Json::array();
    auto add = [&](const std::string& name, bool ok, std::string detail) {
        detail = py_strip(detail).substr(0, 600);
        d.checks.push_back({name, ok, detail});
        Json c = Json::object();
        c.set("check", Json::string(name));
        c.set("ok", Json::boolean_of(ok));
        c.set("detail", Json::string(detail));
        checks_arr.arr.push_back(c);
    };

    add("hostapd mode", hostapd_mode(),
        std::string(AP_UNIT_PATH) + (file_exists(AP_UNIT_PATH) ? " present" : " missing"));
    add(std::string(AP_SERVICE) + ".service",
        py_strip(systemctl({"is-active", AP_SERVICE}).out) == "active",
        systemctl({"is-active", AP_SERVICE}).out);
    auto [hap, why] = ap_health();
    add("ap health", hap, why);
    CmdResult r = run_cmd({"iw", "dev"});
    add(std::string(AP_IFACE) + " exists", contains(r.out, std::string("Interface ") + AP_IFACE),
        r.out);
    r = run_cmd({"ip", "-4", "addr", "show", "dev", AP_IFACE});
    add(std::string(AP_IFACE) + " address", contains(r.out, AP_ADDR), r.out);
    std::string conf;
    {
        std::ifstream f(HOSTAPD_CONF);
        if (f) {
            std::stringstream ss;
            ss << f.rdbuf();
            conf = ss.str();
        } else {
            // str(FileNotFoundError) as Python formats it in the detail string.
            conf = "[Errno 2] No such file or directory: '" + std::string(HOSTAPD_CONF) + "'";
        }
    }
    add("hostapd.conf", contains(conf, "channel="), conf);
    r = ap_sh({"chan"});
    add("channel ap.sh would pick now", r.rc == 0, r.out);
    r = run_cmd({"iw", "reg", "get"});
    if (r.rc == 0) {
        std::string first_line = r.out.substr(0, r.out.find('\n'));
        add("regulatory domain", true, first_line);
    } else {
        add("regulatory domain", false, r.out);
    }
    r = nmcli({"-t", "-f", "DEVICE,STATE,CONNECTION", "dev"});
    add("NetworkManager devices", r.rc == 0, r.out);
    std::vector<Profile> profs = wifi_profiles();
    std::string profs_str;
    for (const Profile& p : profs) {
        if (!profs_str.empty())
            profs_str += ", ";
        profs_str += p.name + "[" + (p.ssid.empty() ? "?" : p.ssid) + "]";
    }
    add("saved Wi-Fi profiles", true, profs_str.empty() ? "none" : profs_str);
    r = run_cmd({"iptables", "-t", "nat", "-S", "POSTROUTING"});
    add("NAT", contains(r.out, "MASQUERADE"), r.out);
    r = run_cmd({"sysctl", "-n", "net.ipv4.ip_forward"});
    add("ip_forward", py_strip(r.out) == "1", r.out);
    add("internet", refresh_internet(), "ping 1.1.1.1 through wlan0");

    d.ok = true;
    for (const DocCheck& c : d.checks)
        d.ok = d.ok && c.ok;
    d.json = Json::object();
    d.json.set("ok", Json::boolean_of(d.ok));
    d.json.set("checks", checks_arr);
    return d;
}

// ---------------------------------------------------------------- HTTP API
//
// Raw POSIX sockets standing in for ThreadingHTTPServer + BaseHTTPRequestHandler:
// one detached thread per connection (daemon-like, as in ThreadingHTTPServer). Request
// parsing only needs to be as tolerant as what netctl.py actually relies on: request
// line (method + path), Content-Length header, and a JSON body for POST. getopt of
// paths uses startswith matching, exactly like the Python handlers.

const char* const SERVER_VERSION = "netctl/0.8.2"; // Handler.server_version in py

bool send_all(int fd, const std::string& data) {
    size_t off = 0;
    while (off < data.size()) {
        ssize_t n = ::send(fd, data.data() + off, data.size() - off, MSG_NOSIGNAL);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return false;
        }
        off += static_cast<size_t>(n);
    }
    return true;
}

void http_log(const std::string& addr, const std::string& msg) {
    std::printf("[netctl] %s %s\n", addr.c_str(), msg.c_str());
    std::fflush(stdout);
}

// HTTP-date, as BaseHTTPRequestHandler.date_time_string() formats it (GMT).
std::string http_date() {
    std::time_t t = std::time(nullptr);
    struct tm tmv;
    ::gmtime_r(&t, &tmv);
    char buf[40];
    std::strftime(buf, sizeof(buf), "%a, %d %b %Y %H:%M:%S GMT", &tmv);
    return buf;
}

void handle_connection(int fd, std::string peer_ip) {
    // Read the request head plus the body Content-Length promises; one read loop with a
    // guard cap is enough for the tiny bodies this API takes.
    std::string raw;
    std::array<char, 8192> buf;
    size_t need = 0; // 0 = headers not complete yet; otherwise full expected request size
    for (;;) {
        ssize_t n = ::recv(fd, buf.data(), buf.size(), 0);
        if (n <= 0) {
            ::close(fd);
            return; // client went away mid-request; BaseHTTPRequestHandler would do the same
        }
        raw.append(buf.data(), static_cast<size_t>(n));
        if (need == 0) {
            size_t he = raw.find("\r\n\r\n");
            if (he == std::string::npos) {
                if (raw.size() > 65536) {
                    ::close(fd); // absurd header block; bail
                    return;
                }
                continue;
            }
            // Content-Length, case-insensitive, default 0 (py: headers.get(...) or 0).
            long long cl = 0;
            std::string head = raw.substr(0, he);
            for (const std::string& line : py_splitlines(head)) {
                if (line.size() > 15 && iequals(line.substr(0, 15), "Content-Length:"))
                    cl = std::strtoll(line.c_str() + 15, nullptr, 10);
            }
            if (cl < 0)
                cl = 0;
            if (cl > 1048576) {
                ::close(fd); // not a body this API would ever accept; bail
                return;
            }
            need = he + 4 + static_cast<size_t>(cl);
        }
        if (raw.size() >= need)
            break;
    }

    size_t he = raw.find("\r\n\r\n");
    std::string head = raw.substr(0, he);
    std::string reqline = py_splitlines(head).empty() ? "" : py_splitlines(head)[0];
    std::vector<std::string> lp = py_split_ws(reqline);
    std::string method = lp.size() >= 1 ? lp[0] : "";
    std::string path = lp.size() >= 2 ? lp[1] : "";
    std::string body = raw.substr(he + 4, raw.size() - (he + 4));

    auto respond = [&](const Json& obj, int code) {
        std::string payload = json_dumps(obj);
        const char* phrase = code == 200 ? "OK" : code == 400 ? "Bad Request" : "Not Found";
        // Header set and ordering exactly as BaseHTTPRequestHandler's _send():
        // status line, Server, Date (both added by send_response), then Content-Type,
        // Content-Length, Access-Control-Allow-Origin from the handler.
        std::string resp = "HTTP/1.0 " + std::to_string(code) + " " + phrase + "\r\n";
        resp += std::string("Server: ") + SERVER_VERSION + " Python/3.11.2\r\n";
        resp += "Date: " + http_date() + "\r\n";
        resp += "Content-Type: application/json\r\n";
        resp += "Content-Length: " + std::to_string(payload.size()) + "\r\n";
        // The PC client is a local tool on the same link; no auth, no origin checks.
        resp += "Access-Control-Allow-Origin: *\r\n";
        resp += "\r\n";
        resp += payload;
        send_all(fd, resp);
        http_log(peer_ip,
                 "\"" + method + " " + path + " HTTP/1.1\" " + std::to_string(code) + " -");
    };

    if (method == "GET") {
        if (path.rfind("/healthz", 0) == 0)
            respond(healthz(), 200);
        else if (path.rfind("/status", 0) == 0)
            respond(status(), 200);
        else if (path.rfind("/scan", 0) == 0) {
            Json j = Json::object();
            Json nets = Json::array();
            for (const NetEntry& n : scan()) {
                Json e = Json::object();
                e.set("ssid", Json::string(n.ssid));
                e.set("signal", Json::integer(n.signal));
                e.set("security", Json::string(n.security));
                e.set("in_use", Json::boolean_of(n.in_use));
                nets.arr.push_back(e);
            }
            j.set("networks", nets);
            respond(j, 200);
        } else if (path.rfind("/doctor", 0) == 0) {
            respond(doctor().json, 200);
        } else {
            Json e = Json::object();
            e.set("error", Json::string("not found"));
            respond(e, 404);
        }
    } else if (method == "POST") {
        std::optional<Json> parsed = body.empty() ? Json(Json::object()) : json_loads(body);
        if (!parsed || parsed->type != Json::Obj) {
            Json e = Json::object();
            e.set("error", Json::string("bad json"));
            respond(e, 400);
        } else if (path.rfind("/connect", 0) == 0) {
            const Json* jssid = parsed->get("ssid");
            if (!jssid || jssid->type != Json::Str || jssid->str.empty()) {
                Json e = Json::object();
                e.set("error", Json::string("ssid required"));
                respond(e, 400);
            } else {
                const Json* jpsk = parsed->get("psk");
                std::string psk_v;
                const std::string* psk = nullptr;
                if (jpsk && jpsk->type == Json::Str) {
                    psk_v = jpsk->str;
                    psk = &psk_v;
                }
                respond(connect(jssid->str, psk), 200);
            }
        } else if (path.rfind("/disconnect", 0) == 0) {
            respond(disconnect(), 200);
        } else if (path.rfind("/hotspot", 0) == 0) {
            bool on = true; // body.get("on", True)
            const Json* jon = parsed->get("on");
            if (jon && jon->type == Json::Bool)
                on = jon->boolean;
            respond(hotspot(on), 200);
        } else {
            Json e = Json::object();
            e.set("error", Json::string("not found"));
            respond(e, 404);
        }
    } else {
        // BaseHTTPRequestHandler answers unknown methods with 501; this API is only
        // ever spoken to by our own client, which sends GET/POST, so treat the rest as
        // not found rather than growing a second error path.
        Json e = Json::object();
        e.set("error", Json::string("not found"));
        respond(e, 404);
    }
    ::shutdown(fd, SHUT_RDWR); // HTTP/1.0 without keep-alive: reply, then hang up
    ::close(fd);
}

void ap_watchdog() {
    // Keep the access point alive, whatever else happens.
    //
    // This is the last line of defence for the one thing that must never stay broken:
    // if hostapd died, if uap0 lost its address, if dnsmasq is gone or the NAT rule was
    // flushed, put it back. Restarts are backed off so a genuinely unstartable AP does
    // not turn into a second restart loop on top of systemd. The service itself retries
    // at a low rate without deleting uap0 or disconnecting wlan0.
    //
    // A healthy AP is never restarted merely because it has no clients or no uplink.
    if (!hostapd_mode())
        return;
    constexpr double CHECK_S = 15.0;
    double backoff = 30.0;
    double last_fix = 0.0;
    bool failure_latched = false;

    for (;;) {
        std::this_thread::sleep_for(std::chrono::duration<double>(CHECK_S));
        double now =
            std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch())
                .count();
        auto [healthy, why] = ap_health();
        if (!healthy) {
            // `Restart=always` reports activating during its retry delay and while
            // ap.sh pre waits for NetworkManager. A watchdog restart here kills that
            // valid attempt halfway through and resets the delay.
            if (ap_recovering())
                continue;
            int failures = ap_failures();
            if (failures >= AP_AUTO_RESTART_LIMIT) {
                if (!failure_latched) {
                    std::printf("[netctl] watchdog: AP remains unhealthy (%s); "
                                "%d short failures; systemd continues low-rate recovery\n",
                                why.c_str(), failures);
                    std::fflush(stdout);
                    failure_latched = true;
                }
                continue;
            }
            if (now - last_fix < backoff)
                continue;
            last_fix = now;
            backoff = std::min(backoff * 2, 300.0);
            std::printf("[netctl] watchdog: AP unhealthy (%s); restarting %s\n", why.c_str(),
                        AP_SERVICE);
            std::fflush(stdout);
            systemctl({"restart", AP_SERVICE});
            continue;
        }
        failure_latched = false;
        backoff = 30.0;

        // Retune only for a real, fully associated uplink whose channel stayed stable.
        // With no uplink (the normal field case), leave the healthy AP untouched: it
        // keeps beaconing, serving DHCP and exposing the Pi at 10.42.0.1.
        std::string wanted = confirmed_uplink_channel();
        std::string current = ap_conf_channel();
        if (!wanted.empty() && !current.empty() && wanted != current && now - last_fix >= backoff) {
            last_fix = now;
            std::printf("[netctl] watchdog: retuning %s for uplink channel %s -> %s\n", AP_SERVICE,
                        current.c_str(), wanted.c_str());
            std::fflush(stdout);
            systemctl({"restart", AP_SERVICE});
            continue;
        }
    }
}

int serve() {
    wifi_radio_on();
    // systemd starts and persistently recovers dji-ap independently. Do not turn netctl
    // startup into a second supervisor that resets its retry delay after a broken boot.
    if (!hostapd_mode())
        hotspot(true);
    std::thread(ap_watchdog).detach();
    std::thread(internet_monitor).detach();

    int srv = ::socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0) {
        std::fprintf(stderr, "[netctl] socket: %s\n", strerror_str(errno).c_str());
        return 1;
    }
    int one = 1;
    // ThreadingHTTPServer leaves SO_REUSEADDR alone (HTTPServer.allow_reuse_address=1 is
    // what sets it in py actually — ThreadingHTTPServer inherits HTTPServer, whose
    // allow_reuse_address defaults to 1: identical effect).
    ::setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    sockaddr_in sa{};
    sa.sin_family = AF_INET;
    sa.sin_port = htons(PORT);
    sa.sin_addr.s_addr = htonl(INADDR_ANY); // ("0.0.0.0", PORT)
    if (::bind(srv, reinterpret_cast<sockaddr*>(&sa), sizeof(sa)) != 0) {
        std::fprintf(stderr, "[netctl] bind :%d: %s\n", PORT, strerror_str(errno).c_str());
        ::close(srv);
        return 1;
    }
    if (::listen(srv, 5) != 0) { // HTTPServer.request_queue_size = 5
        std::fprintf(stderr, "[netctl] listen: %s\n", strerror_str(errno).c_str());
        ::close(srv);
        return 1;
    }
    std::signal(SIGPIPE, SIG_IGN);
    // Threaded: /scan takes seconds and /connect tens of seconds. On the old
    // single-threaded server either one blocked /status, and the PC client read that as
    // "the Pi stopped answering" in the middle of a perfectly good operation.
    std::printf("[netctl] API on :%d  (AP '%s' at %s)\n", PORT, AP_SSID.c_str(), AP_ADDR);
    std::fflush(stdout);
    for (;;) {
        sockaddr_in ca{};
        socklen_t clen = sizeof(ca);
        int fd = ::accept(srv, reinterpret_cast<sockaddr*>(&ca), &clen);
        if (fd < 0) {
            if (errno == EINTR)
                continue;
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            continue;
        }
        char ip[INET_ADDRSTRLEN] = "?";
        ::inet_ntop(AF_INET, &ca.sin_addr, ip, sizeof(ip)); // handler.address_string()
        std::thread(handle_connection, fd, std::string(ip)).detach();
    }
}

} // namespace

int main(int argc, char** argv) {
    {
        // Python: HERE = dirname(abspath(__file__)); AP_SH = join(HERE, "ap.sh").
        std::string exe = argv[0] ? argv[0] : "";
        std::vector<char> pathbuf(4096);
        ssize_t n = ::readlink("/proc/self/exe", pathbuf.data(), pathbuf.size() - 1);
        if (n < 0 && exe.find('/') != std::string::npos) {
            char* rp = ::realpath(exe.c_str(), nullptr);
            if (rp) {
                exe = rp;
                std::free(rp);
                n = 1; // resolved
            }
        } else if (n > 0) {
            pathbuf[static_cast<size_t>(n)] = '\0';
            exe = pathbuf.data();
        }
        size_t slash = exe.rfind('/');
        AP_SH = (slash == std::string::npos ? std::string(".") : exe.substr(0, slash)) + "/ap.sh";
    }

    if (argc < 2) {
        std::printf("%s", USAGE);
        return 2;
    }
    std::string cmd = argv[1];
    if (cmd == "status") {
        std::printf("%s\n", json_dumps(status(), 2).c_str());
    } else if (cmd == "doctor") {
        DoctorResult d = doctor();
        for (const DocCheck& c : d.checks)
            std::printf("[%s] %s: %s\n", c.ok ? "ok " : "FAIL", c.name.c_str(), c.detail.c_str());
        std::printf("%s\n", d.ok ? "\nall good" : "\nsee the FAIL lines above");
        return d.ok ? 0 : 1;
    } else if (cmd == "scan") {
        for (const NetEntry& n : scan()) {
            // print(f" {mark} {signal:3d}%  {security:12s} {ssid}") — %-12s pads by
            // Unicode width; close enough for the CLI view (SSIDs here are ASCII).
            std::printf(" %s %3d%%  %-12s %s\n", n.in_use ? "*" : " ", n.signal, n.security.c_str(),
                        n.ssid.c_str());
        }
    } else if (cmd == "connect") {
        if (argc < 3) {
            std::printf("usage: dji-netctl connect SSID [PASSWORD]\n");
            return 2;
        }
        const std::string* psk = argc > 3 ? new std::string(argv[3]) : nullptr;
        Json res = connect(argv[2], psk);
        delete psk;
        std::printf("%s\n", json_dumps(res, 2).c_str());
    } else if (cmd == "disconnect") {
        std::printf("%s\n", json_dumps(disconnect(), 2).c_str());
    } else if (cmd == "hotspot") {
        bool on = argc < 3 || std::string(argv[2]) != "off";
        std::printf("%s\n", json_dumps(hotspot(on), 2).c_str());
    } else if (cmd == "serve") {
        return serve();
    } else {
        std::printf("%s", USAGE);
        return 2;
    }
    return 0;
}
