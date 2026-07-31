#!/usr/bin/env python3
"""Behavioural tests for netctl.connect()/disconnect() against a simulated nmcli.

The simulator reproduces the two NetworkManager behaviours that produced the bugs:
  * a profile carrying an 802-11-wireless-security section without key-mgmt is rejected
    with "802-11-wireless-security.key-mgmt: property is missing" (NM's verify());
  * `nmcli dev wifi connect SSID password PSK` builds exactly such a profile whenever
    the SSID is not in the scan cache at that instant.

Nothing is mocked at the subprocess level: netctl.run() is replaced wholesale, so the
tests exercise the real command sequences without a Pi, a radio or root.

    python3 tests/netctl_sim_test.py        # from the repository root
"""
import importlib.util
import os
import sys
import uuid as uuidlib

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "netctl", os.path.join(HERE, "..", "dji_link_beta", "pi", "netctl.py"))
netctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(netctl)

assert not netctl.AP_OFF_FLAG.startswith("/run/dji-ap/"), \
    "hotspot-off flag must survive removal of dji-ap.service's RuntimeDirectory"

KEYMGMT_ERR = "Error: 802-11-wireless-security.key-mgmt: property is missing."


class World:
    """The Pi's networking as the mocked commands see it."""

    def __init__(self, networks, passwords, profiles=(), active=None, ap_active=True,
                 ap_failures=0, ap_recovering=False, link_freqs=()):
        self.networks = networks            # [{ssid, security, chan, signal}]
        self.passwords = passwords          # {ssid: right psk}
        self.profiles = [dict(p) for p in profiles]
        self.active = active                # profile name active on wlan0
        self.ap_active = ap_active
        self.ap_restarts = 0
        self.ap_failures = ap_failures
        self.ap_recovering = ap_recovering
        self.link_freqs = list(link_freqs)
        self.ping_calls = 0
        self.scan_visible = {n["ssid"] for n in networks}
        self.log = []

    def find(self, **kw):
        return [p for p in self.profiles
                if all(p.get(k) == v for k, v in kw.items())]


