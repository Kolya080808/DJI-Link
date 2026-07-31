#!/usr/bin/env python3
"""
netctl.py — Wi-Fi control for the Pi jump-host: access point + optional internet uplink.

Two jobs, and they are deliberately independent of each other:

  1. ALWAYS serve an access point the laptop can join. This is the control path to the
     Pi (10.42.0.1: netctl on :9911, the AOA bridge on :9910). It must survive every
     uplink change, every failed join and every reboot, because when it is down there
     is no way left to talk to the Pi in the field.
  2. Optionally join an existing Wi-Fi network as an uplink. AP clients are NATed out
     through it, so the laptop gets internet over the same association it uses to reach
     the Pi — but on a different route: the Pi itself is on-link at 10.42.0.1 and is
     never NATed, so it stays reachable whether or not the uplink exists.

The Pi Zero 2 W has ONE radio. A second virtual interface (uap0) carries the AP while
wlan0 stays the client. The AP itself is run by hostapd + dnsmasq (the dji-ap systemd
unit, see pi/ap.sh) rather than NetworkManager: NM's AP goes through wpa_supplicant,
which advertises WPS and makes Windows demand a PIN instead of the passphrase. hostapd
with wps_state=0 is a plain WPA2 network every OS joins with just the password. When
dji-ap is absent (a Pi not yet re-set-up) we fall back to NM's ipv4.method=shared AP.

The chip requires AP and client to share a channel, so joining an uplink can retune the
AP and clients then reconnect — hardware, not a bug. Every successful uplink join ends
with one delayed, clean AP restart. That gives Windows a predictable disconnect event;
the PC client then explicitly re-associates instead of relying on Windows auto-reconnect.
Disconnecting or failing to join an uplink still leaves a healthy AP untouched.

Usage (on the Pi):
    sudo python3 netctl.py status
    sudo python3 netctl.py doctor           # full diagnosis, run this first when stuck
    sudo python3 netctl.py scan
    sudo python3 netctl.py connect "MySSID" "password"
    sudo python3 netctl.py disconnect
    sudo python3 netctl.py hotspot on|off
    sudo python3 netctl.py serve            # HTTP API on :9911 for the PC client

HTTP API (used by pc_client's network panel):
    GET  /status            -> {"ap": {...}, "uplink": {...}, "internet": bool}
    GET  /scan              -> {"networks": [{"ssid","signal","security","in_use"}]}
    GET  /doctor            -> {"checks": [...], "ok": bool}
    POST /connect           <- {"ssid": "...", "psk": "..."}
    POST /disconnect
    POST /hotspot           <- {"on": true}
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AP_CON = "dji-link-ap"       # legacy NetworkManager AP profile (fallback path)
AP_SERVICE = "dji-ap"        # hostapd+dnsmasq AP unit created by setup_pi.sh (pi/ap.sh)
AP_UNIT_PATH = "/etc/systemd/system/dji-ap.service"
AP_IFACE = "uap0"
STA_IFACE = "wlan0"
AP_PSK = "raspberry"          # default; >= 8 chars for WPA2
AP_ADDR = "10.42.0.1"
PORT = 9911
HERE = os.path.dirname(os.path.abspath(__file__))
AP_SH = os.path.join(HERE, "ap.sh")
HOSTAPD_CONF = "/run/dji-ap/hostapd.conf"
# "the operator asked for the AP to be off" — the one thing that must stop the watchdog
# from putting it back. Under /run on purpose: a reboot clears it, so a hotspot switched
# off during an experiment can never turn into a Pi that comes up unreachable.
# Deliberately outside RuntimeDirectory=dji-ap: systemd removes that directory when the
# AP unit stops, which used to erase the operator's "hotspot off" request and let the
# watchdog turn it straight back on. /run still clears the request on the next reboot.
AP_OFF_FLAG = "/run/dji-link-hotspot-off"
UPLINK_PREFIX = "dji-uplink-"
AP_AUTO_RESTART_LIMIT = 3


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


def run(*args: str, check: bool = False, timeout: float | None = 90) -> tuple[int, str]:
    """Run a command, return (rc, output). Never raises unless check=True.

    Everything here is called from an HTTP handler, so a command that hangs (nmcli
    waiting on a supplicant that never answers) would otherwise pin a worker thread
    forever. A timeout turns that into an ordinary failure.
    """
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, f"{' '.join(args)}: timed out after {timeout}s"
    except OSError as e:
        return 127, f"{' '.join(args)}: {e}"
    out = (p.stdout + p.stderr).strip()
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} -> {out}")
    return p.returncode, out


def nmcli(*args: str, check: bool = False, timeout: float | None = 90) -> tuple[int, str]:
    return run("nmcli", *args, check=check, timeout=timeout)


def systemctl(*args: str) -> tuple[int, str]:
    return run("systemctl", *args, timeout=60)


def ap_sh(*args: str) -> tuple[int, str]:
    return run("bash", AP_SH, *args, timeout=60)


_hostapd_mode: bool | None = None


def hostapd_mode() -> bool:
    """True when the hostapd AP unit (dji-ap.service) is installed — the normal path on a
    Pi set up by the current setup_pi.sh. False on an older Pi, or once the unit file is
    removed, where we fall back to the NetworkManager ipv4.method=shared AP so nothing
    regresses. Detected by the unit file itself so the switch is unambiguous."""
    global _hostapd_mode
    if _hostapd_mode is None:
        _hostapd_mode = os.path.exists(AP_UNIT_PATH) and os.path.exists(AP_SH)
    return _hostapd_mode


def _split_nmcli(line: str) -> list[str]:
    """Split an nmcli -t line on unescaped ':' (nmcli escapes a literal one as '\\:')."""
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


def nmcli_get(field: str, *args: str) -> str:
    """One property value. `-g` prints the bare value; older nmcli needs `-t -f` and
    the 'field:value' line taken apart."""
    rc, out = nmcli("-g", field, *args, timeout=30)
    if rc == 0:
        return out.strip()
    rc, out = nmcli("-t", "-f", field, *args, timeout=30)
    if rc != 0:
        return ""
    for line in out.splitlines():
        parts = _split_nmcli(line)
        if len(parts) >= 2 and parts[0].lower() == field.lower():
            return ":".join(parts[1:]).strip()
    return ""


# ---------------------------------------------------------------- AP (hostapd path)
def ap_unit_state() -> str:
    return systemctl("is-active", AP_SERVICE)[1].strip()


def ap_active() -> bool:
    return ap_unit_state() == "active"


def ap_recovering() -> bool:
    return ap_unit_state() in ("activating", "reloading")


def ap_should_run() -> bool:
    return not os.path.exists(AP_OFF_FLAG)


def _set_ap_off_flag(off: bool) -> None:
    try:
        if off:
            os.makedirs(os.path.dirname(AP_OFF_FLAG), exist_ok=True)
            with open(AP_OFF_FLAG, "w") as f:
                f.write("hotspot turned off through the API\n")
        elif os.path.exists(AP_OFF_FLAG):
            os.remove(AP_OFF_FLAG)
    except OSError as e:
        print(f"[netctl] could not update {AP_OFF_FLAG}: {e}", flush=True)


def ap_health() -> tuple[bool, str]:
    """(healthy, reason). Covers the whole AP, not just 'is the process alive': the
    interface, its address, hostapd, dnsmasq and the NAT rule."""
    if not hostapd_mode():
        return True, "nm-fallback"
    if not ap_should_run():
        return True, "turned off on request"
    if not ap_active():
        return False, f"{AP_SERVICE} is not active"
    rc, out = ap_sh("health")
    return rc == 0, out or ("ok" if rc == 0 else "unhealthy")


def ap_conf_channel() -> str:
    """The channel hostapd was last started with."""
    try:
        with open(HOSTAPD_CONF) as f:
            for line in f:
                if line.startswith("channel="):
                    return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return ""


def ap_wanted_channel() -> str:
    """The channel ap.sh would pick right now — i.e. after the uplink changed."""
    rc, out = ap_sh("chan")
    if rc != 0:
        return ""
    # "<hw_mode> <channel>". Taking the last token rather than index 1 so a stray line
    # on the way through cannot silently turn into a channel number.
    parts = out.split()
    return parts[-1] if parts and parts[-1].isdigit() else ""


def live_uplink_channel() -> str:
    """The channel of a fully associated wlan0, never an AP fallback channel.

    NetworkManager can keep GENERAL.STATE at 100 while wpa_supplicant briefly moves
    through disconnected/scanning/associating. During that window `ap.sh chan` returns
    its no-uplink fallback (normally channel 6), which the watchdog used to mistake for
    a real channel change and restart the otherwise healthy field AP. `iw link` is the
    authoritative extra check: it only contains an SSID/frequency while STA is actually
    associated.
    """
    state = nmcli_get("GENERAL.STATE", "dev", "show", STA_IFACE)
    if not re.match(r"^(?:100\b|connected\b)", state, re.I):
        return ""
    rc, out = run("iw", "dev", STA_IFACE, "link", timeout=15)
    if rc != 0 or not re.search(r"^Connected to\s", out, re.M):
        return ""
    if not re.search(r"^\s*SSID:\s*\S", out, re.M):
        return ""
    m = re.search(r"^\s*freq:\s*(\d+)(?:\.\d+)?\s*$", out, re.M)
    if not m:
        return ""
    freq = int(m.group(1))
    if 2412 <= freq <= 2472:
        return str((freq - 2407) // 5)
    if freq == 2484:
        return "14"
    if 5000 <= freq < 5950:
        return str((freq - 5000) // 5)
    if 5955 <= freq <= 7115:
        return str((freq - 5950) // 5)
    return ""


def confirmed_uplink_channel(delay_s: float = 2.0) -> str:
    """A live uplink channel that stayed unchanged across two observations."""
    first = live_uplink_channel()
    if not first:
        return ""
    time.sleep(delay_s)
    second = live_uplink_channel()
    return first if second == first else ""


def ap_live_channel() -> str:
    rc, out = run("iw", "dev", AP_IFACE, "info", timeout=15)
    m = re.search(r"channel\s+(\d+)", out) if rc == 0 else None
    return m.group(1) if m else ""


def ap_clients() -> int:
    rc, out = run("iw", "dev", AP_IFACE, "station", "dump", timeout=15)
    return out.count("Station ") if rc == 0 else 0


def ap_failures() -> int:
    """Consecutive short hostapd runs recorded by ap.sh."""
    rc, out = ap_sh("failures")
    if rc != 0:
        return 0
    try:
        return max(0, int(out.strip()))
    except ValueError:
        return 0


def reset_ap_failures() -> None:
    ap_sh("reset-failures")


def _restart_ap_async(reason: str, delay: float = 0.7) -> None:
    """Restart dji-ap off the request thread.

    `systemctl restart` takes the AP down before it returns, tearing down the very TCP
    connection this reply has to travel over: without the thread the PC client always
    sees "the Pi did not answer", even on a successful join. The small delay lets the
    response flush first.
    """
    def work() -> None:
        time.sleep(delay)
        print(f"[netctl] restarting {AP_SERVICE}: {reason}", flush=True)
        systemctl("restart", AP_SERVICE)
    threading.Thread(target=work, daemon=True).start()


def ensure_ap(reason: str = "") -> None:
    """Bring the AP back if anything about it is wrong. Never lets a failed uplink
    operation leave the Pi with no way in."""
    if not hostapd_mode():
        return
    ok, why = ap_health()
    if ok:
        return
    if ap_recovering():
        return
    failures = ap_failures()
    if failures >= AP_AUTO_RESTART_LIMIT:
        print(f"[netctl] AP unhealthy ({why}); watchdog restart suppressed after "
              f"{failures} short failures — systemd recovery remains active", flush=True)
        return
    print(f"[netctl] AP unhealthy ({why}); restarting{' — ' + reason if reason else ''}",
          flush=True)
    _restart_ap_async(why or reason)


# ---------------------------------------------------------------- AP (NM fallback)
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
        if on:
            _set_ap_off_flag(False)
            # This endpoint is an explicit operator request, unlike the watchdog. Clear
            # the diagnostic latch and request an immediate attempt; systemd continues
            # low-rate attempts if the firmware is still settling.
            reset_ap_failures()
            systemctl("reset-failed", AP_SERVICE)
            systemctl("start", AP_SERVICE)
            for _ in range(10):             # the unit is Type=simple; give hostapd a moment
                ok, why = ap_health()
                if ok:
                    return {"ok": True, "output": "ap up", "mode": "hostapd"}
                time.sleep(1)
            ok, why = ap_health()
            return {"ok": ok, "output": why, "mode": "hostapd"}
        # Flag first: the watchdog would otherwise see a stopped AP and put it back.
        _set_ap_off_flag(True)
        rc, out = systemctl("stop", AP_SERVICE)
        ap_sh("down")                       # explicit off: silence uap0 and remove NAT
        return {"ok": rc == 0, "output": out, "mode": "hostapd",
                "note": "the AP stays off until /hotspot on, or until the next reboot"}
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


# ---------------------------------------------------------------- scan
def _wifi_radio_on() -> None:
    """Undo every way a radio can be off. A Pi that was rebooted mid-experiment can come
    up soft-blocked or with wlan0 left unmanaged, and then every connect fails with a
    message that says nothing about the real cause."""
    run("rfkill", "unblock", "wifi", timeout=15)
    nmcli("radio", "wifi", "on", timeout=30)
    nmcli("dev", "set", STA_IFACE, "managed", "yes", timeout=30)


def _scan_rows() -> list[list[str]]:
    """Raw scan rows [in_use, ssid, signal, security, chan] from the client interface.

    Rescan on wlan0 only — the AP interface must not leave its channel or connected
    laptops drop. `--rescan yes` blocks until the scan finishes; on an nmcli too old for
    it, fall back to an explicit rescan plus a wait.
    """
    fields = "IN-USE,SSID,SIGNAL,SECURITY,CHAN"
    rc, out = nmcli("-t", "-f", fields, "dev", "wifi", "list",
                    "--rescan", "yes", "ifname", STA_IFACE, timeout=60)
    if rc != 0:
        nmcli("dev", "wifi", "rescan", "ifname", STA_IFACE, timeout=45)
        time.sleep(2)
        rc, out = nmcli("-t", "-f", fields, "dev", "wifi", "list", "ifname", STA_IFACE)
    rows = []
    if rc != 0:
        return rows
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = _split_nmcli(line)
        if len(parts) >= 5:
            rows.append(parts[:5])
    return rows


def scan() -> list[dict]:
    """Visible networks, strongest first."""
    nets: dict[str, dict] = {}
    for in_use, ssid, signal, sec, _chan in _scan_rows():
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


def _scan_entry(ssid: str) -> dict | None:
    """The strongest scan row for `ssid`, or None when it is not in range/not broadcast."""
    best: dict | None = None
    for in_use, s, signal, sec, chan in _scan_rows():
        if s != ssid:
            continue
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        if best is None or sig > best["signal"]:
            best = {"signal": sig, "security": sec or "", "chan": chan}
    return best


# ---------------------------------------------------------------- saved profiles
class Profile:
    __slots__ = ("uuid", "name", "ssid", "filename", "iface")

    def __init__(self, uuid: str, name: str, ssid: str, filename: str, iface: str):
        self.uuid, self.name, self.ssid = uuid, name, ssid
        self.filename, self.iface = filename, iface

    def is_ap(self) -> bool:
        """Our own access point, under any of the names it can have. Deleting it would
        take the Pi off the air, so it is excluded from every cleanup path."""
        return self.iface == AP_IFACE or self.name == AP_CON or self.ssid == AP_SSID


def wifi_profiles() -> list[Profile]:
    """Every saved Wi-Fi profile.

    Two steps on purpose. Setting properties like 802-11-wireless.ssid are NOT valid
    fields for the `con show` *list* — nmcli rejects them with rc=2 and prints
    "invalid field", which is easy to mistake for "no profiles matched". The SSID can
    only be read per profile, so list first (UUID/NAME/TYPE/FILENAME are all valid list
    fields), then query each Wi-Fi profile by UUID.
    """
    out_list: list[Profile] = []
    rc, out = nmcli("-t", "-f", "UUID,NAME,TYPE,FILENAME", "con", "show")
    if rc != 0:
        print(f"[netctl] could not list connections: {out}")
        return out_list
    for line in out.splitlines():
        parts = _split_nmcli(line)
        if len(parts) < 4:
            continue
        uuid, name, typ, fname = parts[0], parts[1], parts[2], parts[3]
        if "wireless" not in typ and "wifi" not in typ:
            continue
        out_list.append(Profile(uuid, name,
                                nmcli_get("802-11-wireless.ssid", "con", "show", "uuid", uuid),
                                fname,
                                nmcli_get("connection.interface-name", "con", "show",
                                          "uuid", uuid)))
    return out_list


def active_uplink() -> Profile | None:
    """The profile currently active on wlan0, so a failed join can put it back."""
    rc, out = nmcli("-t", "-f", "NAME,UUID,DEVICE", "con", "show", "--active")
    if rc != 0:
        return None
    for line in out.splitlines():
        parts = _split_nmcli(line)
        if len(parts) >= 3 and parts[2] == STA_IFACE:
            return Profile(parts[1], parts[0], "", "", STA_IFACE)
    return None


def _staging_name(ssid: str) -> str:
    """The name the new profile is built and tested under.

    Deliberately NOT the SSID: a profile named after the SSID is very likely the one
    the Pi is using right now, and creating ours from scratch starts by deleting the
    name it is going to take. Building under a staging name means nothing that works
    is destroyed before the replacement has proved itself; on success the profile is
    renamed to the SSID (see connect()), which is what nmcli's own `dev wifi connect`
    would have called it and what the PC client shows as the uplink.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]", "_", ssid)[:48] or "net"
    return UPLINK_PREFIX + slug


