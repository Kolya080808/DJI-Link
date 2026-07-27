#!/usr/bin/env python3
"""
netfind.py — locate the Pi jump-host from the PC, following this flow:

  1. Is the Pi already reachable on the network I'm on? (mDNS raspberrypi.local, a saved
     host, or a quick sweep of the local /24). If so, use it and DO NOT touch Wi-Fi —
     the Pi is acting as a repeater and there is already internet.
  2. If not, is there a Pi access point in range (SSID starts with "PI_DJI_LINK-")? Join
     it with the default password, then the caller asks the user whether internet is
     needed; if yes, the caller tells the Pi (via netctl on :9911) to join a Wi-Fi.

Only the discovery/join primitives live here; the "ask the user" and "tell the Pi to go
online" decisions belong to the client UI. Wi-Fi control is Windows-only (netsh); on
other platforms discovery still works, only auto-join is skipped.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request

AP_PREFIX = "PI_DJI_LINK-"
AP_DEFAULT_PSK = "raspberry"
AP_GATEWAY = "10.42.0.1"        # the Pi's address on its own AP
BRIDGE_PORT = 9910
NETCTL_PORT = 9911


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_on_lan(saved_host: str | None = None) -> str | None:
    """Return the Pi's address if the netctl control port answers on the current network.

    Liveness is probed on NETCTL_PORT, not BRIDGE_PORT: netctl (Wi-Fi/AP API) is always
    up, but the bridge only opens :9910 once an RC/UDC is plugged in — which happens
    AFTER discovery — so keying off the bridge made a controller-less Pi look unreachable.
    """
    candidates = []
    if saved_host:
        candidates.append(saved_host)
    candidates += ["raspberrypi.local", AP_GATEWAY]
    for host in candidates:
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            continue
        if _port_open(ip, NETCTL_PORT):
            return ip
    return None


def sweep_lan(port: int = NETCTL_PORT) -> str | None:
    """Fallback: probe every host on our own /24 for the netctl control port, in parallel.
    Cheap on a home LAN, and it finds the Pi even when mDNS is blocked."""
    import concurrent.futures
    try:
        my_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        return None
    if my_ip.startswith("127."):
        return None
    base = my_ip.rsplit(".", 1)[0]
    hosts = [f"{base}.{i}" for i in range(1, 255)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as ex:
        for host, ok in zip(hosts, ex.map(lambda h: _port_open(h, port, 0.3), hosts)):
            if ok:
                return host
    return None


# ---------------------------------------------------------------- Windows Wi-Fi
def _is_windows() -> bool:
    return sys.platform.startswith("win")


def scan_ap() -> list[str]:
    """SSIDs in range whose name marks them as a Pi AP."""
    if not _is_windows():
        return []
    try:
        out = subprocess.run(["netsh", "wlan", "show", "networks"],
                             capture_output=True, text=True).stdout
    except FileNotFoundError:
        return []
    found = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("SSID") and ":" in line:
            ssid = line.split(":", 1)[1].strip()
            if ssid.startswith(AP_PREFIX):
                found.append(ssid)
    return found


def join_ap(ssid: str, psk: str = AP_DEFAULT_PSK) -> bool:
    """Join a Pi AP on Windows by generating a one-shot WLAN profile.

    netsh can only connect to a profile that already exists, so we add one first. The
    profile is named after the SSID and left in place for next time.
    """
    if not _is_windows():
        return False
    profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{ssid}</name>
  <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security>
    <authEncryption><authentication>WPA2PSK</authentication>
      <encryption>AES</encryption><useOneX>false</useOneX></authEncryption>
    <sharedKey><keyType>passPhrase</keyType>
      <protected>false</protected><keyMaterial>{psk}</keyMaterial></sharedKey>
  </security></MSM>
</WLANProfile>"""
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(profile)
        subprocess.run(["netsh", "wlan", "add", "profile", f"filename={path}"],
                       capture_output=True, text=True)
    finally:
        os.unlink(path)
    r = subprocess.run(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False
    # Wait for the interface to actually get the AP's gateway. Probe the always-up netctl
    # control port, not the bridge (which needs an RC/UDC plugged in only later).
    for _ in range(20):
        time.sleep(0.5)
        if _port_open(AP_GATEWAY, NETCTL_PORT):
            return True
    return False


# ---------------------------------------------------------------- Pi netctl API
def _netctl(host: str, path: str, body: dict | None = None, timeout: float = 8.0):
    url = f"http://{host}:{NETCTL_PORT}{path}"
    data = None
    if body is not None:
        import json
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        import json
        return json.loads(r.read())


def pi_status(host: str) -> dict | None:
    try:
        return _netctl(host, "/status")
    except Exception:
        return None


def pi_scan_wifi(host: str) -> list[dict]:
    try:
        return _netctl(host, "/scan").get("networks", [])
    except Exception:
        return []


def pi_connect_wifi(host: str, ssid: str, psk: str) -> dict:
    return _netctl(host, "/connect", {"ssid": ssid, "psk": psk}, timeout=30)


def discover(saved_host: str | None = None, allow_ap_join: bool = True) -> dict:
    """Full discovery. Returns:
        {"host": ip or None, "via": "lan"|"sweep"|"ap"|None,
         "joined_ap": ssid or None, "needs_internet_prompt": bool}
    needs_internet_prompt is True only when we had to join a Pi AP (case 2), i.e. the
    moment it makes sense to ask the user about internet. Reaching the Pi over an
    existing LAN never prompts.
    """
    host = find_on_lan(saved_host)
    if host:
        return {"host": host, "via": "lan", "joined_ap": None,
                "needs_internet_prompt": False}

    host = sweep_lan()
    if host:
        return {"host": host, "via": "sweep", "joined_ap": None,
                "needs_internet_prompt": False}

    if allow_ap_join:
        for ssid in scan_ap():
            if join_ap(ssid):
                # The Pi may already have an uplink (it was left connected to a Wi-Fi):
                # if it reports internet, everything already works — do not prompt.
                st = pi_status(AP_GATEWAY)
                has_net = bool(st and st.get("internet"))
                return {"host": AP_GATEWAY, "via": "ap", "joined_ap": ssid,
                        "needs_internet_prompt": not has_net}
    return {"host": None, "via": None, "joined_ap": None,
            "needs_internet_prompt": False}


def main() -> int:
    r = discover(sys.argv[1] if len(sys.argv) > 1 else None)
    print(r)
    if r["host"]:
        st = pi_status(r["host"])
        print("pi status:", st)
    return 0 if r["host"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