def make_run(w: World):
    def esc(s):
        return s.replace(":", "\\:")

    def nmcli(a):
        # ---- global options
        getfield = None
        while a and a[0] in ("-t", "-f", "-g", "--wait"):
            if a[0] == "-g":
                getfield = a[1]
                a = a[2:]
            elif a[0] in ("-f", "--wait"):
                if a[0] == "-f":
                    getfield = getfield or a[1]
                a = a[2:]
            else:
                a = a[1:]

        if a[:2] == ["radio", "wifi"] or a[:2] == ["dev", "set"]:
            return 0, ""
        if a[:1] == ["dev"] and len(a) >= 2 and a[1] == "wifi" and a[2] == "list":
            rows = []
            for n in w.networks:
                mark = "*" if w.active and w.find(name=w.active) and \
                    w.find(name=w.active)[0].get("ssid") == n["ssid"] else " "
                rows.append(":".join([mark, esc(n["ssid"]), str(n["signal"]),
                                      n["security"], str(n["chan"])]))
            return 0, "\n".join(rows)
        if a[:1] == ["dev"] and len(a) >= 3 and a[1] == "wifi" and a[2] == "connect":
            ssid = a[3]
            if ssid not in w.scan_visible:
                return 1, KEYMGMT_ERR      # the legacy path's failure mode
            return 0, f"Device 'wlan0' successfully activated"
        if a[:2] == ["dev", "disconnect"]:
            w.active = None
            return 0, "Device 'wlan0' successfully disconnected."
        if a[:1] == ["dev"] and len(a) >= 2 and a[1] == "show":
            if getfield == "GENERAL.STATE":
                return 0, "100 (connected)" if w.active else "30 (disconnected)"
            return 0, "GENERAL.CONNECTION:" + (w.active or "--")
        if a[:1] == ["dev"]:
            st = "connected" if w.active else "disconnected"
            return 0, f"wlan0:wifi:{st}:{w.active or '--'}\nlo:loopback:unmanaged:--"

        if a[:2] == ["con", "show"]:
            if "--active" in a:
                if not w.active:
                    return 0, ""
                p = w.find(name=w.active)[0]
                return 0, f"{esc(p['name'])}:{p['uuid']}:wlan0"
            if "uuid" in a:
                u = a[a.index("uuid") + 1]
                m = w.find(uuid=u)
                if not m:
                    return 10, "Error: unknown connection"
                p = m[0]
                if getfield == "802-11-wireless.ssid":
                    return 0, p.get("ssid", "")
                if getfield == "connection.interface-name":
                    return 0, p.get("iface", "")
                return 0, ""
            if getfield == "NAME":
                return 0, "\n".join(p["name"] for p in w.profiles)
            return 0, "\n".join(
                ":".join([p["uuid"], esc(p["name"]), "802-11-wireless", esc(p["filename"])])
                for p in w.profiles)

        if a[:2] == ["con", "add"]:
            kv = {}
            i = 2
            while i < len(a) - 1:
                kv[a[i]] = a[i + 1]
                i += 2
            name = kv.get("con-name")
            sec_keys = [k for k in kv if k.startswith("802-11-wireless-security.")]
            has_km = "802-11-wireless-security.key-mgmt" in kv
            if sec_keys and not has_km:
                return 1, KEYMGMT_ERR       # NM's verify(), the whole point
            if w.find(name=name):
                return 1, f"Error: connection '{name}' already exists."
            w.profiles.append({
                "uuid": str(uuidlib.uuid4()), "name": name,
                "filename": f"/etc/NetworkManager/system-connections/{name}.nmconnection",
                "ssid": kv.get("ssid", ""), "iface": kv.get("ifname", ""),
                "key_mgmt": kv.get("802-11-wireless-security.key-mgmt", ""),
                "psk": kv.get("802-11-wireless-security.psk", ""),
                "hidden": kv.get("802-11-wireless.hidden", "no"),
                "autoconnect": kv.get("connection.autoconnect", "yes"),
            })
            w.log.append(("add", name, kv.get("802-11-wireless-security.key-mgmt", "")))
            return 0, f"Connection '{name}' successfully added."

        if a[:2] == ["con", "modify"]:
            key = a[3] if a[2] in ("uuid", "id") else a[2]
            m = w.find(uuid=key) or w.find(name=key)
            if not m:
                return 10, "Error: unknown connection"
            if "connection.autoconnect" in a:
                m[0]["autoconnect"] = a[a.index("connection.autoconnect") + 1]
            if "connection.id" in a:
                new = a[a.index("connection.id") + 1]
                if w.active == m[0]["name"]:
                    w.active = new
                m[0]["name"] = new
            return 0, ""

        if a[:2] == ["con", "delete"]:
            key = a[3] if len(a) > 3 and a[2] in ("uuid", "id") else a[2]
            m = w.find(uuid=key) or w.find(name=key)
            if not m:
                return 10, f"Error: unknown connection '{key}'."
            if w.active == m[0]["name"]:
                w.active = None
            w.profiles.remove(m[0])
            w.log.append(("delete", m[0]["name"], ""))
            return 0, "Connection successfully deleted."

        if a[:2] == ["con", "up"]:
            key = a[3] if len(a) > 3 and a[2] in ("uuid", "id") else a[2]
            m = w.find(uuid=key) or w.find(name=key)
            if not m:
                return 10, f"Error: unknown connection '{key}'."
            p = m[0]
            net = next((n for n in w.networks if n["ssid"] == p["ssid"]), None)
            if net is None:
                return 4, "Error: Connection activation failed: no suitable device found"
            if net["security"] and net["security"] != "open":
                if not p["key_mgmt"]:
                    return 1, KEYMGMT_ERR
                if p["psk"] != w.passwords.get(p["ssid"]):
                    return 4, ("Error: Connection activation failed: "
                               "(7) Secrets were required, but not provided.")
            w.active = p["name"]
            return 0, f"Connection successfully activated"

        if a[:2] == ["con", "reload"]:
            return 0, ""
        return 0, ""

    def run(*args, check=False, timeout=None):
        a = list(args)
        if a[0] == "nmcli":
            return nmcli(a[1:])
        if a[0] == "systemctl":
            if a[1] == "is-active":
                if w.ap_recovering:
                    return 3, "activating"
                return (0, "active") if w.ap_active else (3, "inactive")
            if a[1] in ("restart", "start"):
                w.ap_restarts += 1
                w.ap_active = True
                return 0, ""
            if a[1] == "stop":
                w.ap_active = False
                return 0, ""
            return 0, ""
        if a[0] == "bash":                      # ap.sh
            sub = a[-1]
            if sub == "health":
                return (0, "ok") if w.ap_active else (1, "dji-ap is not active")
            if sub == "chan":
                return 0, "g 6"
            if sub == "failures":
                return 0, str(w.ap_failures)
            if sub == "reset-failures":
                w.ap_failures = 0
                return 0, ""
            return 0, ""
        if a[0] == "iw":
            if a[-1] == "link":
                freq = w.link_freqs.pop(0) if w.link_freqs else None
                if freq is None:
                    return 0, "Not connected."
                return 0, ("Connected to aa:bb:cc:dd:ee:ff (on wlan0)\n"
                           "\tSSID: TestNet\n"
                           f"\tfreq: {freq}\n")
            if "station" in a:
                return 0, ""
            if "info" in a:
                return 0, "channel 6 (2437 MHz)"
            return 0, ""
        if a[0] == "ping":
            w.ping_calls += 1
            return (0, "") if w.active else (1, "")
        if a[0] == "hostname":
            return 0, "10.42.0.1 192.168.1.55"
        return 0, ""
    return run


FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def setup(w):
    netctl.run = make_run(w)
    netctl._hostapd_mode = True
    netctl._internet_cache = (0.0, False)
    # ap_conf_channel reads a real file; keep it deterministic.
    netctl.ap_conf_channel = lambda: "6"
    netctl._restart_ap_async = lambda reason, delay=0.7: w.__setattr__(
        "ap_restarts", w.ap_restarts + 1)


HOME = [{"ssid": "ASUS_65", "security": "WPA2", "chan": 6, "signal": 80},
        {"ssid": "Cafe", "security": "", "chan": 11, "signal": 40},
        {"ssid": "New3", "security": "WPA3", "chan": 1, "signal": 55},
        {"ssid": "Work", "security": "WPA2 802.1X", "chan": 6, "signal": 60}]
PW = {"ASUS_65": "hunter2", "New3": "sae-pass"}

print("A. fresh join of a WPA2 network")
w = World(HOME, PW)
setup(w)
r = netctl.connect("ASUS_65", "hunter2")
check(r["ok"], "connect reports ok")
check(w.active == "ASUS_65", f"profile is renamed to the SSID (active={w.active})")
p = w.find(name="ASUS_65")[0]
check(p["key_mgmt"] == "wpa-psk", f"key-mgmt is set explicitly ({p['key_mgmt']!r})")
check(p["hidden"] == "no", "not marked hidden (it was in the scan)")
check(w.ap_restarts == 1, "a successful join schedules one clean AP reassociation cycle")

print("\nB. rejoin with a stale key-mgmt-less profile in the way (the reported bug)")
stale = {"uuid": "stale-uuid", "name": "netplan-wlan0-ASUS_65",
         "filename": "/run/NetworkManager/system-connections/netplan-wlan0-ASUS_65.nmconnection",
         "ssid": "ASUS_65", "iface": "wlan0", "key_mgmt": "", "psk": "",
         "hidden": "no", "autoconnect": "yes"}
w = World(HOME, PW, profiles=[stale], active="netplan-wlan0-ASUS_65")
setup(w)
r = netctl.connect("ASUS_65", "hunter2")
check(r["ok"], "connect succeeds despite the stale profile")
check(not w.find(name="netplan-wlan0-ASUS_65"), "the stale duplicate was removed AFTER success")
check(len(w.find(ssid="ASUS_65")) == 1, "exactly one profile is left for the SSID")