def _uplink_ssid() -> str:
    """The SSID wlan0 is actually associated with, straight from the driver — the NM
    profile name is only a label and need not match."""
    rc, out = run("iw", "dev", STA_IFACE, "link", timeout=15)
    m = re.search(r"^\s*SSID:\s*(.+)$", out, re.M) if rc == 0 else None
    return m.group(1).strip() if m else ""


def _uplink_ip() -> str:
    rc, out = run("ip", "-4", "-o", "addr", "show", "dev", STA_IFACE, timeout=15)
    m = re.search(r"inet\s+(\S+)", out) if rc == 0 else None
    return m.group(1) if m else ""


def _security_args(entry: dict | None, psk: str | None) -> list[str] | None:
    """The nmcli properties for this network's security, or None if we cannot do it.

    THE point of this function: `key-mgmt` is set here, explicitly, always. Relying on
    `nmcli dev wifi connect <ssid> password <psk>` instead is what produced
    "802-11-wireless-security.key-mgmt: property is missing" — that command hands NM a
    profile carrying only a PSK and expects the daemon to infer key-mgmt from the scan
    entry, which fails whenever the AP is not in the scan cache at that instant (right
    after a disconnect, a re-join, a hidden SSID) and has regressed outright in several
    NetworkManager releases. A profile that states its own key-mgmt has nothing to infer.
    """
    sec = (entry or {}).get("security", "").upper()
    if sec in ("--", "OPEN", "NONE"):
        sec = ""
    if "802.1X" in sec or "EAP" in sec:
        return None                       # WPA-Enterprise: needs credentials we do not have
    if "OWE" in sec:
        return ["802-11-wireless-security.key-mgmt", "owe"]   # "enhanced open", no password
    # An open network stays open even if a password was typed in: attaching a security
    # section to it makes the association fail outright, which reads as "wrong password"
    # on a network that has none.
    if entry is not None and not sec:
        return []
    if not psk:
        return []                         # open network (or a psk-less retry)
    if "WEP" in sec and "WPA" not in sec:
        return ["802-11-wireless-security.key-mgmt", "none",
                "802-11-wireless-security.wep-key0", psk,
                "802-11-wireless-security.wep-key-flags", "0"]
    # 'wpa-psk' covers WPA2 and WPA2/WPA3-transition APs. Only a WPA3-ONLY AP needs
    # 'sae', and it rejects wpa-psk. An unknown/absent scan entry means a hidden network,
    # where wpa-psk is overwhelmingly the right guess.
    if "WPA3" in sec and "WPA2" not in sec and "WPA1" not in sec:
        return ["802-11-wireless-security.key-mgmt", "sae",
                "802-11-wireless-security.psk", psk,
                # 0 = system-owned. The default (agent-owned) makes NM wait for a secret
                # agent that does not exist on a headless Pi and fail with
                # "(7) Secrets were required, but not provided".
                "802-11-wireless-security.psk-flags", "0",
                "802-11-wireless-security.pmf", "2"]
    return ["802-11-wireless-security.key-mgmt", "wpa-psk",
            "802-11-wireless-security.psk", psk,
            "802-11-wireless-security.psk-flags", "0"]


