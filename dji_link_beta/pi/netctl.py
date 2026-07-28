#!/usr/bin/env python3
"""
netctl.py — Wi-Fi control for the Pi jump-host: access point + optional internet uplink.

Two jobs:
  1. Always serve an access point the laptop can join in the field, where there is no
     other network. This is what makes the link work away from home.
  2. Optionally join an existing Wi-Fi network as an uplink, so the same AP also has
     internet (for coding at home, updates, DJI account login).

The Pi Zero 2 W has ONE radio. A second virtual interface (uap0) carries the AP while
wlan0 stays the client. The AP itself is run by hostapd + dnsmasq (the dji-ap systemd
unit, see pi/ap.sh) rather than NetworkManager: NM's AP goes through wpa_supplicant,
which advertises WPS and makes Windows demand a PIN instead of the passphrase. hostapd
with wps_state=0 is a plain WPA2 network every OS joins with just the password. When
dji-ap is absent (a Pi not yet re-set-up) we fall back to NM's ipv4.method=shared AP.

The chip requires AP and client to share a channel: joining an uplink therefore
retunes the AP (netctl restarts dji-ap), and clients must reconnect. Hardware, not a bug.

Usage (on the Pi):
    sudo python3 netctl.py status
    sudo python3 netctl.py scan
    sudo python3 netctl.py connect "MySSID" "password"
    sudo python3 netctl.py disconnect
    sudo python3 netctl.py hotspot on|off
    sudo python3 netctl.py serve            # HTTP API on :9911 for the PC client

HTTP API (used by pc_client's network panel):
    GET  /status            -> {"ap": {...}, "uplink": {...}, "internet": bool}
    GET  /scan              -> {"networks": [{"ssid","signal","security","in_use"}]}
    POST /connect           <- {"ssid": "...", "psk": "..."}
    POST /disconnect
    POST /hotspot           <- {"on": true}
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

AP_CON = "dji-link-ap"       # legacy NetworkManager AP profile (fallback path)
AP_SERVICE = "dji-ap"        # hostapd+dnsmasq AP unit created by setup_pi.sh (pi/ap.sh)
AP_UNIT_PATH = "/etc/systemd/system/dji-ap.service"
AP_IFACE = "uap0"
STA_IFACE = "wlan0"
AP_PSK = "raspberry"          # default; >= 8 chars for WPA2
AP_ADDR = "10.42.0.1"
PORT = 9911


def ap_ssid() -> str:
    """Stable, per-device AP name: PI_DJI_LINK-<4 hex>. The suffix (from machine-id)
    lets the PC client recognise a Pi AP by prefix while staying unique per board."""
    suffix = "0000"
    try:
        with open("/etc/machine-id") as f:
            mid = f.read().strip()
            if len(mid) >= 4:
                suffix = mid[-4:]
    except OSError:
        pass
    return f"PI_DJI_LINK-{suffix}"


AP_SSID = ap_ssid()


def run(*args: str, check: bool = False) -> tuple[int, str]:
    """Run a command, return (rc, output). Never raises unless check=True."""
    p = subprocess.run(args, capture_output=True, text=True)
    out = (p.stdout + p.stderr).strip()
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {out}")
    return p.returncode, out


def nmcli(*args: str, check: bool = False) -> tuple[int, str]:
    return run("nmcli", *args, check=check)


def systemctl(*args: str) -> tuple[int, str]:
    return run("systemctl", *args)


_hostapd_mode: bool | None = None


def hostapd_mode() -> bool:
    """True when the hostapd AP unit (dji-ap.service) is installed — the normal path on a
    Pi set up by the current setup_pi.sh. False on an older Pi, or once the unit file is
    removed, where we fall back to the NetworkManager ipv4.method=shared AP so nothing
    regresses. Detected by the unit file itself so the switch is unambiguous."""
    global _hostapd_mode
    if _hostapd_mode is None:
        _hostapd_mode = os.path.exists(AP_UNIT_PATH)
    return _hostapd_mode


# ---------------------------------------------------------------- AP interface
def ensure_ap_iface() -> bool:
    """Create the uap0 virtual interface if the driver allows AP+STA concurrency."""
    rc, out = run("iw", "dev")
    if AP_IFACE in out:
        return True
    rc, combos = run("iw", "list")
    if "valid interface combinations" in combos and "AP" not in combos:
        print(f"[netctl] this radio reports no AP capability:\n{combos[:400]}")
        return False
    rc, out = run("iw", "dev", STA_IFACE, "interface", "add", AP_IFACE, "type", "__ap")
    if rc != 0:
        print(f"[netctl] could not create {AP_IFACE}: {out}")
        return False
    # A stable MAC keeps the laptop from seeing a "new" network on every reboot.
    run("ip", "link", "set", AP_IFACE, "up")
    return True


def ensure_ap_profile() -> None:
    """Define the AP connection. ipv4.method=shared gives DHCP + NAT for free, which is
    what routes the laptop's traffic out through whatever uplink wlan0 has."""
    rc, out = nmcli("-t", "-f", "NAME", "con", "show")
    if AP_CON in out.split("\n"):
        return
    nmcli("con", "add", "type", "wifi", "ifname", AP_IFACE, "con-name", AP_CON,
          "autoconnect", "yes", "ssid", AP_SSID, check=True)
    nmcli("con", "modify", AP_CON,
          "802-11-wireless.mode", "ap",
          "802-11-wireless.band", "bg",
          "ipv4.method", "shared",
          "ipv4.addresses", f"{AP_ADDR}/24",
          "wifi-sec.key-mgmt", "wpa-psk",
          "wifi-sec.psk", AP_PSK,
          "connection.autoconnect-priority", "10", check=True)


