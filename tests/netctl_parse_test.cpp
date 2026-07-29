// Parsing of the Pi netctl API replies (netfind::parse_*), checked against real
// json.dumps output from dji_link_beta/pi/netctl.py. No Pi and no sockets needed, so
// this runs on every CI runner. Plain asserts + a nonzero exit: no test framework is
// pulled in for what is a pure-function check.
//
// test_v082_replies() is the compatibility guard for the Pi bundle: v0.8.2 renamed the
// uplink profile, appended six keys to /status and reworded the /connect note, and the
// desktop client is NOT rebuilt for a Pi-only release. Every sample there is verbatim
// output from the v0.8.2 netctl.py, so a Pi change that would break the shipped client
// fails here instead of in the field.

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

// ---------------------------------------------------------------- v0.8.2 bundle
// Verbatim netctl.py v0.8.2 output. The six trailing keys are new; "ap" and "uplink"
// keep their position and shape because parse_status() reads "state"/"connection"
// relative to those two keys rather than by a real JSON path.
const char* kStatusV082 = R"({"ap": {"iface": "uap0", "state": "active",
"connection": "dji-ap", "ssid": "PI_DJI_LINK-9f3c", "address": "10.42.0.1",
"mode": "hostapd"}, "uplink": {"iface": "wlan0", "state": "connected",
"connection": "HomeNet"}, "internet": true, "addresses": ["10.42.0.1",
"192.168.1.57"], "ap_ssid": "PI_DJI_LINK-9f3c", "ap_psk": "raspberry",
"ap_healthy": true, "ap_detail": "ok", "ap_channel": "6", "ap_clients": 0,
"uplink_ssid": "HomeNet", "uplink_ip": "192.168.1.57/24"})";

// v0.8.2 keeps the AP up when the uplink goes away — the case the whole release is
// about — so ap_active must still read true here.
const char* kStatusV082NoUplink = R"({"ap": {"iface": "uap0", "state": "active",
"connection": "dji-ap", "ssid": "PI_DJI_LINK-9f3c", "address": "10.42.0.1",
"mode": "hostapd"}, "uplink": {"iface": "wlan0", "state": "disconnected",
"connection": "--"}, "internet": false, "addresses": ["10.42.0.1"],
"ap_ssid": "PI_DJI_LINK-9f3c", "ap_psk": "raspberry", "ap_healthy": true,
"ap_detail": "ok", "ap_channel": "6", "ap_clients": 0, "uplink_ssid": "",
"uplink_ip": ""})";

const char* kScanV082 = R"({"networks": [{"ssid": "Cafe", "signal": 88,
"security": "open", "in_use": false}, {"ssid": "HomeNet", "signal": 74,
"security": "WPA1 WPA2", "in_use": false}, {"ssid": "Phone_AP", "signal": 21,
"security": "WPA2 WPA3", "in_use": true}]})";

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

void test_v082_replies() {
    auto s = parse_status(kStatusV082);
    check(s.has_value(), "v0.8.2 status parses");
    if (!s)
        return;
    check(s->ap_active, "v0.8.2 ap active");
    check(s->internet, "v0.8.2 internet");
    check(s->ap_ssid == "PI_DJI_LINK-9f3c", "v0.8.2 ap ssid past the new keys");
    check(s->ap_psk == "raspberry", "v0.8.2 ap psk past the new keys");
    check(s->uplink_state == "connected", "v0.8.2 uplink state");
    // v0.8.2 renames the winning profile to the SSID, so this is an exact match again;
    // gui.cpp's "did it join?" check also accepts a name that merely contains the SSID.
    check(s->uplink_name == "HomeNet", "v0.8.2 uplink profile is named after the SSID");

    auto d = parse_status(kStatusV082NoUplink);
    check(d.has_value(), "v0.8.2 no-uplink status parses");
    if (d) {
        check(d->ap_active, "AP survives the uplink going away");
        check(!d->internet, "no internet without an uplink");
        check(d->uplink_name.empty(), "no uplink name");
    }

    auto nets = parse_networks(kScanV082);
    check(nets.size() == 3, "v0.8.2 scan: three networks");
    if (nets.size() != 3)
        return;
    check(nets[0].ssid == "Cafe" && nets[0].open(), "open network first (strongest)");
    check(nets[1].ssid == "HomeNet" && !nets[1].open(), "WPA1 WPA2 is not open");
    check(nets[2].ssid == "Phone_AP" && nets[2].in_use, "in_use survives the new fields");

    // v0.8.2 reworded "note" and appends a hint to "output" on a bad password. "output"
    // must still win over "note", and the hint must reach the UI.
    auto joined = parse_action(R"({"ok": true, "output": "Connection successfully activated",
"note": "AP unchanged — the laptop stays connected"})");
    check(joined.ok, "v0.8.2 connect ok");
    check(joined.output.rfind("Connection", 0) == 0, "v0.8.2 output preferred over note");

    // Custom delimiter: the hint ends in "?)", and `)"` would close a plain raw literal.
    auto refused = parse_action(R"J({"ok": false, "output": "Error: Connection activation failed:
(7) Secrets were required, but not provided.  (wrong password?)", "note": "AP unchanged"})J");
    check(!refused.ok, "v0.8.2 connect failure");
    check(refused.output.find("wrong password?") != std::string::npos,
          "v0.8.2 password hint reaches the UI");

    auto enterprise = parse_action(
        R"({"ok": false, "output": "'Work' is a WPA-Enterprise (802.1X) network; netctl can only
join personal WPA/WPA2/WPA3 networks"})");
    check(!enterprise.ok && enterprise.output.find("802.1X") != std::string::npos,
          "v0.8.2 802.1X refusal reaches the UI");
}

} // namespace

int main() {
    test_status();
    test_networks();
    test_action();
    test_v082_replies();
    if (failures) {
        std::printf("%d check(s) failed\n", failures);
        return EXIT_FAILURE;
    }
    std::printf("netctl parse: all checks passed\n");
    return EXIT_SUCCESS;
}