def _build_profile(name: str, ssid: str, psk: str | None,
                   entry: dict | None) -> tuple[bool, str]:
    """Create the uplink profile from scratch, fully specified, in ONE nmcli call.

    From scratch because a profile that already exists may carry exactly the half-filled
    security section we are trying to get away from. In one call because setting
    key-mgmt in a separate `con modify` from the properties it depends on can itself
    fail validation.
    """
    sec = _security_args(entry, psk)
    if sec is None:
        return False, (f"'{ssid}' is a WPA-Enterprise (802.1X) network; "
                       "netctl can only join personal WPA/WPA2/WPA3 networks")
    nmcli("con", "delete", "id", name)     # ignore rc: usually "unknown connection"
    args = ["con", "add", "type", "wifi",
            "con-name", name,
            "ifname", STA_IFACE,
            "ssid", ssid,
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", "5",
            "802-11-wireless.mode", "infrastructure",
            # Keep the station's hardware MAC: uap0's address is derived from it, and a
            # randomised one also breaks DHCP reservations and some routers' ACLs.
            "802-11-wireless.cloned-mac-address", "permanent",
            # 2 = disable. Wi-Fi power save is a documented cause of a Raspberry Pi
            # becoming unreachable minutes after it connects, and it makes the brcmfmac
            # AP+STA combination markedly less stable.
            "802-11-wireless.powersave", "2",
            "ipv4.method", "auto",
            # Without this, activation "succeeds" on a network where DHCP never answered
            # as long as IPv6 came up — and then the AP is NATed to nowhere.
            "ipv4.may-fail", "no",
            "ipv6.method", "auto",
            "ipv6.may-fail", "yes"]
    if entry is None:
        # Not in the scan: either out of range, or the SSID is not broadcast. Marking it
        # hidden makes NM probe for it by name, which is the only way to join the latter.
        args += ["802-11-wireless.hidden", "yes"]
    args += sec
    rc, out = nmcli(*args)
    return rc == 0, out


