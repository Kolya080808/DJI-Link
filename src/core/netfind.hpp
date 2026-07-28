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

// The Pi's address if the netctl control port answers on the current network, else
// nullopt. Liveness is probed on NETCTL_PORT, not BRIDGE_PORT: netctl (the Wi-Fi/AP
// API) is always up, while the bridge only opens :9910 once an RC/UDC is plugged in —
// which happens AFTER discovery — so keying discovery off the bridge made a Pi with no
// controller attached look unreachable ("gateway answers on no port").
std::optional<std::string> find_on_lan(const std::optional<std::string>& saved_host = std::nullopt);

// Every non-loopback IPv4 address this machine currently holds. A PC with WSL,
// Hyper-V, VirtualBox or a VPN has several — the Wi-Fi one is rarely the first.
std::vector<std::string> local_ipv4s();

// Probe every host on each of our own /24s for the netctl control port, in parallel.
std::optional<std::string> sweep_lan(int port = NETCTL_PORT);

// SSIDs in range whose name marks them as a Pi AP (netsh / nmcli / airport).
std::vector<std::string> scan_ap();

// Join a Pi AP and wait until its gateway answers on the netctl control port.
bool join_ap(const std::string& ssid, const std::string& psk = AP_DEFAULT_PSK);

// GET http://host:9911<path> — the Pi netctl API. Body only, nullopt on failure.
std::optional<std::string> netctl_get(const std::string& host, const std::string& path,
                                      double timeout_s = 8.0);

// POST http://host:9911<path> with a JSON body. Same return contract as netctl_get.
// The timeout defaults high because /connect blocks on the Pi while nmcli associates.
std::optional<std::string> netctl_post(const std::string& host, const std::string& path,
                                       const std::string& json_body, double timeout_s = 45.0);

// Whether the Pi reports an uplink of its own (/status -> "internet": true).
bool pi_has_internet(const std::string& host);

// ------------------------------------------------------- Pi Wi-Fi (netctl API)
// One network the Pi's wlan0 can see (netctl /scan -> "networks").
struct WifiNet {
    std::string ssid;
    int signal = 0;       // percent, 0-100
    std::string security; // "WPA2", "open", ...
    bool in_use = false;  // the Pi is currently joined to this one
    bool open() const {
        return security.empty() || security == "open" || security == "--";
    }
};

// What the Pi reports about its own networking (netctl /status).
struct PiNetStatus {
    bool internet = false;  // the Pi can reach the outside world
    bool ap_active = false; // its access point is up
    std::string ap_ssid;
    std::string ap_psk;
    std::string uplink_state; // nmcli device state of wlan0 ("connected", ...)
    std::string uplink_name;  // the network wlan0 is joined to, if any
};

// Result of a netctl action (/connect, /disconnect, /hotspot).
struct PiActionResult {
    bool ok = false;
    std::string output; // nmcli/systemctl output or the error, for the UI
};

// Response parsing, split out from the HTTP calls so it is testable without a Pi
// (tests/netctl_parse_test.cpp). netctl's replies are flat json.dumps output.
std::vector<WifiNet> parse_networks(const std::string& json);
std::optional<PiNetStatus> parse_status(const std::string& json);
PiActionResult parse_action(const std::string& json);

// Networks the Pi can see, strongest first. Empty on any failure (the Pi is the
// only thing that can scan here — the PC's own radio is busy holding the AP link).
std::vector<WifiNet> pi_scan_wifi(const std::string& host);

// Join / leave an uplink on the Pi. Joining retunes the AP to the uplink's channel
// (one radio), so the PC's own association to the Pi AP may drop and re-form.
PiActionResult pi_connect_wifi(const std::string& host, const std::string& ssid,
                               const std::string& psk);
PiActionResult pi_disconnect_wifi(const std::string& host);

// Full /status, or nullopt when the Pi does not answer.
std::optional<PiNetStatus> pi_status(const std::string& host);

// Block until the Pi answers /status again, up to timeout_s. Called after a connect:
// the AP restart drops the PC for a few seconds and the link comes back on its own.
bool wait_for_pi(const std::string& host, double timeout_s = 40.0);

struct DiscoverResult {
    std::optional<std::string> host;
    std::string via; // "lan" | "sweep" | "ap" | "" (none)
    std::optional<std::string> joined_ap;
    bool needs_internet_prompt = false;
};

DiscoverResult discover(const std::optional<std::string>& saved_host = std::nullopt,
                        bool allow_ap_join = true);

} // namespace djilink::netfind
