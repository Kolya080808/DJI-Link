#include "core/netfind.hpp"

#include "core/applog.hpp"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <thread>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
// clang-format off
// Order is load-bearing: winsock2.h before windows.h (or windows.h pulls in the
// winsock 1 declarations and they clash), and iphlpapi.h after both. "IncludeBlocks:
// Regroup" would happily sort these back into a broken order.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <iphlpapi.h>
// clang-format on
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
#endif

namespace djilink::netfind {
namespace {

#ifdef _WIN32
using sock_t = SOCKET;
constexpr sock_t kBadSock = INVALID_SOCKET;
struct WsaInit {
    WsaInit() {
        WSADATA d;
        WSAStartup(MAKEWORD(2, 2), &d);
    }
};
void ensure_wsa() {
    static WsaInit init;
}
void close_sock(sock_t s) {
    ::closesocket(s);
}
#else
using sock_t = int;
constexpr sock_t kBadSock = -1;
void ensure_wsa() {}
void close_sock(sock_t s) {
    ::close(s);
}
#endif

std::optional<std::string> resolve_ipv4(const std::string& host) {
    // Winsock must be up before *any* name lookup: getaddrinfo on an
    // uninitialised stack fails with WSANOTINITIALISED, which used to make every
    // candidate in find_on_lan() silently unresolvable on Windows.
    ensure_wsa();
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    if (getaddrinfo(host.c_str(), nullptr, &hints, &res) != 0 || !res)
        return std::nullopt;
    char buf[INET_ADDRSTRLEN] = {0};
    auto* sa = reinterpret_cast<sockaddr_in*>(res->ai_addr);
    inet_ntop(AF_INET, &sa->sin_addr, buf, sizeof(buf));
    freeaddrinfo(res);
    return std::string(buf);
}

// Connected socket, or kBadSock. Non-blocking connect + select for the timeout.
sock_t connect_timeout(const std::string& host, int port, double timeout_s) {
    ensure_wsa();
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    const std::string ports = std::to_string(port);
    if (getaddrinfo(host.c_str(), ports.c_str(), &hints, &res) != 0 || !res)
        return kBadSock;
    sock_t fd = ::socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd == kBadSock) {
        freeaddrinfo(res);
        return kBadSock;
    }
#ifdef _WIN32
    u_long nb = 1;
    ioctlsocket(fd, FIONBIO, &nb);
#else
    int fl = ::fcntl(fd, F_GETFL, 0);
    ::fcntl(fd, F_SETFL, fl | O_NONBLOCK);
#endif
    ::connect(fd, res->ai_addr, static_cast<int>(res->ai_addrlen));
    freeaddrinfo(res);

    fd_set wf, ef;
    FD_ZERO(&wf);
    FD_SET(fd, &wf);
    FD_ZERO(&ef);
    FD_SET(fd, &ef); // Windows reports a refused connect in exceptfds, not writefds
    timeval tv;
    tv.tv_sec = static_cast<long>(timeout_s);
    tv.tv_usec = static_cast<long>((timeout_s - tv.tv_sec) * 1e6);
    const int r = ::select(static_cast<int>(fd) + 1, nullptr, &wf, &ef, &tv);
    bool ok = false;
    if (r > 0 && FD_ISSET(fd, &wf)) {
        int err = 0;
        socklen_t len = sizeof(err);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&err), &len) == 0 &&
            err == 0) {
            ok = true;
        }
    }
    if (!ok) {
        close_sock(fd);
        return kBadSock;
    }
    // Back to blocking so the caller can just send()/recv() with SO_*TIMEO.
#ifdef _WIN32
    u_long b = 0;
    ioctlsocket(fd, FIONBIO, &b);
#else
    ::fcntl(fd, F_SETFL, fl);
#endif
    return fd;
}

void set_io_timeout(sock_t fd, double seconds) {
#ifdef _WIN32
    DWORD ms = static_cast<DWORD>(seconds * 1000);
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&ms), sizeof(ms));
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&ms), sizeof(ms));
#else
    timeval tv;
    tv.tv_sec = static_cast<long>(seconds);
    tv.tv_usec = static_cast<long>((seconds - tv.tv_sec) * 1e6);
    ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