def _finish_ap_for_uplink(force_reconnect: bool = False) -> str:
    """Leave the access point in the right state after the uplink changed; return the
    note the PC client shows the user.

    A successful join deliberately requests one clean restart even when the channel did
    not change. brcmfmac can retune the virtual AP without making Windows notice that its
    old association is unusable; an explicit AP cycle plus the PC-side reconnect avoids
    that half-connected state. Failed joins and uplink disconnects do not force a cycle.
    """
    run("iw", "dev", STA_IFACE, "set", "power_save", "off", timeout=15)
    if not hostapd_mode():
        nmcli("con", "up", AP_CON)
        ensure_forwarding()                # the uplink is new — make sure it is NATed
        return "AP re-applied (NetworkManager fallback)"
    healthy, why = ap_health()
    if not healthy:
        reset_ap_failures()
        systemctl("reset-failed", AP_SERVICE)
        _restart_ap_async(why)
        return "AP is restarting — reconnect the laptop if it dropped"
    wanted, current = ap_wanted_channel(), ap_conf_channel()
    if wanted and current and wanted != current:
        reset_ap_failures()
        systemctl("reset-failed", AP_SERVICE)
        _restart_ap_async(f"channel {current} -> {wanted}")
        return "AP retunes to the uplink channel — reconnect the laptop if it dropped"
    if force_reconnect:
        reset_ap_failures()
        systemctl("reset-failed", AP_SERVICE)
        _restart_ap_async("uplink connected; refreshing AP for client reassociation")
        return "AP is refreshing — the PC client will reconnect to it"
    return "AP unchanged — the laptop stays connected"


