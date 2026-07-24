// Locate the Pi jump-host from the PC, ported from netfind.py.
//
// Phase 1 implements the network-discovery core: is the Pi reachable on the
// current LAN (saved host / raspberrypi.local / AP gateway), else a parallel /24
// sweep for the bridge port. Wi-Fi AP-join (Windows netsh) and the Pi netctl HTTP
// API belong to the graphical discovery screen and are added in the GUI phase.
#pragma once

#include <optional>
#include <string>

namespace djilink::netfind {

inline constexpr const char* AP_PREFIX = "PI_DJI_LINK-";
inline constexpr const char* AP_GATEWAY = "10.42.0.1";
inline constexpr int BRIDGE_PORT = 9910;
inline constexpr int NETCTL_PORT = 9911;

bool port_open(const std::string& host, int port, double timeout_s = 0.4);

// The Pi's address if the bridge port answers on the current network, else nullopt.
std::optional<std::string> find_on_lan(const std::optional<std::string>& saved_host = std::nullopt);

// Probe every host on our own /24 for the bridge port, in parallel.
std::optional<std::string> sweep_lan(int port = BRIDGE_PORT);

struct DiscoverResult {
    std::optional<std::string> host;
    std::string via; // "lan" | "sweep" | "" (none)
    std::optional<std::string> joined_ap;
    bool needs_internet_prompt = false;
};

DiscoverResult discover(const std::optional<std::string>& saved_host = std::nullopt);

} // namespace djilink::netfind