#endif
}

// Run a command and capture its stdout. The GUI is a WIN32_EXECUTABLE, so the
// child must not flash a console window (CREATE_NO_WINDOW).
std::string run_capture(const std::string& cmd) {
#ifdef _WIN32
    SECURITY_ATTRIBUTES sa{};
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE rd = nullptr, wr = nullptr;
    if (!CreatePipe(&rd, &wr, &sa, 0))
        return {};
    SetHandleInformation(rd, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOA si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = wr;
    si.hStdError = wr;
    si.hStdInput = nullptr;
    PROCESS_INFORMATION pi{};
    std::string full = "cmd.exe /c " + cmd;
    std::vector<char> mut(full.begin(), full.end());
    mut.push_back('\0');
    if (!CreateProcessA(nullptr, mut.data(), nullptr, nullptr, TRUE, CREATE_NO_WINDOW, nullptr,
                        nullptr, &si, &pi)) {
        CloseHandle(rd);
        CloseHandle(wr);
        return {};
    }
    CloseHandle(wr); // our copy; the read ends when the child's copy closes
    std::string out;
    char buf[4096];
    DWORD got = 0;
    while (ReadFile(rd, buf, sizeof(buf), &got, nullptr) && got > 0)
        out.append(buf, got);
    CloseHandle(rd);
    WaitForSingleObject(pi.hProcess, 15000);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return out;
#else
    std::string out;
    FILE* p = ::popen((cmd + " 2>/dev/null").c_str(), "r");
    if (!p)
        return out;
    char buf[4096];
    while (std::fgets(buf, sizeof(buf), p))
        out += buf;
    ::pclose(p);
    return out;
#endif
}

int run_quiet(const std::string& cmd) {
#ifdef _WIN32
    run_capture(cmd);
    return 0;
#else
    return std::system((cmd + " >/dev/null 2>&1").c_str());
#endif
}

std::string shell_quote(const std::string& s) {
#ifdef _WIN32
    return "\"" + s + "\"";
#else
    std::string out = "'";
    for (char c : s) {
        if (c == '\'')
            out += "'\\''";
        else
            out += c;
    }
    out += "'";
    return out;
#endif
}

#ifdef _WIN32
std::string xml_escape(const std::string& s) {
    std::string out;
    for (char c : s) {
        switch (c) {
            case '&':
                out += "&amp;";
                break;
            case '<':
                out += "&lt;";
                break;
            case '>':
                out += "&gt;";
                break;
            case '"':
                out += "&quot;";
                break;
            default:
                out += c;
        }
    }
    return out;
}
#endif

std::string subnet_of(const std::string& ip) {
    const auto dot = ip.rfind('.');
    return dot == std::string::npos ? std::string{} : ip.substr(0, dot);
}

// Scan one /24 for an open port. Returns the host that answered, if any.
std::optional<std::string> sweep_subnet(const std::string& base, int port) {
    std::atomic<int> found{-1};
    std::atomic<int> next{1};
    std::vector<std::thread> pool;
    const int workers = 64;
    pool.reserve(workers);
    for (int w = 0; w < workers; ++w) {
        pool.emplace_back([&]() {
            int i;
            while ((i = next.fetch_add(1)) <= 254) {
                if (found.load() >= 0)
                    return;
                if (port_open(base + "." + std::to_string(i), port, 0.3)) {
                    int expected = -1;
                    found.compare_exchange_strong(expected, i);
                    return;
                }
            }
        });
    }
    for (auto& t : pool)
        t.join();
    const int idx = found.load();
    if (idx < 0)
        return std::nullopt;
    return base + "." + std::to_string(idx);
}

// Pull every "PI_DJI_LINK-xxxx" token out of a Wi-Fi scan dump. Parsing the
// "SSID N : name" label would break on localised Windows/nmcli output, so the
// prefix itself is the anchor.
std::vector<std::string> ssids_in(const std::string& blob) {
    std::vector<std::string> found;
    const std::string prefix = AP_PREFIX;
    std::size_t pos = 0;
    while ((pos = blob.find(prefix, pos)) != std::string::npos) {
        std::size_t end = pos;
        while (end < blob.size()) {
            const unsigned char c = static_cast<unsigned char>(blob[end]);
            if (std::isalnum(c) || c == '_' || c == '-')
                ++end;
            else
                break;
        }
        std::string ssid = blob.substr(pos, end - pos);
        if (std::find(found.begin(), found.end(), ssid) == found.end())
            found.push_back(ssid);
        pos = end;
    }
    return found;
}

} // namespace