def connect(ssid: str, psk: str | None) -> dict:
    """Join a network as the uplink, keeping the AP up.

    Contract: whatever happens, this call never leaves the Pi worse off than it found
    it. The AP is up when it returns, and if the join fails the connection that was
    active before is put back — the previous version deleted the saved profiles up
    front, so a failed join could strand a Pi with no network at all and no way to
    reach it from either side.
    """
    if ssid == AP_SSID:
        return {"ok": False, "output": f"'{ssid}' is this Pi's own access point"}

    _wifi_radio_on()
    prev = active_uplink()
    entry = _scan_entry(ssid)
    name = _staging_name(ssid)

    # No password given for a network that needs one: this is "reconnect to something I
    # already know", not "join with an empty password". Use the saved secret rather than
    # replacing the profile with a passwordless one that cannot possibly associate.
    secured = bool((entry or {}).get("security", "").strip(" -"))
    if not psk and secured:
        for p in wifi_profiles():
            if p.ssid != ssid or p.is_ap():
                continue
            rc, out = nmcli("--wait", "45", "con", "up", "uuid", p.uuid)
            if rc == 0:
                ap_note = _finish_ap_for_uplink(True)
                return {"ok": True, "output": out,
                        "note": (f"reconnected using the saved password for '{ssid}'; "
                                 f"{ap_note}")}
        ensure_ap("reconnect without a password failed")
        return {"ok": False,
                "output": f"'{ssid}' needs a password and none is saved for it"}

    ok, out = _build_profile(name, ssid, psk, entry)
    if not ok:
        ensure_ap("profile creation failed")
        return {"ok": False, "output": f"could not create the profile for '{ssid}': {out}"}

    # Other saved profiles for the same SSID would win the next autoconnect race with a
    # stale password. Park them instead of deleting them: if this join fails they are
    # the Pi's way back onto the network, and that is exactly when we must not have
    # thrown them away.
    others = [p for p in wifi_profiles()
              if p.ssid == ssid and p.name != name and not p.is_ap()]
    for p in others:
        nmcli("con", "modify", "uuid", p.uuid, "connection.autoconnect", "no")

    rc, out = nmcli("--wait", "45", "con", "up", "id", name)
    ok = rc == 0

    if not ok and hostapd_mode() and ap_active() and entry:
        # One radio: associating on a channel the AP is not on is the fragile case on
        # brcmfmac. Give the station the radio to itself for the retry — the AP comes
        # back either way, and the PC client already re-probes after a join.
        target = entry.get("chan", "")
        if target and target != ap_conf_channel():
            print(f"[netctl] retrying the join with the AP stopped "
                  f"(uplink is on channel {target}, AP on {ap_conf_channel() or '?'})",
                  flush=True)
            systemctl("stop", AP_SERVICE)
            time.sleep(1)
            rc, out2 = nmcli("--wait", "45", "con", "up", "id", name)
            ok = rc == 0
            out = out2 if ok else f"{out}\nretry without the AP: {out2}"
            systemctl("start", AP_SERVICE)

    if ok:
        for p in others:                   # proven redundant now, and only now
            nmcli("con", "delete", "uuid", p.uuid)
            print(f"[netctl] removed duplicate profile '{p.name}' for '{ssid}'", flush=True)
        # The duplicates are gone, so the SSID is free as a name. Take it: the PC client
        # shows the uplink by profile name and matches it against the SSID it asked for.
        if nmcli("con", "modify", "id", name, "connection.id", ssid)[0] == 0:
            name = ssid
    else:
        nmcli("con", "delete", "id", name)
        for p in others:                   # undo the parking
            nmcli("con", "modify", "uuid", p.uuid, "connection.autoconnect", "yes")
        if prev:                           # put the Pi back where it was
            nmcli("--wait", "30", "con", "up", "uuid", prev.uuid)
        if "Secrets were required" in out or "no secrets" in out.lower():
            out += "  (wrong password?)"

    return {"ok": ok, "output": out, "note": _finish_ap_for_uplink(ok)}


