#!/usr/bin/env python3
"""
http_check.py — identify hosts: camera / router / DJI DRONE.

Pokes HTTP at / and /v1? /v2? and prints the status + Server header + start of the body.
The DJI drone's response to /v1?/v2? is distinctive; cameras/routers answer differently.

  py -3 http_check.py                 # checks the found hosts + DJI addresses
  py -3 http_check.py 192.168.1.64 192.168.2.1
"""

from __future__ import annotations
import socket
import sys

# hosts from the previous scan + known DJI addresses
DEFAULT_HOSTS = [
    "192.168.1.1", "192.168.1.64", "192.168.1.131", "192.168.1.221",
    "192.168.1.223", "192.168.1.224", "192.168.1.229",
    "192.168.2.1", "192.168.42.1", "192.168.42.2",
]
PORTS = [80, 8000, 8080]
PATHS = ["/", "/v1?", "/v2?"]


def http_get(host, port, path, timeout=1.2):
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        if s.connect_ex((host, port)) != 0:
            s.close(); return None
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
               f"User-Agent: probe\r\nConnection: close\r\n\r\n")
        s.sendall(req.encode())
        data = b""
        while len(data) < 2048:
            chunk = s.recv(1024)
            if not chunk:
                break
            data += chunk
        s.close()
        return data
    except Exception:
        return None


def summarize(data: bytes) -> str:
    text = data.decode("latin-1", "replace")
    head, _, body = text.partition("\r\n\r\n")
    status = head.splitlines()[0] if head else "?"
    server = ""
    for line in head.splitlines():
        if line.lower().startswith("server:"):
            server = line.split(":", 1)[1].strip()
    snippet = body[:100].replace("\n", " ").replace("\r", " ").strip()
    return f"{status} | Server: {server or '—'} | {snippet[:80]}"


def looks_dji(data: bytes) -> bool:
    low = data.lower()
    return any(k in low for k in (b"dji", b'"result"', b"osdk", b"/v1", b"duml",
                                  b"wm160", b"aircraft", b"gimbal"))


def main() -> int:
    hosts = sys.argv[1:] or DEFAULT_HOSTS
    print(f"[*] checking {len(hosts)} hosts...\n")
    for host in hosts:
        printed_host = False
        for port in PORTS:
            for path in PATHS:
                data = http_get(host, port, path)
                if not data:
                    continue
                if not printed_host:
                    print(f"── {host}"); printed_host = True
                flag = "  🚁 LOOKS LIKE DJI!" if looks_dji(data) else ""
                print(f"   :{port}{path:5} → {summarize(data)}{flag}")
        if printed_host:
            print()
    print("Look for the line with 🚁 (DJI) — that's the drone. camera/router is not it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
