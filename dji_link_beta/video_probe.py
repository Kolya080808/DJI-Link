#!/usr/bin/env python3
"""
video_probe.py — find the DRONE's adapter and its open ports (FTP/HTTP/config).

Problem: the PC has several networks (home + possibly WSL/Hyper-V), and the drone is on
its OWN USB adapter (RNDIS). The script enumerates all adapters, identifies the
"drone" ones (private IP, NO gateway — point-to-point), and scans only their subnets
(+ the known DJI addresses 192.168.2.1 / 192.168.42.x), bound to the interface.

TCP-connect only. Run (Windows):  py -3 video_probe.py
"""

from __future__ import annotations
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor

PORTS = {21: "FTP", 23: "FTP/telnet", 80: "HTTP", 443: "HTTPS", 554: "RTSP",
         8080: "HTTP-alt", 8000: "HTTP/stream", 37777: "camera"}
LIVENESS = [80, 21, 23, 8080, 554]
# known DJI addresses from reverse-engineering (drone config/ftp)
DJI_KNOWN = ["192.168.2.1", "192.168.42.1", "192.168.42.2", "192.168.42.3"]


def adapters():
    """List of (alias, ip, gateway|'') via PowerShell Get-NetIPConfiguration."""
    ps = ("Get-NetIPConfiguration | ForEach-Object { "
          "$_.InterfaceAlias + '|' + ($_.IPv4Address.IPAddress -join ',') + '|' + "
          "($_.IPv4DefaultGateway.NextHop -join ',') }")
    try:
        out = subprocess.check_output(["powershell", "-NoProfile", "-Command", ps],
                                      text=True, timeout=15, stderr=subprocess.DEVNULL)
    except Exception:
        return []
    res = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[1].strip():
            res.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return res


def tcp_open(host, port, src=None, timeout=0.4) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if src:
            try: s.bind((src, 0))
            except Exception: pass
        s.settimeout(timeout)
        ok = s.connect_ex((host, port)) == 0
        s.close()
        return ok
    except Exception:
        return False


def scan_subnet(base, src, my_last):
    hosts = [f"{base}.{i}" for i in range(1, 255) if str(i) not in my_last]
    with ThreadPoolExecutor(max_workers=128) as ex:
        alive = [h for h, ok in zip(hosts,
                 ex.map(lambda h: any(tcp_open(h, p, src, 0.25) for p in LIVENESS), hosts)) if ok]
    for h in alive:
        openp = [(p, PORTS[p]) for p in PORTS if tcp_open(h, p, src)]
        print(f"   ✅ LIVE HOST {h}:")
        for p, name in openp:
            hint = ""
            if name.startswith("FTP") or p in (21, 23):
                hint = f"  → ftp {h}  (ls/dir/pwd) — drone files"
            elif "HTTP" in name:
                hint = f"  → http://{h}:{p}/  (+ /v1? /v2?)"
            elif name == "RTSP":
                hint = f"  → ffplay rtsp://{h}:{p}/live"
            print(f"        port {p} ({name}){hint}")


def main() -> int:
    ads = adapters()
    if ads:
        print("[*] network adapters:")
        for alias, ip, gw in ads:
            tag = ""
            g = "gateway " + gw if gw else "NO GATEWAY"
            if not gw and (ip.startswith("192.168.") or ip.startswith("10.")):
                tag = "  <-- DRONE candidate (point-to-point)"
            print(f"     {alias:28} {ip:15} {g}{tag}")
    else:
        print("[!] failed to enumerate adapters via PowerShell.")

    # candidate subnets: private adapters with NO gateway (drone/virtual)
    candidates = []
    for alias, ip, gw in ads:
        first = ip.split(",")[0]
        if not gw and (first.startswith("192.168.") or first.startswith("10.")):
            base = ".".join(first.split(".")[:3])
            candidates.append((base, first))

    print()
    # 1) known DJI addresses — always probe them
    print("[*] known DJI addresses:")
    hit = False
    for h in DJI_KNOWN:
        openp = [p for p in PORTS if tcp_open(h, p)]
        if openp:
            hit = True
            print(f"   ✅ {h}: ports {openp}")
    if not hit:
        print("   (unreachable)")

    # 2) scan the candidate subnets
    for base, src in candidates:
        my_last = {a[1].split(",")[0].split(".")[3] for a in ads
                   if a[1].startswith(base + ".")}
        print(f"\n[*] scanning drone subnet {base}.0/24 (interface {src})...")
        scan_subnet(base, src, my_last)

    if not candidates:
        print("\n[!] no candidate adapter (private, no gateway) was found.")
        print("    Sure way to find the drone: run ipconfig WITHOUT the drone, then plug the drone in")
        print("    and run ipconfig again — the NEW adapter is the drone. Tell me its IP/mask.")
    print("\n(no video here — liveview goes over AOA through the remote controller. The goal here: drone FTP/HTTP/config.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
