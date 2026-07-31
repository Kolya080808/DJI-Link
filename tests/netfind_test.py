#!/usr/bin/env python3
"""Offline Pi discovery must not depend on detailed status or internet."""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    "netfind", os.path.join(HERE, "..", "dji_link_beta", "netfind.py"))
netfind = importlib.util.module_from_spec(spec)
spec.loader.exec_module(netfind)

calls = []


def fake_netctl(host, path, body=None, timeout=8.0):
    calls.append((host, path, timeout))
    if path == "/healthz":
        return {"ok": True, "service": "dji-link-netctl",
                "address": "10.42.0.1", "ap_ssid": "PI_DJI_LINK-test"}
    if path == "/status":
        raise TimeoutError("offline detailed status is deliberately unavailable")
    raise AssertionError(path)


netfind._netctl = fake_netctl

assert netfind.is_pi_host("10.42.0.1")
assert calls == [("10.42.0.1", "/healthz", 1.0)]

calls.clear()
st = netfind.pi_status("10.42.0.1")
assert st and st["service"] == "dji-link-netctl"
assert [path for _, path, _ in calls] == ["/status", "/healthz"]

calls.clear()
netfind.find_on_lan = lambda saved_host=None: "10.42.0.1"
netfind.sweep_lan = lambda: (_ for _ in ()).throw(AssertionError("unneeded LAN sweep"))
result = netfind.discover()
assert result["host"] == "10.42.0.1"
assert result["needs_internet_prompt"] is False

print("offline netfind checks passed")
