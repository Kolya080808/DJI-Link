"""Минимальный ADB-клиент для песочницы без записи в пользовательский .android."""
from __future__ import annotations

import socket
import sys
import time
from pathlib import Path


HOST = ("127.0.0.1", 5037)
SERIAL = "emulator-5554"


def read_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise RuntimeError("ADB connection closed")
        out.extend(chunk)
    return bytes(out)


def request(sock: socket.socket, command: str) -> bytes:
    raw = command.encode()
    sock.sendall(f"{len(raw):04x}".encode() + raw)
    status = read_exact(sock, 4)
    if status == b"FAIL":
        n = int(read_exact(sock, 4), 16)
        raise RuntimeError(read_exact(sock, n).decode(errors="replace"))
    if status != b"OKAY":
        raise RuntimeError(f"ADB status {status!r}")
    return sock.recv(4) if command.startswith("host:") else b""


def host_devices() -> str:
    with socket.create_connection(HOST, 300) as sock:
        sock.settimeout(300)
        raw = "host:devices-l".encode()
        sock.sendall(f"{len(raw):04x}".encode() + raw)
        if read_exact(sock, 4) != b"OKAY":
            raise RuntimeError("host:devices-l failed")
        n = int(read_exact(sock, 4), 16)
        return read_exact(sock, n).decode(errors="replace")


def shell(command: str) -> str:
    with socket.create_connection(HOST, 300) as sock:
        sock.settimeout(300)
        transport = f"host:transport:{SERIAL}".encode()
        sock.sendall(f"{len(transport):04x}".encode() + transport)
        if read_exact(sock, 4) != b"OKAY":
            raise RuntimeError("transport selection failed")
        raw = f"shell:{command}".encode()
        sock.sendall(f"{len(raw):04x}".encode() + raw)
        if read_exact(sock, 4) != b"OKAY":
            raise RuntimeError("shell failed")
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode(errors="replace")


def _transport() -> socket.socket:
    sock = socket.create_connection(HOST, 300)
    sock.settimeout(300)
    raw = f"host:transport:{SERIAL}".encode()
    sock.sendall(f"{len(raw):04x}".encode() + raw)
    if read_exact(sock, 4) != b"OKAY":
        sock.close()
        raise RuntimeError("transport selection failed")
    return sock


def push(local: str, remote: str) -> None:
    path = Path(local)
    size = path.stat().st_size
    with _transport() as sock:
        sock.sendall(b"0005sync:")
        if read_exact(sock, 4) != b"OKAY":
            raise RuntimeError("sync service failed")
        target = f"{remote},33272".encode()
        sock.sendall(b"SEND" + len(target).to_bytes(4, "little") + target)
        sent = 0
        last_pct = -1
        with path.open("rb") as stream:
            while chunk := stream.read(64 * 1024):
                sock.sendall(b"DATA" + len(chunk).to_bytes(4, "little") + chunk)
                sent += len(chunk)
                pct = int(sent * 100 / size)
                if pct != last_pct:
                    print(f"push {pct}%", file=sys.stderr, flush=True)
                    last_pct = pct
        sock.sendall(b"DONE" + int(time.time()).to_bytes(4, "little"))
        status = read_exact(sock, 4)
        if status == b"FAIL":
            n = int.from_bytes(read_exact(sock, 4), "little")
            raise RuntimeError(read_exact(sock, n).decode(errors="replace"))
        if status != b"OKAY":
            raise RuntimeError(f"sync result {status!r}")
        read_exact(sock, 4)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) == 1:
        print(host_devices(), end="")
    elif sys.argv[1] == "shell":
        print(shell(" ".join(sys.argv[2:])), end="")
    elif sys.argv[1] == "push":
        push(sys.argv[2], sys.argv[3])
    else:
        raise SystemExit("usage: adb_proto.py [shell command]")


if __name__ == "__main__":
    main()
