// Locate the Pi jump-host from the PC, ported from netfind.py.
//
// The flow mirrors the Python original: is the Pi reachable on the current LAN
// (saved host / raspberrypi.local / AP gateway), else a parallel sweep of every
// /24 we have an address on, else join a "PI_DJI_LINK-*" access point and talk
// to the Pi's netctl HTTP API on its own gateway.
#pragma once

#include <optional>
#include <string>
#include <vector>

namespace djilink::netfind {

inline constexpr const char* AP_PREFIX = "PI_DJI_LINK-";
inline constexpr const char* AP_DEFAULT_PSK = "raspberry";
inline constexpr const char* AP_GATEWAY = "10.42.0.1";
inline constexpr int BRIDGE_PORT = 9910;
inline constexpr int NETCTL_PORT = 9911;

bool port_open(const std::string& host, int port, double timeout_s = 0.4);

// The Pi's address if the bridge port answers on the current network, else nullopt.
std::optional<std::string> find_on_lan(const std::optional<std::string>& saved_host = std::nullopt);

// Every non-loopback IPv4 address this machine currently holds. A PC with WSL,
// Hyper-V, VirtualBox or a VPN has several — the Wi-Fi one is rarely the first.
std::vector<std::string> local_ipv4s();

// Probe every host on each of our own /24s for the bridge port, in parallel.
std::optional<std::string> sweep_lan(int port = BRIDGE_PORT);

// SSIDs in range whose name marks them as a Pi AP (netsh / nmcli / airport).
std::vector<std::string> scan_ap();

// Join a Pi AP and wait until its gateway answers on the bridge port.
bool join_ap(const std::string& ssid, const std::string& psk = AP_DEFAULT_PSK);

// GET http://host:9911<path> — the Pi netctl API. Body only, nullopt on failure.
std::optional<std::string> netctl_get(const std::string& host, const std::string& path,
                                      double timeout_s = 8.0);

// Whether the Pi reports an uplink of its own (/status -> "internet": true).
bool pi_has_internet(const std::string& host);

struct DiscoverResult {
    std::optional<std::string> host;
    std::string via; // "lan" | "sweep" | "ap" | "" (none)
    std::optional<std::string> joined_ap;
    bool needs_internet_prompt = false;
};

DiscoverResult discover(const std::optional<std::string>& saved_host = std::nullopt,
                        bool allow_ap_join = true);

} // namespace djilink::netfind