bool port_open(const std::string& host, int port, double timeout_s) {
    sock_t fd = connect_timeout(host, port, timeout_s);
    if (fd == kBadSock)
        return false;
    close_sock(fd);
    return true;
}

std::optional<std::string> find_on_lan(const std::optional<std::string>& saved_host) {
    std::vector<std::string> candidates;
    if (saved_host && !saved_host->empty())
        candidates.push_back(*saved_host);
    candidates.push_back("raspberrypi.local");
    candidates.push_back(AP_GATEWAY);
    for (const auto& host : candidates) {
        auto ip = resolve_ipv4(host);
        if (!ip)
            continue;
        if (port_open(*ip, NETCTL_PORT)) {
            applog::info("[netfind] Pi answers at " + *ip + " (" + host + ")");
            return ip;
        }
    }
    return std::nullopt;
}

std::vector<std::string> local_ipv4s() {
    ensure_wsa();
    std::vector<std::string> ips;
    auto keep = [&](const std::string& ip) {
        if (ip.empty() || ip.rfind("127.", 0) == 0 || ip.rfind("169.254.", 0) == 0)
            return;
        if (std::find(ips.begin(), ips.end(), ip) == ips.end())
            ips.push_back(ip);
    };

#ifdef _WIN32
    ULONG size = 16384;
    std::vector<char> buf(size);
    ULONG rc = GetAdaptersAddresses(
        AF_INET, GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER,
        nullptr, reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buf.data()), &size);
    if (rc == ERROR_BUFFER_OVERFLOW) {
        buf.assign(size, 0);
        rc = GetAdaptersAddresses(
            AF_INET, GAA_FLAG_SKIP_ANYCAST | GAA_FLAG_SKIP_MULTICAST | GAA_FLAG_SKIP_DNS_SERVER,
            nullptr, reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buf.data()), &size);
    }
    if (rc == NO_ERROR) {
        for (auto* a = reinterpret_cast<IP_ADAPTER_ADDRESSES*>(buf.data()); a; a = a->Next) {
            if (a->OperStatus != IfOperStatusUp || a->IfType == IF_TYPE_SOFTWARE_LOOPBACK)
                continue;
            for (auto* u = a->FirstUnicastAddress; u; u = u->Next) {
                if (!u->Address.lpSockaddr || u->Address.lpSockaddr->sa_family != AF_INET)
                    continue;
                char s[INET_ADDRSTRLEN] = {0};
                auto* sa = reinterpret_cast<sockaddr_in*>(u->Address.lpSockaddr);
                inet_ntop(AF_INET, &sa->sin_addr, s, sizeof(s));
                keep(s);
            }
        }
    }
#else
    ifaddrs* ifa = nullptr;
    if (getifaddrs(&ifa) == 0) {
        for (auto* p = ifa; p; p = p->ifa_next) {
            if (!p->ifa_addr || p->ifa_addr->sa_family != AF_INET)
                continue;
            if (!(p->ifa_flags & IFF_UP) || (p->ifa_flags & IFF_LOOPBACK))
                continue;
            char s[INET_ADDRSTRLEN] = {0};
            auto* sa = reinterpret_cast<sockaddr_in*>(p->ifa_addr);
            inet_ntop(AF_INET, &sa->sin_addr, s, sizeof(s));
            keep(s);
        }
        freeifaddrs(ifa);
    }
#endif

    if (ips.empty()) {
        // Last resort: the old hostname lookup (one adapter, and not always the
        // right one — that is exactly why the enumeration above exists).
        char name[256] = {0};
        if (gethostname(name, sizeof(name)) == 0) {
            if (auto ip = resolve_ipv4(name))
                keep(*ip);
        }
    }
    return ips;
}