def ensure_forwarding() -> None:
    """Make the AP actually route to the uplink.

    ipv4.method=shared is supposed to set up ip_forward + NAT itself, but it does that
    through iptables/nftables and dnsmasq — on a Lite image where those are missing NM
    still brings the AP up, so a laptop associates, gets an address and has no way out.
    Re-asserting the two pieces here is idempotent and costs nothing when NM did its job.
    """
    run("sysctl", "-w", "net.ipv4.ip_forward=1")
    rc, out = run("iptables", "-t", "nat", "-S", "POSTROUTING")
    if rc != 0:
        print("[netctl] iptables unavailable; cannot verify NAT for the AP")
        return
    if "MASQUERADE" in out:
        return                              # NM (or a previous run) already set it up
    print(f"[netctl] no NAT rule found; adding masquerade {AP_IFACE} -> {STA_IFACE}")
    run("iptables", "-t", "nat", "-A", "POSTROUTING", "-o", STA_IFACE, "-j", "MASQUERADE")
    run("iptables", "-A", "FORWARD", "-i", STA_IFACE, "-o", AP_IFACE,
        "-m", "state", "--state", "RELATED,ESTABLISHED", "-j", "ACCEPT")
    run("iptables", "-A", "FORWARD", "-i", AP_IFACE, "-o", STA_IFACE, "-j", "ACCEPT")


def hotspot(on: bool) -> dict:
    if hostapd_mode():
        rc, out = systemctl("start" if on else "stop", AP_SERVICE)
        return {"ok": rc == 0, "output": out, "mode": "hostapd"}
    # Legacy NetworkManager AP fallback.
    if on:
        if not ensure_ap_iface():
            return {"ok": False, "error": "no AP-capable interface (uap0 could not be created)"}
        ensure_ap_profile()
        rc, out = nmcli("con", "up", AP_CON)
        if rc == 0:
            ensure_forwarding()
        return {"ok": rc == 0, "output": out, "mode": "nm"}
    rc, out = nmcli("con", "down", AP_CON)
    return {"ok": rc == 0, "output": out, "mode": "nm"}


# ---------------------------------------------------------------- scan / uplink
def scan() -> list[dict]:
    """Visible networks, strongest first. Rescan on the client interface only — the AP
    interface must not leave its channel or connected laptops drop."""
    nmcli("dev", "wifi", "rescan", "ifname", STA_IFACE)
    time.sleep(2)
    rc, out = nmcli("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "dev", "wifi", "list",
                    "ifname", STA_IFACE)
    nets: dict[str, dict] = {}
    for line in out.split("\n"):
        if not line.strip():
            continue
        # -t escapes ':' inside fields as '\:'
        parts = [p.replace("\\:", ":") for p in _split_nmcli(line)]
        if len(parts) < 4:
            continue
        in_use, ssid, signal, sec = parts[0], parts[1], parts[2], parts[3]
        if not ssid:
            continue                      # hidden network
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        # The same SSID appears once per band/AP; keep the strongest.
        if ssid not in nets or sig > nets[ssid]["signal"]:
            nets[ssid] = {"ssid": ssid, "signal": sig,
                          "security": sec or "open", "in_use": in_use == "*"}
    return sorted(nets.values(), key=lambda n: -n["signal"])