print("\nC. wrong password must not strand the Pi (the v0.8.1 regression)")
good = {"uuid": "keepme", "name": "preconfigured", "filename": "/etc/NM/preconfigured",
        "ssid": "ASUS_65", "iface": "wlan0", "key_mgmt": "wpa-psk", "psk": "hunter2",
        "hidden": "no", "autoconnect": "yes"}
w = World(HOME, PW, profiles=[good], active="preconfigured")
setup(w)
r = netctl.connect("ASUS_65", "wrong-password")
check(not r["ok"], "connect reports failure")
check(bool(w.find(name="preconfigured")), "the pre-existing working profile still exists")
check(w.find(name="preconfigured")[0]["autoconnect"] == "yes", "its autoconnect was restored")
check(w.active == "preconfigured", f"the Pi is back on its old network (active={w.active})")
check(not w.find(name="dji-uplink-ASUS_65"), "the failed profile was cleaned up")
check("wrong password" in r["output"], "the error names the likely cause")
check(w.ap_restarts == 0, "a failed join does not interrupt a healthy field AP")

print("\nD. open network")
w = World(HOME, PW)
setup(w)
r = netctl.connect("Cafe", None)
check(r["ok"], "connect ok")
check(w.find(name="Cafe")[0]["key_mgmt"] == "", "no security section on an open net")

print("\nE. WPA3-only network uses sae")
w = World(HOME, PW)
setup(w)
r = netctl.connect("New3", "sae-pass")
check(r["ok"], "connect ok")
check(w.find(name="New3")[0]["key_mgmt"] == "sae", "key-mgmt is sae")

print("\nF. SSID not in the scan is treated as hidden")
w = World(HOME, dict(PW, Hidden1="pw"))
w.networks = HOME + []            # Hidden1 deliberately absent from the scan
w.passwords["Hidden1"] = "pw"
w.networks.append({"ssid": "Hidden1", "security": "WPA2", "chan": 6, "signal": 0})
w.scan_visible = {n["ssid"] for n in HOME}
setup(w)
# make the scan not report it, while con up still can find it
orig = netctl._scan_entry
netctl._scan_entry = lambda s: None if s == "Hidden1" else orig(s)
r = netctl.connect("Hidden1", "pw")
netctl._scan_entry = orig
check(r["ok"], "connect ok")
check(w.find(name="Hidden1")[0]["hidden"] == "yes", "profile is marked hidden")

print("\nG. WPA-Enterprise is refused cleanly, nothing is touched")
w = World(HOME, PW, profiles=[dict(good)], active="preconfigured")
setup(w)
r = netctl.connect("Work", "whatever")
check(not r["ok"], "connect refuses")
check("802.1X" in r["output"], "the error explains why")
check(bool(w.find(name="preconfigured")), "existing profiles untouched")
check(w.active == "preconfigured", "still on the old network")

print("\nH. joining our own AP SSID is refused")
w = World(HOME, PW)
setup(w)
r = netctl.connect(netctl.AP_SSID, "raspberry")
check(not r["ok"] and "own access point" in r["output"], "refused with a clear reason")

print("\nI. disconnect leaves a healthy AP alone")
w = World(HOME, PW, profiles=[dict(good)], active="preconfigured")
setup(w)
before = w.ap_restarts
r = netctl.disconnect()
check(r["ok"], "disconnect ok")
check(w.ap_restarts == before, "the AP was NOT restarted (laptop keeps its link)")
check("unchanged" in r["note"], "the note says so")

print("\nJ. disconnect repairs a broken AP")
w = World(HOME, PW, profiles=[dict(good)], active="preconfigured", ap_active=False)
setup(w)
before = w.ap_restarts
netctl.disconnect()
check(w.ap_restarts > before, "a dead AP is restarted")

print("\nK. a failed join still leaves the AP up")
w = World(HOME, PW, ap_active=False)
setup(w)
netctl.connect("ASUS_65", "nope")
check(w.ap_restarts > 0, "the AP was brought back after the failure")