std::optional<std::string> sweep_lan(int port) {
    std::vector<std::string> bases;
    for (const auto& ip : local_ipv4s()) {
        auto base = subnet_of(ip);
        if (!base.empty() && std::find(bases.begin(), bases.end(), base) == bases.end())
            bases.push_back(base);
    }
    // The Pi's own AP subnet is the most likely hit — try it before the VPN /
    // WSL / Hyper-V networks a desktop tends to accumulate.
    const std::string ap_base = subnet_of(AP_GATEWAY);
    std::stable_sort(bases.begin(), bases.end(), [&](const std::string& a, const std::string& b) {
        return a == ap_base && b != ap_base;
    });
    for (const auto& base : bases) {
        applog::info("[netfind] sweeping " + base + ".0/24 for port " + std::to_string(port));
        if (auto host = sweep_subnet(base, port)) {
            applog::info("[netfind] Pi answers at " + *host);
            return host;
        }
    }
    return std::nullopt;
}

std::vector<std::string> scan_ap() {
#if defined(_WIN32)
    // A fresh scan needs the interface to have rescanned recently; netsh returns
    // the cached list, which is what the Windows UI shows too.
    return ssids_in(run_capture("netsh wlan show networks"));
#elif defined(__APPLE__)
    // airport -s was removed in macOS 14.4; system_profiler still lists the SSIDs.
    const char* airport = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/"
                          "Current/Resources/airport";
    auto out = run_capture(std::string(airport) + " -s");
    if (out.empty())
        out = run_capture("system_profiler SPAirPortDataType");
    return ssids_in(out);
#else
    auto out = run_capture("nmcli -t -f SSID dev wifi list --rescan auto");
    if (out.empty())
        out = run_capture("nmcli -t -f SSID dev wifi list");
    return ssids_in(out);
#endif
}

