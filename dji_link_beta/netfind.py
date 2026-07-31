#!/usr/bin/env python3
"""
netfind.py — locate the Pi jump-host from the PC.

Discovery only accepts a host as the Pi after the netctl /status endpoint returns the
DJI-Link status shape. An open TCP port is not enough: Windows boxes and stale services
can otherwise look like a Pi while the real board is off.
"""

from __future__ import annotations

import html
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

AP_PREFIX = "PI_DJI_LINK-"
AP_DEFAULT_PSK = "raspberry"
AP_GATEWAY = "10.42.0.1"
BRIDGE_PORT = 9910
NETCTL_PORT = 9911


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _netctl(host: str, path: str, body: dict | None = None, timeout: float = 8.0):
    url = f"http://{host}:{NETCTL_PORT}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _looks_like_pi_status(st: object) -> bool:
    if not isinstance(st, dict):
        return False
    if st.get("service") == "dji-link-netctl":
        return True
    ap = st.get("ap")
    uplink = st.get("uplink")
    return (
        ("internet" in st and isinstance(st.get("internet"), bool)) and
        isinstance(st.get("addresses"), list) and
        (isinstance(ap, dict) or ap is None) and
        (isinstance(uplink, dict) or uplink is None) and
        isinstance(st.get("ap_ssid"), str) and
        st.get("ap_ssid", "").startswith(AP_PREFIX)
    )


def pi_status(host: str) -> dict | None:
    try:
        st = _netctl(host, "/status", timeout=3.0)
    except Exception:
        st = None
    if _looks_like_pi_status(st):
        return st
    return pi_health(host)


def pi_health(host: str) -> dict | None:
    try:
        st = _netctl(host, "/healthz", timeout=1.0)
    except Exception:
        return None
    return st if isinstance(st, dict) and st.get("service") == "dji-link-netctl" else None


def is_pi_host(host: str) -> bool:
    return pi_health(host) is not None


def find_on_lan(saved_host: str | None = None) -> str | None:
    """Return the Pi address if a real netctl endpoint answers on the current network."""
    candidates: list[str] = []
    if saved_host:
        candidates.append(saved_host)
    candidates += ["raspberrypi.local", AP_GATEWAY]
    seen: set[str] = set()
    for host in candidates:
        try:
            ip = socket.gethostbyname(host)
        except OSError:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        if is_pi_host(ip):
            return ip
    return None


def _candidate_local_ipv4s() -> list[str]:
    ips: set[str] = set()

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass

    if sys.platform.startswith("win"):
        cmds = (["ipconfig"],)
    else:
        cmds = (["hostname", "-I"], ["ip", "-4", "-o", "addr"])
    for cmd in cmds:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout or ""
        except Exception:
            continue
        for m in re.finditer(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", out):
            ips.add(m.group(0))

    return sorted(ip for ip in ips
                  if not ip.startswith(("127.", "169.254."))
                  and ipaddress.ip_address(ip).is_private)


def sweep_lan(port: int = NETCTL_PORT) -> str | None:
    """Probe local /24 networks for a real netctl endpoint."""
    import concurrent.futures

    hosts: list[str] = []
    seen: set[str] = set()
    for my_ip in _candidate_local_ipv4s():
        base = my_ip.rsplit(".", 1)[0]
        for i in range(1, 255):
            host = f"{base}.{i}"
            if host != my_ip and host not in seen:
                hosts.append(host)
                seen.add(host)

    def probe(host: str) -> str | None:
        if not _port_open(host, port, 0.25):
            return None
        return host if is_pi_host(host) else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=96) as ex:
        for hit in ex.map(probe, hosts):
            if hit:
                return hit
    return None


# ---------------------------------------------------------------- Windows Wi-Fi
def _is_windows() -> bool:
    return sys.platform.startswith("win")


def scan_ap() -> list[str]:
    """SSIDs in range whose name marks them as a Pi AP."""
    if not _is_windows():
        return []
    try:
        out = (subprocess.run(["netsh", "wlan", "show", "networks", "mode=bssid"],
                              capture_output=True, text=True, timeout=15).stdout or "")
    except Exception:
        return []
    found: list[str] = []
    for m in re.finditer(r"^\s*SSID\s+\d+\s*:\s*(.+?)\s*$", out, re.M):
        ssid = m.group(1).strip()
        if ssid.startswith(AP_PREFIX) and ssid not in found:
            found.append(ssid)
    return found