print("\nL. status keeps the shape the C++ client parses")
w = World(HOME, PW, profiles=[dict(good)], active="preconfigured")
setup(w)
import json
body = json.dumps(netctl.status())
ap_at, up_at = body.find('"ap"'), body.find('"uplink"')
check(ap_at != -1 and up_at != -1 and ap_at < up_at, '"ap" comes before "uplink"')
check(body.find('"state"', ap_at) < body.find('"uplink"'), '"ap" carries the first "state"')
check('"internet"' in body and '"ap_ssid"' in body and '"ap_psk"' in body, "required keys present")
check(netctl.healthz()["service"] == "dji-link-netctl", "/healthz identifies the Pi without commands")
check(w.ping_calls == 0, "/status never waits for an internet probe")
check(netctl.refresh_internet(), "the background/doctor probe updates internet state")
check(w.ping_calls == 1 and netctl.have_internet(), "cached internet state is immediate")


# ------------------------------------------------------------------ the reported cycle
print("\nM. the reported cycle: A -> off -> B -> off -> A again")
NETS = [{"ssid": "ASUS_65", "security": "WPA2", "chan": 6, "signal": 80},
        {"ssid": "Phone_AP", "security": "WPA2", "chan": 11, "signal": 60}]
w = World(NETS, {"ASUS_65": "hunter2", "Phone_AP": "phonepass"})
setup(w)
steps = [("connect", "ASUS_65", "hunter2"), ("disconnect",),
         ("connect", "Phone_AP", "phonepass"), ("disconnect",),
         ("connect", "ASUS_65", "hunter2")]
allok = True
for s in steps:
    r = netctl.connect(s[1], s[2]) if s[0] == "connect" else netctl.disconnect()
    label = f"{s[0]} {s[1] if len(s) > 1 else ''}".strip()
    if not r["ok"]:
        allok = False
        print(f"       {label}: {r['output'][:120]}")
    check(r["ok"], label)
check(allok, "every step of the disconnect/reconnect cycle succeeded")
check(w.active == "ASUS_65", f"ends up back on ASUS_65 (active={w.active})")
check(sorted(p['name'] for p in w.profiles) == ['ASUS_65', 'Phone_AP'], f"one profile per network, named after it ({[p['name'] for p in w.profiles]})")

print("\nN. same cycle, but the scan cache is empty on the way back (legacy failure mode)")
w = World(NETS, {"ASUS_65": "hunter2", "Phone_AP": "phonepass"})
setup(w)
netctl.connect("ASUS_65", "hunter2")
netctl.disconnect()
netctl.connect("Phone_AP", "phonepass")
netctl.disconnect()
w.scan_visible = set()                       # what breaks `dev wifi connect`
orig = netctl._scan_entry
netctl._scan_entry = lambda s: None          # nothing in the scan cache at all
r = netctl.connect("ASUS_65", "hunter2")
netctl._scan_entry = orig
check(r["ok"], "still joins with an empty scan cache (explicit key-mgmt, hidden probe)")
check(w.active == "ASUS_65", "and is on the right network")


print("\nO. reconnect with an empty password uses the saved secret")
w = World(HOME, PW, profiles=[dict(good)], active=None)
setup(w)
r = netctl.connect("ASUS_65", None)
check(r["ok"], "connect ok without a password")
check(w.active == "preconfigured", f"used the saved profile (active={w.active})")
check("saved password" in r["note"], "the note explains what happened")
check(len(w.find(ssid="ASUS_65")) == 1, "no extra profile was created")

print("\nP. no password and nothing saved -> clear error, nothing changed")
w = World(HOME, PW)
setup(w)
r = netctl.connect("ASUS_65", None)
check(not r["ok"], "refused")
check("needs a password" in r["output"], f"clear message: {r['output']}")
check(not w.profiles, "no profile left behind")

print("\nQ. netctl must not add a tight loop to systemd's low-rate AP recovery")
w = World(HOME, PW, ap_active=False, ap_failures=3)
setup(w)
netctl.ensure_ap("test")
check(w.ap_restarts == 0, "watchdog restart is suppressed after three short failures")
netctl.hotspot(True)
check(w.ap_failures == 0, "an explicit operator retry clears the failure latch")

print("\nR. netctl must not interrupt systemd while an AP attempt is activating")
w = World(HOME, PW, ap_active=False, ap_recovering=True)
setup(w)
netctl.ensure_ap("test")
check(w.ap_restarts == 0, "an activating AP attempt is left alone")