bool join_ap(const std::string& ssid, const std::string& psk) {
    applog::info("[netfind] joining Pi AP '" + ssid + "'");
#if defined(_WIN32)
    // netsh can only connect to a profile that already exists, so create one
    // first; it is left in place so the next join is instant.
    namespace fs = std::filesystem;
    const std::string path = (fs::temp_directory_path() / ("dji-link-" + ssid + ".xml")).string();
    {
        std::ofstream f(path, std::ios::binary);
        if (!f)
            return false;
        f << "<?xml version=\"1.0\"?>\n"
          << "<WLANProfile xmlns=\"http://www.microsoft.com/networking/WLAN/profile/v1\">\n"
          << "  <name>" << xml_escape(ssid) << "</name>\n"
          << "  <SSIDConfig><SSID><name>" << xml_escape(ssid) << "</name></SSID></SSIDConfig>\n"
          << "  <connectionType>ESS</connectionType>\n"
          << "  <connectionMode>manual</connectionMode>\n"
          << "  <MSM><security>\n"
          << "    <authEncryption><authentication>WPA2PSK</authentication>\n"
          << "      <encryption>AES</encryption><useOneX>false</useOneX></authEncryption>\n"
          << "    <sharedKey><keyType>passPhrase</keyType>\n"
          << "      <protected>false</protected><keyMaterial>" << xml_escape(psk)
          << "</keyMaterial></sharedKey>\n"
          << "  </security></MSM>\n"
          << "</WLANProfile>\n";
    }
    // Capture netsh output — a failed profile-add or connect otherwise vanishes and the
    // join just looks like "nothing happened". A one-line trace makes it diagnosable.
    auto trim = [](std::string s) {
        while (!s.empty() && (s.back() == '\n' || s.back() == '\r' || s.back() == ' '))
            s.pop_back();
        return s;
    };
    std::string add_out = run_capture("netsh wlan add profile filename=" + shell_quote(path));
    std::error_code ec;
    fs::remove(path, ec);
    applog::info("[netfind] netsh add profile: " + trim(add_out));
    // After a shared-radio channel retune Windows can keep reporting the old, unusable
    // association. Force a state transition and keep reconnecting while the Pi's delayed
    // hostapd restart completes.
    applog::info("[netfind] netsh disconnect: " + trim(run_capture("netsh wlan disconnect")));
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    const std::string connect_cmd =
        "netsh wlan connect name=" + shell_quote(ssid) + " ssid=" + shell_quote(ssid);
    for (int attempt = 1; attempt <= 22; ++attempt) {
        const std::string conn_out = run_capture(connect_cmd);
        if (attempt == 1 || attempt % 5 == 0)
            applog::info("[netfind] netsh connect attempt " + std::to_string(attempt) + ": " +
                         trim(conn_out));
        for (int probe = 0; probe < 4; ++probe) {
            std::this_thread::sleep_for(std::chrono::milliseconds(500));
            if (port_open(AP_GATEWAY, NETCTL_PORT))
                return true;
        }
    }
    applog::info("[netfind] Windows could not reassociate with '" + ssid + "'");
    return false;
#elif defined(__APPLE__)
    // The Pi restarts hostapd ~2.5 s after it answers; an immediate connect can bind to
    // the dying AP and never re-associate. Drop the current association first, wait out
    // the Pi's scheduled restart, then connect.
    run_quiet("networksetup -setairportpower en0 off");
    std::this_thread::sleep_for(std::chrono::milliseconds(4000));
    run_quiet("networksetup -setairportpower en0 on");
    std::this_thread::sleep_for(std::chrono::milliseconds(800));
    run_quiet("networksetup -setairportnetwork en0 " + shell_quote(ssid) + " " + shell_quote(psk));
#else
    // Same reasoning as the macOS branch: without a forced disconnect+wait nmcli may
    // keep the stale association and the fresh AP's beacon gets ignored.
    run_quiet("nmcli dev disconnect wlan0");
    std::this_thread::sleep_for(std::chrono::milliseconds(4000));
    run_quiet("nmcli dev wifi connect " + shell_quote(ssid) + " password " + shell_quote(psk));
#endif
    // Wait for the interface to actually get the AP's gateway. Probe the netctl control
    // port (always up), not the bridge (needs the RC, which is plugged in only later).
    for (int i = 0; i < 20; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        if (port_open(AP_GATEWAY, NETCTL_PORT))
            return true;
    }
    applog::info("[netfind] joined '" + ssid + "' but " + AP_GATEWAY + ":" +
                 std::to_string(NETCTL_PORT) + " stayed silent");
    return false;
}

