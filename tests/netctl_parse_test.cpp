// Parsing of the Pi netctl API replies (netfind::parse_*), checked against real
// json.dumps output from dji_link_beta/pi/netctl.py. No Pi and no sockets needed, so
// this runs on every CI runner. Plain asserts + a nonzero exit: no test framework is
// pulled in for what is a pure-function check.

#include "core/netfind.hpp"

#include <cstdio>
#include <cstdlib>
#include <string>

using namespace djilink::netfind;

namespace {

int failures = 0;

void check(bool ok, const char* what) {
    if (!ok) {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

// hostapd-mode /status, as netctl.py emits it on a Pi set up by setup_pi.sh.
const char* kStatus = R"({"ap": {"iface": "uap0", "state": "active",
"connection": "dji-ap", "ssid": "PI_DJI_LINK-9f3c", "address": "10.42.0.1",
"mode": "hostapd"}, "uplink": {"iface": "wlan0", "state": "connected",
"connection": "HomeNet"}, "internet": true, "addresses": ["10.42.0.1",
"192.168.1.57"], "ap_ssid": "PI_DJI_LINK-9f3c", "ap_psk": "raspberry"})";

// Same, with no uplink: nmcli reports the connection as "--", which must read as none.
const char* kStatusNoUplink = R"({"ap": {"iface": "uap0", "state": "active",
"connection": "dji-ap", "ssid": "PI_DJI_LINK-9f3c", "address": "10.42.0.1"},
"uplink": {"iface": "wlan0", "state": "disconnected", "connection": "--"},
"internet": false, "addresses": ["10.42.0.1"], "ap_ssid": "PI_DJI_LINK-9f3c",
"ap_psk": "raspberry"})";

void test_status() {
    auto s = parse_status(kStatus);
    check(s.has_value(), "status parses");
    if (!s)
        return;
    check(s->internet, "internet true");
    check(s->ap_active, "ap active");
    check(s->ap_ssid == "PI_DJI_LINK-9f3c", "ap ssid");
    check(s->ap_psk == "raspberry", "ap psk");
    check(s->uplink_name == "HomeNet", "uplink name");
    check(s->uplink_state == "connected", "uplink state");

    auto n = parse_status(kStatusNoUplink);
    check(n.has_value(), "no-uplink status parses");
    if (n) {
        check(!n->internet, "internet false");
        check(n->uplink_name.empty(), "nmcli '--' means no uplink");
        check(n->ap_active, "ap still up without an uplink");
    }
    // A body that is not a status reply at all must not be mistaken for one.
    check(!parse_status(R"({"error": "not found"})").has_value(), "error body is not a status");
}

void test_networks() {
    // Ordering is deliberately wrong in the input to prove the sort, and the escapes
    // cover what json.dumps produces for a quote and for non-ASCII (\uXXXX).
    const std::string body = R"({"networks": [
{"ssid": "Weak", "signal": 21, "security": "WPA2", "in_use": false},
{"ssid": "Café \"Free\"", "signal": 88, "security": "open", "in_use": false},
{"ssid": "HomeNet", "signal": 74, "security": "WPA1 WPA2", "in_use": true}]})";
    auto nets = parse_networks(body);
    check(nets.size() == 3, "three networks");
    if (nets.size() != 3)
        return;
    check(nets[0].ssid == "Caf\xc3\xa9 \"Free\"", "unicode + quote unescaped");
    check(nets[0].signal == 88 && nets[0].open(), "strongest first, open detected");
    check(nets[1].ssid == "HomeNet" && nets[1].in_use, "in_use flag");
    check(!nets[1].open(), "WPA2 is not open");
    check(nets[2].signal == 21, "weakest last");
    check(parse_networks(R"({"networks": []})").empty(), "empty list");
    check(parse_networks("").empty(), "empty body");
}

void test_action() {
    auto ok = parse_action(R"({"ok": true, "output": "Device 'wlan0' successfully activated",
"note": "AP retunes to the uplink channel"})");
    check(ok.ok, "ok true");
    check(ok.output.rfind("Device", 0) == 0, "output preferred over note");

    auto bad = parse_action(R"({"ok": false, "output": "Secrets were required"})");
    check(!bad.ok && bad.output == "Secrets were required", "failure output");

    // /connect with a missing ssid answers with "error" and no "output".
    auto err = parse_action(R"({"error": "ssid required"})");
    check(!err.ok && err.output == "ssid required", "error field used as output");
}

} // namespace

int main() {
    test_status();
    test_networks();
    test_action();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    std::printf("netctl parse: all checks passed\n");
    return EXIT_SUCCESS;
}
