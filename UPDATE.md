---
title: DJI Link v0.7.0 — the app finds the Pi again (LAN, /24 sweep and Wi-Fi AP)
version: 0.7.0
prerelease: true
---

## Highlights

- Pi discovery now works on a real Windows desktop: name lookups no longer fail
  before they start, the sweep looks at **every** adapter instead of a random one,
  and the app can join the Pi's own `PI_DJI_LINK-*` access point by itself.
- The Pi's access point now really shares its internet with the connected PC.

## Added

- **Wi-Fi AP join.** When the Pi is not on any reachable network, discovery scans for
  `PI_DJI_LINK-*` access points and joins one with the default key — `netsh` on
  Windows, `nmcli` on Linux, `airport`/`networksetup` on macOS. This was the last
  piece of `netfind.py` missing from the C++ port, which is why the app could not see
  the Pi's own network at all.
- **Pi netctl client** (`netctl_get`, `pi_has_internet`): after joining the AP the app
  asks the Pi on `:9911` whether it already has an uplink, and only then tells you to
  configure Wi-Fi.
- `netfind::local_ipv4s()` — every non-loopback IPv4 address the machine holds.
- Discovery progress goes to the log tail (`[netfind] ...`), so a failed scan shows
  which candidates, subnets and access points it actually tried.

## Fixed

- **Discovery found nothing on Windows.** `find_on_lan()` resolved its candidates
  (saved host, `raspberrypi.local`, `10.42.0.1`) before Winsock was initialised, so
  every lookup failed with `WSANOTINITIALISED` and all three candidates were skipped —
  including the Pi's AP gateway while connected straight to the Pi.
- **The /24 sweep scanned the wrong network.** It derived the subnet from
  `gethostname()`, which on a PC with WSL, Hyper-V, VirtualBox or a VPN returns a
  virtual adapter's address. It now enumerates the real adapters
  (`GetAdaptersAddresses` / `getifaddrs`) and sweeps every /24 we are on, Pi AP subnet
  first.
- **Connecting to the Pi AP gave no internet.** `setup_pi.sh` did not install
  `dnsmasq-base`/`iptables`, which NetworkManager's `ipv4.method=shared` needs for the
  AP's DHCP/DNS and NAT; without them the AP came up, clients associated, and nothing
  routed. `netctl.py` now also re-asserts `ip_forward` and adds a masquerade rule when
  no NAT rule exists — on hotspot start and after each uplink change.
- Refused TCP connects are detected immediately on Windows (`select` reports them in
  `exceptfds`), so scans no longer wait out the full timeout per host.

## Changed

- **`install-pi.sh` is now a real upgrader.** Re-running it (which is what
  `dji-update.timer` does) stops the services, keeps the previous bundle as
  `/opt/dji-link/pi.old`, unpacks the new one, then restarts both services and prints
  their state. Before it changes anything it verifies the downloaded archive, so a
  truncated download can no longer take a working Pi down, and a failed unpack rolls
  back to the previous install. Until now a new bundle could land on disk while the
  services kept running the old code.
- Discovery screen reports which access point it joined, and says plainly when neither
  a LAN Pi nor a Pi AP was found.
- macOS AP scanning falls back to `system_profiler` — the `airport -s` tool it used
  first was removed in macOS 14.4.

## Known limitations

- The Pi bundle is still Python + shell; the C++ rewrite of `pi/` has not started, so
  the Pi keeps needing `python3` and NetworkManager.
- The AP's NAT rules are not persisted across reboots by design — `netctl.py`
  re-applies them every time the hotspot comes up.
- On Windows the list of access points comes from the Wi-Fi service's cache
  (`netsh wlan show networks`); a Pi powered on seconds earlier may need one Retry.
- Joining the Pi's access point moves the PC off its own Wi-Fi. Internet then depends
  on the Pi having an uplink of its own — the discovery screen says when it does not.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.7.0 => version: 0.7.0).
  2. Commit UPDATE.md.
  3. git tag v0.7.0 && git push origin v0.7.0
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
