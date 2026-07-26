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
        if (port_open(*ip, BRIDGE_PORT)) {
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
    run_quiet("netsh wlan add profile filename=" + shell_quote(path));
    std::error_code ec;
    fs::remove(path, ec);
    run_quiet("netsh wlan connect name=" + shell_quote(ssid) + " ssid=" + shell_quote(ssid));
#elif defined(__APPLE__)
    run_quiet("networksetup -setairportnetwork en0 " + shell_quote(ssid) + " " + shell_quote(psk));
#else
    run_quiet("nmcli dev wifi connect " + shell_quote(ssid) + " password " + shell_quote(psk));
#endif
    // Wait for the interface to actually get the AP's gateway.
    for (int i = 0; i < 20; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        if (port_open(AP_GATEWAY, BRIDGE_PORT))
            return true;
    }
    applog::info("[netfind] joined '" + ssid + "' but " + AP_GATEWAY + ":" +
                 std::to_string(BRIDGE_PORT) + " stayed silent");
    return false;
}

std::optional<std::string> netctl_get(const std::string& host, const std::string& path,
                                      double timeout_s) {
    sock_t fd = connect_timeout(host, NETCTL_PORT, timeout_s);
    if (fd == kBadSock)
        return std::nullopt;
    set_io_timeout(fd, timeout_s);
    const std::string req = "GET " + path + " HTTP/1.0\r\nHost: " + host + "\r\n" +
                            "Connection: close\r\nAccept: application/json\r\n\r\n";
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

bool pi_has_internet(const std::string& host) {
    auto body = netctl_get(host, "/status");
    if (!body)
        return false;
    const auto key = body->find("\"internet\"");
    if (key == std::string::npos)
        return false;
    const auto colon = body->find(':', key);
    if (colon == std::string::npos)
        return false;
    const auto val = body->find_first_not_of(" \t", colon + 1);
    return val != std::string::npos && body->compare(val, 4, "true") == 0;
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