def disconnect() -> dict:
    rc, out = nmcli("--wait", "30", "dev", "disconnect", STA_IFACE)
    # Deliberately NOT restarting the AP here. The old code always did, which dropped
    # every laptop on the Pi's own network the moment the uplink went away — the exact
    # opposite of what the AP is for. With the uplink gone the radio is free and hostapd
    # keeps beaconing on its channel, so the only reason to restart is that the AP is
    # actually broken.
    note = "uplink down; the access point is unchanged"
    if hostapd_mode():
        healthy, why = ap_health()
        if not healthy:
            _restart_ap_async(why)
            note = f"uplink down; the access point is restarting ({why})"
    return {"ok": rc == 0, "output": out, "note": note}


# ---------------------------------------------------------------- status
_internet_cache: tuple[float, bool] = (0.0, False)
_internet_lock = threading.Lock()


def have_internet() -> bool:
    """Return the last probe result immediately; never block the local control API."""
    with _internet_lock:
        return _internet_cache[1]


def refresh_internet() -> bool:
    """Probe the optional uplink and cache the result for future /status calls."""
    global _internet_cache
    rc, _ = run("ping", "-c", "1", "-W", "1", "-I", STA_IFACE, "1.1.1.1", timeout=4)
    with _internet_lock:
        _internet_cache = (time.monotonic(), rc == 0)
    return rc == 0