def _split_nmcli(line: str) -> list[str]:
    """Split an nmcli -t line on unescaped ':'."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += "\\" + ch if ch != ":" else ":"
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _wifi_profiles() -> list[tuple[str, str, str, str]]:
    """Saved Wi-Fi profiles as (uuid, name, ssid, filename).

    Two steps on purpose. Setting properties like 802-11-wireless.ssid are NOT valid
    fields for the `con show` *list* — nmcli rejects them with rc=2 and prints
    "invalid field", which is easy to mistake for "no profiles matched". The SSID can
    only be read per profile, so list first (UUID/NAME/TYPE/FILENAME are all valid list
    fields), then query each Wi-Fi profile by UUID.
    """
    out_list: list[tuple[str, str, str, str]] = []
    rc, out = nmcli("-t", "-f", "UUID,NAME,TYPE,FILENAME", "con", "show")
    if rc != 0:
        print(f"[netctl] could not list connections: {out}")
        return out_list
    for line in out.splitlines():
        parts = [p.replace("\\:", ":") for p in _split_nmcli(line)]
        if len(parts) < 4:
            continue
        uuid, name, typ, fname = parts[0], parts[1], parts[2], ":".join(parts[3:])
        if "wireless" not in typ and "wifi" not in typ:
            continue
        rc_if, if_out = nmcli("-t", "-f", "connection.interface-name", "con", "show", "uuid", uuid)
        bound_if = if_out.split(":", 1)[1].strip() if rc_if == 0 and ":" in if_out else ""
        if bound_if == AP_IFACE:
            continue                          # the access point's own profile
        rc2, ssid_out = nmcli("-t", "-f", "802-11-wireless.ssid", "con", "show", "uuid", uuid)
        if rc2 != 0:
            continue
        # Output is "802-11-wireless.ssid:MySSID"; the SSID itself may contain ':'.
        ssid = ""
        for l2 in ssid_out.splitlines():
            fields = [p.replace("\\:", ":") for p in _split_nmcli(l2)]
            if len(fields) >= 2 and fields[0].endswith("ssid"):
                ssid = ":".join(fields[1:])
                break
        out_list.append((uuid, name, ssid, fname))
    return out_list


def _sec_key_mgmt(ssid: str) -> str:
    """Pick key-mgmt for `ssid` from the scan: 'sae' only for a WPA3-only AP.

    'wpa-psk' covers WPA2 *and* WPA3-transition APs, so it is the right default. A
    WPA3-only AP (SECURITY says WPA3 with no WPA2) needs 'sae' and rejects wpa-psk.
    """
    rc, out = nmcli("-t", "-f", "SSID,SECURITY", "dev", "wifi", "list", "ifname", STA_IFACE)
    if rc != 0:
        return "wpa-psk"
    for line in out.splitlines():
        parts = [p.replace("\\:", ":") for p in _split_nmcli(line)]
        if len(parts) < 2 or parts[0] != ssid:
            continue
        sec = parts[1].upper()
        if "WPA3" in sec and "WPA2" not in sec:
            print(f"[netctl] '{ssid}' looks WPA3-only; using key-mgmt=sae")
            return "sae"
        break
    return "wpa-psk"


def _delete_profiles_for_ssid(ssid: str) -> None:
    """Delete every saved profile whose wireless SSID matches `ssid`.

    nmcli con delete takes a profile NAME or UUID, not an SSID. On a netplan/NM Pi the
    profile for 'ASUS_65' is named 'netplan-wlan0-ASUS_65', so deleting by the bare SSID
    silently does nothing; the stale profile is then reused by the next `dev wifi connect`,
    which discards the supplied password and fails with
    '802-11-wireless-security-key-mgmt.property-is-missing' because the leftover profile
    has no security section. Delete by UUID — names are not unique, SSIDs are what we match.
    """
    for uuid, name, prof_ssid, fname in _wifi_profiles():
        if prof_ssid != ssid:
            continue
        # Never delete the AP. Checking the name alone is not enough: the profile may be
        # named anything, and a user could ask to join a network whose SSID equals ours.
        if name == AP_CON or prof_ssid == AP_SSID:
            continue
        # A netplan/cloud-init YAML in /etc/netplan can regenerate the profile after we
        # delete it, so say so rather than letting the next failure look inexplicable.
        if "netplan" in fname or fname.startswith("/usr/lib"):
            print(f"[netctl] note: '{name}' is backed by {fname}; it may be regenerated")
        # "uuid" keyword: con delete accepts id|uuid|path, and a profile NAME is not
        # unique, so an ambiguous bare argument could match the wrong profile.
        rc, out = nmcli("con", "delete", "uuid", uuid)
        if rc == 0:
            print(f"[netctl] deleted stale profile '{name}' for SSID '{ssid}'")
        else:
            print(f"[netctl] could not delete profile '{name}': {out}")


def connect(ssid: str, psk: str | None) -> dict:
    """Join a network as the uplink, keeping the AP up."""
    # Delete any existing profile for this SSID so the given password is always
    # applied. Must search by SSID field — the profile name rarely equals the SSID.
    if psk is not None:
        _delete_profiles_for_ssid(ssid)

    args = ["dev", "wifi", "connect", ssid, "ifname", STA_IFACE]
    if psk:
        args += ["password", psk]
    rc, out = nmcli(*args)
    ok = rc == 0

    # Fallback: build the profile by hand. `dev wifi connect` is a convenience wrapper that
    # reuses whatever profile it finds; if one survived deletion (renamed, read-only, owned
    # by netplan) it still fails with a missing key-mgmt. An explicit profile sets every
    # security property itself, so there is nothing left to inherit from a stale one.
    if not ok and psk:
        print(f"[netctl] dev wifi connect failed ({out.strip()}); building profile explicitly")
        _delete_profiles_for_ssid(ssid)
        tmp = f"dji-uplink-{ssid}"
        nmcli("con", "delete", "id", tmp)    # a leftover from a previous failed attempt
        # Every security property in ONE call. Setting key-mgmt in a separate `con modify`
        # from the properties it depends on can itself fail validation.
        rc_add, out_add = nmcli(
            "con", "add", "type", "wifi", "ifname", STA_IFACE, "con-name", tmp,
            "autoconnect", "yes", "ssid", ssid,
            "802-11-wireless.mode", "infrastructure",
            "802-11-wireless-security.key-mgmt", _sec_key_mgmt(ssid),
            "802-11-wireless-security.psk", psk,
            # psk-flags 0 = system-owned. The default (agent-owned) makes NM wait for a
            # secret agent that does not exist on a headless Pi, which fails with
            # "(7) Secrets were required, but not provided".
            "802-11-wireless-security.psk-flags", "0",
            "ipv4.method", "auto")
        if rc_add == 0:
            rc, out = nmcli("con", "up", tmp)
            ok = rc == 0
            if not ok:
                nmcli("con", "delete", "id", tmp)  # do not leave a broken profile behind
        else:
            out = f"{out}\nprofile creation failed: {out_add}"
    # The radio just retuned to the uplink's channel; the AP must follow (single radio).
    if ok:
        if hostapd_mode():
            # Restart MUST happen in a background thread: systemctl restart takes the AP
            # down before this function returns, tearing down the TCP connection the HTTP
            # server is about to reply on. Without the thread the client always sees
            # "Pi did not answer" even on a successful join.
            import threading
            threading.Thread(target=lambda: systemctl("restart", AP_SERVICE),
                             daemon=True).start()
        else:
            nmcli("con", "up", AP_CON)
            ensure_forwarding()                # the uplink is new — make sure it is NATed
    return {"ok": ok, "output": out, "note":
            "AP retunes to the uplink channel — reconnect the laptop if it dropped"}


def disconnect() -> dict:
    rc, out = nmcli("dev", "disconnect", STA_IFACE)
    # BCM43430 is a single radio: when wlan0 loses its uplink channel the AP on uap0
    # loses its channel too and stops being reachable. Restarting dji-ap makes ap.sh
    # pick a default channel so 10.42.0.1 answers again.
    if hostapd_mode():
        import threading
        threading.Thread(target=lambda: systemctl("restart", AP_SERVICE),
                         daemon=True).start()
    return {"ok": rc == 0, "output": out}


def have_internet() -> bool:
    rc, _ = run("ping", "-c", "1", "-W", "2", "1.1.1.1")
    return rc == 0


def status() -> dict:
    rc, devs = nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev")
    ap_up = uplink = None
    for line in devs.split("\n"):
        parts = _split_nmcli(line)
        if len(parts) < 4:
            continue
        dev, typ, state, con = parts[:4]
        # In hostapd mode uap0 is NM-unmanaged; its state comes from the unit below.
        if dev == AP_IFACE and not hostapd_mode():
            ap_up = {"iface": dev, "state": state, "connection": con,
                     "ssid": AP_SSID, "address": AP_ADDR}
        elif dev == STA_IFACE:
            uplink = {"iface": dev, "state": state, "connection": con}
    if hostapd_mode():
        _, act = systemctl("is-active", AP_SERVICE)
        ap_up = {"iface": AP_IFACE, "state": act.strip() or "unknown",
                 "connection": AP_SERVICE, "ssid": AP_SSID, "address": AP_ADDR,
                 "mode": "hostapd"}
    rc, addr = run("hostname", "-I")
    return {"ap": ap_up, "uplink": uplink, "internet": have_internet(),
            "addresses": addr.split(), "ap_ssid": AP_SSID, "ap_psk": AP_PSK}


# ---------------------------------------------------------------- HTTP API
class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The PC client is a local tool on the same link; no auth, no origin checks.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *a):
        print(f"[netctl] {self.address_string()} {fmt % a}", flush=True)

    def do_GET(self):
        if self.path.startswith("/status"):
            self._send(status())
        elif self.path.startswith("/scan"):
            self._send({"networks": scan()})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send({"error": "bad json"}, 400)
        if self.path.startswith("/connect"):
            ssid = body.get("ssid")
            if not ssid:
                return self._send({"error": "ssid required"}, 400)
            self._send(connect(ssid, body.get("psk")))
        elif self.path.startswith("/disconnect"):
            self._send(disconnect())
        elif self.path.startswith("/hotspot"):
            self._send(hotspot(bool(body.get("on", True))))
        else:
            self._send({"error": "not found"}, 404)


def ap_watchdog() -> None:
    """Restart dji-ap whenever the last client leaves.

    BCM43430 (Pi Zero 2 W) firmware gets stuck after a client disassociates:
    hostapd cannot accept new associations until the AP service is restarted.
    This watchdog polls the DHCP lease file; when it sees clients drop to zero
    it restarts dji-ap so the next connection attempt finds a clean AP.

    Only active in hostapd mode (dji-ap.service present). In the NM-fallback
    mode the chip does not exhibit this behaviour.
    """
    if not hostapd_mode():
        return
    LEASES = "/var/lib/misc/dnsmasq.leases"
    CHECK_S = 10      # poll interval while clients are present
    IDLE_S  = 20      # extra wait after last client leaves before restart
                      # (avoids a restart during a quick reconnect)
    had_clients = False
    idle_since: float | None = None

    while True:
        time.sleep(CHECK_S)
        try:
            leases = [l for l in open(LEASES).read().splitlines() if l.strip()]
        except OSError:
            leases = []

        if leases:
            had_clients = True
            idle_since = None
        elif had_clients:
            # Transition: had clients, now none.
            if idle_since is None:
                idle_since = time.monotonic()
                print("[netctl] watchdog: all clients left — will restart AP in "
                      f"{IDLE_S}s if no one reconnects", flush=True)
            elif time.monotonic() - idle_since >= IDLE_S:
                print("[netctl] watchdog: restarting dji-ap to clear BCM43430 state",
                      flush=True)
                systemctl("restart", AP_SERVICE)
                had_clients = False
                idle_since = None


def serve():
    hotspot(True)          # the AP is the whole point — bring it up on start
    import threading
    threading.Thread(target=ap_watchdog, daemon=True).start()
    srv = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[netctl] API on :{PORT}  (AP '{AP_SSID}' at {AP_ADDR})", flush=True)
    srv.serve_forever()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "scan":
        for n in scan():
            mark = "*" if n["in_use"] else " "
            print(f" {mark} {n['signal']:3d}%  {n['security']:12s} {n['ssid']}")
    elif cmd == "connect":
        if len(sys.argv) < 3:
            print("usage: netctl.py connect SSID [PASSWORD]")
            return 2
        print(json.dumps(connect(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None),
                         indent=2))
    elif cmd == "disconnect":
        print(json.dumps(disconnect(), indent=2))
    elif cmd == "hotspot":
        on = len(sys.argv) < 3 or sys.argv[2] != "off"
        print(json.dumps(hotspot(on), indent=2))
    elif cmd == "serve":
        serve()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