print("\nS. watchdog channel checks must preserve the AP without a stable uplink")
w = World(HOME, PW, profiles=[dict(good)], active=None)
setup(w)
check(netctl.confirmed_uplink_channel(0) == "",
      "no uplink produces no retune channel (field AP stays on 10.42.0.1)")

w = World(HOME, PW, profiles=[dict(good)], active="preconfigured",
          link_freqs=[2442, None])
setup(w)
check(netctl.confirmed_uplink_channel(0) == "",
      "a link that disappears between checks is not a channel change")

w = World(HOME, PW, profiles=[dict(good)], active="preconfigured",
          link_freqs=[2442, 2437])
setup(w)
check(netctl.confirmed_uplink_channel(0) == "",
      "a changing uplink channel is not acted on yet")

w = World(HOME, PW, profiles=[dict(good)], active="preconfigured",
          link_freqs=[2442, 2442])
setup(w)
check(netctl.confirmed_uplink_channel(0) == "7",
      "two stable connected observations confirm channel 7")

print("\nT. PC discovery must accept an offline Pi through /healthz")
nf_spec = importlib.util.spec_from_file_location(
    "netfind", os.path.join(HERE, "..", "dji_link_beta", "netfind.py"))
netfind = importlib.util.module_from_spec(nf_spec)
nf_spec.loader.exec_module(netfind)
nf_calls = []

def fake_netctl(host, path, body=None, timeout=8.0):
    nf_calls.append(path)
    if path == "/healthz":
        return {"ok": True, "service": "dji-link-netctl", "address": "10.42.0.1"}
    if path == "/status":
        raise TimeoutError("offline status")
    raise AssertionError(path)

netfind._netctl = fake_netctl
check(netfind.is_pi_host("10.42.0.1"), "offline /healthz is enough to identify the Pi")
check(nf_calls == ["/healthz"], "discovery does not wait for detailed status")
nf_calls.clear()
check(netfind.pi_status("10.42.0.1") is not None,
      "detailed status falls back to the local health endpoint")
check(nf_calls == ["/status", "/healthz"], "status fallback uses /healthz")

print("\nU. Windows explicitly disconnects before rejoining the Pi AP")
nf_commands = []

class FakeCompleted:
    returncode = 0
    stdout = "ok"

def fake_run(args, **kwargs):
    nf_commands.append(list(args))
    return FakeCompleted()

netfind._is_windows = lambda: True
netfind.subprocess.run = fake_run
netfind.time.sleep = lambda _seconds: None
netfind.is_pi_host = lambda host: host == netfind.AP_GATEWAY
check(netfind.join_ap("PI_DJI_LINK-test"), "Windows AP rejoin reaches the Pi")
disconnect_at = next((i for i, c in enumerate(nf_commands) if c[:3] == ["netsh", "wlan", "disconnect"]), -1)
connect_at = next((i for i, c in enumerate(nf_commands) if c[:3] == ["netsh", "wlan", "connect"]), -1)
check(0 <= disconnect_at < connect_at, "netsh disconnect happens before netsh connect")

print("\nV. beta uplink setup reconnects and verifies the requested SSID")
statuses = [
    {"ap_ssid": "PI_DJI_LINK-test"},
    {"uplink_ssid": "ASUS_65", "uplink": {"connection": "ASUS_65"}},
]
rejoined = []
netfind.pi_status = lambda host: statuses.pop(0)
netfind._netctl = lambda host, path, body=None, timeout=8.0: {"ok": True, "output": "joined"}
netfind.join_ap = lambda ssid, psk=netfind.AP_DEFAULT_PSK: rejoined.append(ssid) or True
r = netfind.pi_connect_wifi(netfind.AP_GATEWAY, "ASUS_65", "hunter2")
check(r.get("ok") and r.get("ap_reconnected"), "successful uplink restores the Windows AP link")
check(rejoined == ["PI_DJI_LINK-test"], "the same per-device Pi AP is rejoined")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    sys.exit(1)
print("all checks passed")