def internet_monitor() -> None:
    while True:
        refresh_internet()
        time.sleep(5)


def healthz() -> dict:
    """Command-free Pi identity used for discovery when the uplink is offline."""
    return {"ok": True, "service": "dji-link-netctl", "address": AP_ADDR,
            "ap_ssid": AP_SSID}


def status() -> dict:
    rc, devs = nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", timeout=30)
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
    rc, addr = run("hostname", "-I", timeout=15)
    healthy, why = ap_health()
    # Key order matters: the C++ client reads "state"/"connection" relative to the "ap"
    # and "uplink" keys, so those two objects stay first and keep their shape. Anything
    # new is appended.
    return {"ap": ap_up, "uplink": uplink, "internet": have_internet(),
            "addresses": addr.split(), "ap_ssid": AP_SSID, "ap_psk": AP_PSK,
            "ap_healthy": healthy, "ap_detail": why,
            "ap_channel": ap_live_channel() or ap_conf_channel(),
            "ap_clients": ap_clients() if hostapd_mode() else 0,
            "ap_failures": ap_failures() if hostapd_mode() else 0,
            "uplink_ssid": _uplink_ssid(), "uplink_ip": _uplink_ip(),
            "service": "dji-link-netctl"}


def doctor() -> dict:
    """Everything needed to tell where the network went, in one place."""
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail.strip()[:600]})

    add("hostapd mode", hostapd_mode(),
        f"{AP_UNIT_PATH} {'present' if os.path.exists(AP_UNIT_PATH) else 'missing'}")
    _, u = systemctl("is-active", AP_SERVICE)
    add(f"{AP_SERVICE}.service", u.strip() == "active", u)
    ok, why = ap_health()
    add("ap health", ok, why)
    rc, out = run("iw", "dev")
    add(f"{AP_IFACE} exists", f"Interface {AP_IFACE}" in out, out)
    rc, out = run("ip", "-4", "addr", "show", "dev", AP_IFACE)
    add(f"{AP_IFACE} address", AP_ADDR in out, out)
    try:
        with open(HOSTAPD_CONF) as f:
            conf = f.read()
    except OSError as e:
        conf = str(e)
    add("hostapd.conf", "channel=" in conf, conf)
    rc, out = ap_sh("chan")
    add("channel ap.sh would pick now", rc == 0, out)
    rc, out = run("iw", "reg", "get")
    add("regulatory domain", rc == 0, out.split("\n")[0] if rc == 0 else out)
    rc, out = nmcli("-t", "-f", "DEVICE,STATE,CONNECTION", "dev")
    add("NetworkManager devices", rc == 0, out)
    profs = wifi_profiles()
    add("saved Wi-Fi profiles", True,
        ", ".join(f"{p.name}[{p.ssid or '?'}]" for p in profs) or "none")
    rc, out = run("iptables", "-t", "nat", "-S", "POSTROUTING")
    add("NAT", "MASQUERADE" in out, out)
    rc, out = run("sysctl", "-n", "net.ipv4.ip_forward")
    add("ip_forward", out.strip() == "1", out)
    add("internet", refresh_internet(), "ping 1.1.1.1 through wlan0")
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