def join_ap(ssid: str, psk: str = AP_DEFAULT_PSK) -> bool:
    """Explicitly rejoin a Pi AP on Windows and verify the Pi identity endpoint."""
    if not _is_windows():
        return False
    profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{html.escape(ssid)}</name>
  <SSIDConfig><SSID><name>{html.escape(ssid)}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>manual</connectionMode>
  <MSM><security>
    <authEncryption><authentication>WPA2PSK</authentication>
      <encryption>AES</encryption><useOneX>false</useOneX></authEncryption>
    <sharedKey><keyType>passPhrase</keyType>
      <protected>false</protected><keyMaterial>{html.escape(psk)}</keyMaterial></sharedKey>
  </security></MSM>
</WLANProfile>"""
    fd, path = tempfile.mkstemp(suffix=".xml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(profile)
        subprocess.run(["netsh", "wlan", "add", "profile", f"filename={path}"],
                       capture_output=True, text=True, timeout=15)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    # A channel retune can leave Windows claiming it is still connected while packets
    # use the dead association. Force a real state transition, then retry because the
    # Pi intentionally restarts hostapd shortly after replying to /connect.
    try:
        subprocess.run(["netsh", "wlan", "disconnect"], capture_output=True,
                       text=True, timeout=15)
        time.sleep(0.8)
        deadline = time.monotonic() + 45.0
        while time.monotonic() < deadline:
            subprocess.run(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"],
                           capture_output=True, text=True, timeout=15)
            for _ in range(4):
                time.sleep(0.5)
                if is_pi_host(AP_GATEWAY):
                    return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- Pi netctl API
def pi_scan_wifi(host: str) -> list[dict]:
    try:
        return _netctl(host, "/scan").get("networks", [])
    except Exception:
        return []


def pi_connect_wifi(host: str, ssid: str, psk: str) -> dict:
    via_ap = host == AP_GATEWAY
    before = pi_status(host) if via_ap else None
    pi_ap_ssid = (before or {}).get("ap_ssid", "")
    if via_ap and not pi_ap_ssid.startswith(AP_PREFIX):
        visible = scan_ap()
        if len(visible) == 1:
            pi_ap_ssid = visible[0]

    try:
        result = _netctl(host, "/connect", {"ssid": ssid, "psk": psk}, timeout=60)
    except Exception as e:
        # A successful radio retune can tear down this HTTP response. Reconnection and
        # /status below are authoritative in that case.
        result = {"ok": False, "output": f"connect response was interrupted: {e}"}

    explicit_failure = isinstance(result, dict) and result.get("ok") is False \
        and not str(result.get("output", "")).startswith("connect response was interrupted:")
    if not via_ap or explicit_failure or not _is_windows():
        return result
    if not pi_ap_ssid.startswith(AP_PREFIX):
        return {"ok": False, "output": ("Pi uplink may have connected, but the client could not "
                                         "identify the PI_DJI_LINK-* AP to rejoin")}

    time.sleep(1.0)  # let netctl's delayed hostapd restart begin
    if not join_ap(pi_ap_ssid, AP_DEFAULT_PSK):
        return {"ok": False, "output": (f"Pi uplink may have connected, but Windows could not "
                                         f"rejoin '{pi_ap_ssid}'")}

    after = pi_status(host) or {}
    uplink = after.get("uplink") or {}
    actual_ssid = after.get("uplink_ssid") or uplink.get("connection", "")
    profile_matches = actual_ssid == ssid or str(actual_ssid).endswith("-" + ssid)
    if profile_matches:
        result["ok"] = True
        result.setdefault("output", f"connected to '{ssid}'")
        result["ap_reconnected"] = True
    elif not result.get("ok"):
        result["output"] = (result.get("output", "connect failed") +
                            "; Pi AP reconnected, but the requested uplink is not active")
    return result


def discover(saved_host: str | None = None, allow_ap_join: bool = True) -> dict:
    """Full discovery result for the GUI/CLI."""
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
                st = pi_status(AP_GATEWAY) or {}
                has_net = bool(st.get("internet"))
                return {"host": AP_GATEWAY, "via": "ap", "joined_ap": ssid,
                        "needs_internet_prompt": not has_net}
    return {"host": None, "via": None, "joined_ap": None,
            "needs_internet_prompt": False}


def wait_for_pi(host: str = AP_GATEWAY, timeout_s: float = 45.0) -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = pi_status(host)
        if st:
            return st
        time.sleep(0.5)
    return None


def main() -> int:
    r = discover(sys.argv[1] if len(sys.argv) > 1 else None)
    print(r)
    if r["host"]:
        print("pi status:", pi_status(r["host"]))
    return 0 if r["host"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
