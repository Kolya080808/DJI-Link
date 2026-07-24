#include "core/netfind.hpp"

#include <atomic>
#include <thread>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
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

} // namespace

bool port_open(const std::string& host, int port, double timeout_s) {
    ensure_wsa();
    addrinfo hints{};
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    addrinfo* res = nullptr;
    const std::string ports = std::to_string(port);
    if (getaddrinfo(host.c_str(), ports.c_str(), &hints, &res) != 0 || !res)
        return false;
    sock_t fd = ::socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd == kBadSock) {
        freeaddrinfo(res);
        return false;
    }
    // non-blocking connect + select for the timeout
#ifdef _WIN32
    u_long nb = 1;
    ioctlsocket(fd, FIONBIO, &nb);
#else
    int fl = ::fcntl(fd, F_GETFL, 0);
    ::fcntl(fd, F_SETFL, fl | O_NONBLOCK);
#endif
    ::connect(fd, res->ai_addr, static_cast<int>(res->ai_addrlen));
    freeaddrinfo(res);

    fd_set wf;
    FD_ZERO(&wf);
    FD_SET(fd, &wf);
    timeval tv;
    tv.tv_sec = static_cast<long>(timeout_s);
    tv.tv_usec = static_cast<long>((timeout_s - tv.tv_sec) * 1e6);
    const int r = ::select(static_cast<int>(fd) + 1, nullptr, &wf, nullptr, &tv);
    bool ok = false;
    if (r > 0 && FD_ISSET(fd, &wf)) {
        int err = 0;
        socklen_t len = sizeof(err);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, reinterpret_cast<char*>(&err), &len) == 0 &&
            err == 0) {
            ok = true;
        }
    }
    close_sock(fd);
    return ok;
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
        if (port_open(*ip, BRIDGE_PORT))
            return ip;
    }
    return std::nullopt;
}

std::optional<std::string> sweep_lan(int port) {
    ensure_wsa();
    char name[256] = {0};
    if (gethostname(name, sizeof(name)) != 0)
        return std::nullopt;
    auto my_ip = resolve_ipv4(name);
    if (!my_ip || my_ip->rfind("127.", 0) == 0)
        return std::nullopt;
    const std::string base = my_ip->substr(0, my_ip->rfind('.'));

    std::atomic<int> found{-1};
    std::vector<std::thread> pool;
    std::atomic<int> next{1};
    const int workers = 64;
    for (int w = 0; w < workers; ++w) {
        pool.emplace_back([&]() {
            int i;
            while ((i = next.fetch_add(1)) <= 254) {
                if (found.load() >= 0)
                    return;
                const std::string host = base + "." + std::to_string(i);
                if (port_open(host, port, 0.3)) {
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

DiscoverResult discover(const std::optional<std::string>& saved_host) {
    if (auto host = find_on_lan(saved_host)) {
        return {host, "lan", std::nullopt, false};
    }
    if (auto host = sweep_lan()) {
        return {host, "sweep", std::nullopt, false};
    }
    // Wi-Fi AP-join (Windows netsh) + Pi netctl are added in the GUI phase.
    return {std::nullopt, "", std::nullopt, false};
}

} // namespace djilink::netfind