# ---------------------------------------------------------------- HTTP API
class Handler(BaseHTTPRequestHandler):
    server_version = "netctl/0.8.2"

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
        if self.path.startswith("/healthz"):
            self._send(healthz())
        elif self.path.startswith("/status"):
            self._send(status())
        elif self.path.startswith("/scan"):
            self._send({"networks": scan()})
        elif self.path.startswith("/doctor"):
            self._send(doctor())
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
    """Keep the access point alive, whatever else happens.

    This is the last line of defence for the one thing that must never stay broken: if
    hostapd died, if uap0 lost its address, if dnsmasq is gone or the NAT rule was
    flushed, put it back. Restarts are backed off so a genuinely unstartable AP does not
    turn into a second restart loop on top of systemd. The service itself retries at a
    low rate without deleting uap0 or disconnecting wlan0.

    A healthy AP is never restarted merely because it has no clients or no uplink.
    """
    if not hostapd_mode():
        return
    CHECK_S = 15
    backoff, last_fix = 30.0, 0.0
    failure_latched = False

    while True:
        time.sleep(CHECK_S)
        now = time.monotonic()
        healthy, why = ap_health()
        if not healthy:
            # `Restart=always` reports activating during its retry delay and while
            # ap.sh pre waits for NetworkManager. A watchdog restart here kills that
            # valid attempt halfway through and resets the delay.
            if ap_recovering():
                continue
            failures = ap_failures()
            if failures >= AP_AUTO_RESTART_LIMIT:
                if not failure_latched:
                    print(f"[netctl] watchdog: AP remains unhealthy ({why}); "
                          f"{failures} short failures; systemd continues low-rate recovery",
                          flush=True)
                    failure_latched = True
                continue
            if now - last_fix < backoff:
                continue
            last_fix = now
            backoff = min(backoff * 2, 300.0)
            print(f"[netctl] watchdog: AP unhealthy ({why}); restarting {AP_SERVICE}",
                  flush=True)
            systemctl("restart", AP_SERVICE)
            continue
        failure_latched = False
        backoff = 30.0

        # Retune only for a real, fully associated uplink whose channel stayed stable.
        # With no uplink (the normal field case), leave the healthy AP untouched: it
        # keeps beaconing, serving DHCP and exposing the Pi at 10.42.0.1.
        wanted, current = confirmed_uplink_channel(), ap_conf_channel()
        if wanted and current and wanted != current and now - last_fix >= backoff:
            last_fix = now
            print(f"[netctl] watchdog: retuning {AP_SERVICE} "
                  f"for uplink channel {current} -> {wanted}", flush=True)
            systemctl("restart", AP_SERVICE)
            continue

def serve():
    _wifi_radio_on()
    # systemd starts and persistently recovers dji-ap independently. Do not turn netctl
    # startup into a second supervisor that resets its retry delay after a broken boot.
    if not hostapd_mode():
        hotspot(True)
    threading.Thread(target=ap_watchdog, daemon=True).start()
    threading.Thread(target=internet_monitor, daemon=True).start()
    # Threaded: /scan takes seconds and /connect tens of seconds. On the old
    # single-threaded server either one blocked /status, and the PC client read that as
    # "the Pi stopped answering" in the middle of a perfectly good operation.
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"[netctl] API on :{PORT}  (AP '{AP_SSID}' at {AP_ADDR})", flush=True)
    srv.serve_forever()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "status":
        print(json.dumps(status(), indent=2))
    elif cmd == "doctor":
        d = doctor()
        for c in d["checks"]:
            print(f"[{'ok ' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}")
        print("\nall good" if d["ok"] else "\nsee the FAIL lines above")
        return 0 if d["ok"] else 1
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