namespace {

// Minimal HTTP/1.0 request against the netctl API. One request per connection
// (Connection: close), which is all netctl's BaseHTTPRequestHandler serves anyway.
std::optional<std::string> netctl_request(const std::string& host, const std::string& method,
                                          const std::string& path, const std::string& body,
                                          double timeout_s) {
    sock_t fd = connect_timeout(host, NETCTL_PORT, timeout_s);
    if (fd == kBadSock)
        return std::nullopt;
    set_io_timeout(fd, timeout_s);
    std::string req = method + " " + path + " HTTP/1.0\r\nHost: " + host + "\r\n" +
                      "Connection: close\r\nAccept: application/json\r\n";
    if (!body.empty()) {
        req += "Content-Type: application/json\r\nContent-Length: " + std::to_string(body.size()) +
               "\r\n";
    }
    req += "\r\n" + body;
    if (::send(fd, req.data(), static_cast<int>(req.size()), 0) < 0) {
        close_sock(fd);
        return std::nullopt;
    }
    std::string resp;
    char buf[2048];
    while (true) {
        const int n = static_cast<int>(::recv(fd, buf, static_cast<int>(sizeof(buf)), 0));
        if (n <= 0)
            break;
        resp.append(buf, static_cast<std::size_t>(n));
        if (resp.size() > 512 * 1024)
            break;
    }
    close_sock(fd);
    const auto sep = resp.find("\r\n\r\n");
    if (sep == std::string::npos)
        return std::nullopt;
    return resp.substr(sep + 4);
}

// ---------------------------------------------------------------- tiny JSON reads
// netctl's replies are flat, machine-generated objects (json.dumps of a dict of
// scalars and one list of flat dicts), so scanning for the key is enough — the same
// approach updater.cpp already uses for the GitHub API. No dependency, no parser.

// Value slice after "key": , or npos. Search starts at `from` so a caller can walk
// repeated keys inside an array.
std::size_t value_pos(const std::string& js, const std::string& key, std::size_t from = 0) {
    const std::string k = "\"" + key + "\"";
    const auto at = js.find(k, from);
    if (at == std::string::npos)
        return std::string::npos;
    const auto colon = js.find(':', at + k.size());
    if (colon == std::string::npos)
        return std::string::npos;
    return js.find_first_not_of(" \t\r\n", colon + 1);
}

bool json_bool(const std::string& js, const std::string& key, std::size_t from = 0) {
    const auto v = value_pos(js, key, from);
    return v != std::string::npos && js.compare(v, 4, "true") == 0;
}

int json_int(const std::string& js, const std::string& key, std::size_t from = 0) {
    const auto v = value_pos(js, key, from);
    if (v == std::string::npos)
        return 0;
    // A quoted number ("signal": "72") must parse too — nmcli fields reach netctl
    // as strings in some paths and json.dumps keeps them quoted.
    const std::size_t s = (js[v] == '"') ? v + 1 : v;
    try {
        return std::stoi(js.substr(s, 12));
    } catch (const std::exception&) {
        return 0;
    }
}

// Unescaped string value. SSIDs legitimately contain quotes, backslashes and UTF-8
// (json.dumps escapes non-ASCII as \uXXXX), so the escapes must be undone or the
// SSID handed back to nmcli would not match the network.
std::string json_str(const std::string& js, const std::string& key, std::size_t from = 0) {
    auto v = value_pos(js, key, from);
    if (v == std::string::npos || js[v] != '"')
        return {};
    std::string out;
    for (std::size_t i = v + 1; i < js.size(); ++i) {
        const char c = js[i];
        if (c == '"')
            break;
        if (c != '\\') {
            out += c;
            continue;
        }
        if (++i >= js.size())
            break;
        switch (js[i]) {
            case 'n':
                out += '\n';
                break;
            case 't':
                out += '\t';
                break;
            case 'r':
                out += '\r';
                break;
            case 'b':
                out += '\b';
                break;
            case 'f':
                out += '\f';
                break;
            case 'u': {
                if (i + 4 >= js.size())
                    return out;
                unsigned cp = 0;
                try {
                    cp = static_cast<unsigned>(std::stoul(js.substr(i + 1, 4), nullptr, 16));
                } catch (const std::exception&) {
                    return out;
                }
                i += 4;
                // UTF-8 encode; surrogate halves are passed through as U+FFFD since a
                // lone half cannot be encoded and an SSID that needs one is unusable.
                if (cp >= 0xD800 && cp <= 0xDFFF)
                    cp = 0xFFFD;
                if (cp < 0x80) {
                    out += static_cast<char>(cp);
                } else if (cp < 0x800) {
                    out += static_cast<char>(0xC0 | (cp >> 6));
                    out += static_cast<char>(0x80 | (cp & 0x3F));
                } else {
                    out += static_cast<char>(0xE0 | (cp >> 12));
                    out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                    out += static_cast<char>(0x80 | (cp & 0x3F));
                }
                break;
            }
            default:
                out += js[i];
                break;
        }
    }
    return out;
}

// JSON string literal for one value (keys here are all ASCII literals we control).
std::string json_quote(const std::string& s) {
    std::string out = "\"";
    for (unsigned char c : s) {
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
            default:
                if (c < 0x20) {
                    char buf[7];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    return out + "\"";
}

} // namespace

std::optional<std::string> netctl_get(const std::string& host, const std::string& path,
                                      double timeout_s) {
    return netctl_request(host, "GET", path, {}, timeout_s);
}

std::optional<std::string> netctl_post(const std::string& host, const std::string& path,
                                       const std::string& json_body, double timeout_s) {
    return netctl_request(host, "POST", path, json_body, timeout_s);
}

bool pi_has_internet(const std::string& host) {
    auto body = netctl_get(host, "/status");
    return body && json_bool(*body, "internet");
}

std::optional<PiNetStatus> parse_status(const std::string& body) {
    if (body.find("\"ap\"") == std::string::npos)
        return std::nullopt;
    PiNetStatus s;
    s.internet = json_bool(body, "internet");
    s.ap_ssid = json_str(body, "ap_ssid");
    s.ap_psk = json_str(body, "ap_psk");
    // "ap" and "uplink" are nested objects that both carry a "state"/"connection" key,
    // so each is read from its own offset rather than from the top of the document.
    const auto ap_at = body.find("\"ap\"");
    const auto up_at = body.find("\"uplink\"");
    if (ap_at != std::string::npos) {
        // hostapd mode reports systemctl's "active"; the NM fallback reports "activated".
        const std::string st = json_str(body, "state", ap_at);
        s.ap_active = st == "active" || st == "activated" || st == "connected";
    }
    if (up_at != std::string::npos) {
        s.uplink_state = json_str(body, "state", up_at);
        s.uplink_name = json_str(body, "connection", up_at);
        if (s.uplink_name == "--")
            s.uplink_name.clear();
    }
    return s;
}

std::vector<WifiNet> parse_networks(const std::string& body) {
    std::vector<WifiNet> nets;
    // Walk the "networks" array by its per-entry "ssid" keys: every entry has exactly
    // one, and the remaining fields of that entry follow it before the next one.
    std::size_t pos = body.find("\"networks\"");
    if (pos == std::string::npos)
        return nets;
    while (true) {
        const std::size_t ssid_at = body.find("\"ssid\"", pos);
        if (ssid_at == std::string::npos)
            break;
        WifiNet n;
        n.ssid = json_str(body, "ssid", ssid_at);
        n.signal = json_int(body, "signal", ssid_at);
        n.security = json_str(body, "security", ssid_at);
        n.in_use = json_bool(body, "in_use", ssid_at);
        if (!n.ssid.empty())
            nets.push_back(std::move(n));
        pos = ssid_at + 6;
    }
    // netctl already sorts by signal; re-sort so the UI does not depend on that.
    std::stable_sort(nets.begin(), nets.end(),
                     [](const WifiNet& a, const WifiNet& b) { return a.signal > b.signal; });
    return nets;
}

PiActionResult parse_action(const std::string& body) {
    PiActionResult r;
    r.ok = json_bool(body, "ok");
    r.output = json_str(body, "output");
    if (r.output.empty())
        r.output = json_str(body, "error");
    if (r.output.empty())
        r.output = json_str(body, "note");
    return r;
}

std::optional<PiNetStatus> pi_status(const std::string& host) {
    auto body = netctl_get(host, "/status");
    if (!body)
        return std::nullopt;
    return parse_status(*body);
}

std::vector<WifiNet> pi_scan_wifi(const std::string& host) {
    // The Pi rescans wlan0 and sleeps 2 s before replying, so allow well over that.
    auto body = netctl_get(host, "/scan", 25.0);
    if (!body)
        return {};
    return parse_networks(*body);
}

namespace {

PiActionResult action_from(const std::optional<std::string>& body, const char* what) {
    if (!body) {
        PiActionResult r;
        r.output = std::string(what) + ": the Pi did not answer";
        return r;
    }
    return parse_action(*body);
}

} // namespace

PiActionResult pi_connect_wifi(const std::string& host, const std::string& ssid,
                               const std::string& psk) {
    applog::info("[netctl] asking the Pi to join '" + ssid + "'");
#ifdef _WIN32
    const bool via_ap = host == AP_GATEWAY;
    std::string pi_ap_ssid;
    if (via_ap) {
        if (auto before = pi_status(host))
            pi_ap_ssid = before->ap_ssid;
        if (pi_ap_ssid.rfind(AP_PREFIX, 0) != 0) {
            const auto visible = scan_ap();
            if (visible.size() == 1)
                pi_ap_ssid = visible.front();
        }
    }
#endif
    std::string body = "{\"ssid\":" + json_quote(ssid);
    if (!psk.empty())
        body += ",\"psk\":" + json_quote(psk);
    body += "}";
    // nmcli's association plus the AP restart can take a while; the Pi answers only
    // once both are done, so this request is deliberately long-lived.
    auto resp = netctl_post(host, "/connect", body, 60.0);
    auto r = action_from(resp, "connect");

    // The Pi responds before its delayed AP refresh. If the response was interrupted,
    // it may still have joined successfully; reconnect and let /status decide. A clear
    // negative response means no AP cycle was scheduled, so preserve the current link.
#ifdef _WIN32
    if (via_ap && (!resp || r.ok)) {
        if (pi_ap_ssid.rfind(AP_PREFIX, 0) != 0) {
            r.ok = false;
            r.output = "Pi uplink may have connected, but the PI_DJI_LINK-* AP to rejoin "
                       "could not be identified";
        } else {
            // netctl's /connect schedules the AP restart with a ~0.7 s pre-flush delay
            // (_restart_ap_async) and dji-ap's hostapd+dnsmasq bring-up takes a few
            // seconds more. With only ~1 s here the explicit disconnect ran into a still
            // breathing AP, Windows stayed glued to its stale Association, the first
            // connect retries felt no break and kept a dead session, and we gave up
            // before the fresh AP even existed. Hold for the AP to go down, force a
            // disconnect Windows cannot miss, then let join_ap re-associate.
            applog::info("[netctl] waiting for the Pi's delayed AP refresh");
            std::this_thread::sleep_for(std::chrono::milliseconds(4500));
            applog::info("[netctl] netsh disconnect before rejoining the refreshed AP: " +
                         run_capture("netsh wlan disconnect"));
            std::this_thread::sleep_for(std::chrono::milliseconds(1500));
            if (!join_ap(pi_ap_ssid, AP_DEFAULT_PSK)) {
                r.ok = false;
                r.output = "Pi uplink may have connected, but Windows could not rejoin '" +
                           pi_ap_ssid + "'";
            } else if (auto after = pi_status(host);
                       after && (after->uplink_name == ssid ||
                                 after->uplink_name.find(ssid) != std::string::npos)) {
                r.ok = true;
                if (!resp)
                    r.output = "connected to '" + ssid + "'; Pi AP reconnected";
            } else {
                r.ok = false;
                r.output = "Pi AP reconnected, but uplink '" + ssid + "' is not active";
            }
        }
    }
#endif
    applog::info(std::string("[netctl] connect ") + (r.ok ? "ok" : "failed") + ": " +
                 r.output.substr(0, 200));
    return r;
}

PiActionResult pi_disconnect_wifi(const std::string& host) {
    auto resp = netctl_post(host, "/disconnect", "", 30.0);
    auto r = action_from(resp, "disconnect");
    applog::info(std::string("[netctl] disconnect ") + (r.ok ? "ok" : "failed"));
    return r;
}

bool wait_for_pi(const std::string& host, double timeout_s) {
    // join_ap() explicitly restores Windows' association. This remains useful for
    // non-Windows clients and callers that want a final identity check.
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(static_cast<int>(timeout_s * 1000));
    while (std::chrono::steady_clock::now() < deadline) {
        if (port_open(host, NETCTL_PORT, 1.0))
            return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(700));
    }
    applog::info("[netctl] " + host + " stayed unreachable after the AP retune");
    return false;
}

DiscoverResult discover(const std::optional<std::string>& saved_host, bool allow_ap_join) {
    if (auto host = find_on_lan(saved_host)) {
        return {host, "lan", std::nullopt, false};
    }
    if (auto host = sweep_lan()) {
        return {host, "sweep", std::nullopt, false};
    }
    if (allow_ap_join) {
        const auto aps = scan_ap();
        if (aps.empty())
            applog::info("[netfind] no '" + std::string(AP_PREFIX) + "*' access point in range");
        for (const auto& ssid : aps) {
            if (join_ap(ssid)) {
                // The Pi may already have an uplink (it was left connected to a
                // Wi-Fi): if it reports internet, everything works — no prompt.
                const bool has_net = pi_has_internet(AP_GATEWAY);
                return {std::string(AP_GATEWAY), "ap", ssid, !has_net};
            }
        }
    }
    return {std::nullopt, "", std::nullopt, false};
}

} // namespace djilink::netfind
