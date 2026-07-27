# Pi discovery + Wi-Fi AP fix

Notes on the change that fixed PC → Pi connectivity: the C++ client not joining the
Pi's Wi-Fi, Windows asking for a WPS PIN instead of the password, and the AP having no
internet / its gateway answering on no port.

## Symptoms reported

1. The C++ client did not auto-join the Pi AP — it dropped the current Wi-Fi and then
   "did nothing".
2. The Pi's gateway `10.42.0.1` answered on no port during discovery.
3. Clients on the Pi's AP had no internet, even though the Pi itself did.
4. **Windows** prompted for "the PIN from the router" instead of the passphrase — on
   every Windows machine, but not on Linux/macOS/iOS.

## Root causes (three independent bugs)

### A. Discovery probed the wrong port (bridge :9910 instead of netctl :9911)

Discovery keyed liveness off `BRIDGE_PORT` (9910). But `bridge.py` calls `detect_udc()`
and **exits when no RC/UDC is plugged in** — and the RC is plugged in *after* discovery
(the UI even says so). So :9910 was down at discovery time and `dji-bridge.service`
just restart-looped. The control service `netctl` (:9911) is always up.

Result: `join_ap()` waited 10 s on a dead :9910 and reported failure ("joined but does
nothing"); `find_on_lan` / `sweep_lan` found nothing → "gateway answers on no port".

### B. NetworkManager's AP advertises WPS → Windows PIN prompt

NM runs its AP through `wpa_supplicant`, which advertises WPS in the beacon. Windows
(WCN) then offers WPS PIN registration instead of the passphrase prompt, and the
association frequently fails outright. Linux/macOS/iOS ignore the WPS IE — which is why
only Windows broke. There is no reliable way to disable WPS on NM's AP short of a very
new NM (not exposed via nmcli); the dependable fix is hostapd with `wps_state=0`.

### C. AP+STA on one radio + `ipv4.method=shared` is fragile

AP+STA concurrency on the Pi Zero 2 W's brcmfmac chip is barely supported (clients
sometimes can't even reach the AP host), and on a Lite image `ipv4.method=shared` can
come up without dnsmasq/iptables, so clients associate but get no DHCP / no route.

## Changes

### Discovery → netctl control port (:9911)

Liveness is now probed on `NETCTL_PORT`, which is always up; `BRIDGE_PORT` stays only
for the actual flight data connection. `netsh` output on Windows is now logged so a
failed join is diagnosable instead of silent.

- `src/core/netfind.cpp`, `src/core/netfind.hpp` — `find_on_lan`, `sweep_lan`,
  `join_ap` probe `NETCTL_PORT`; netsh add-profile / connect output logged.
- `dji_link_beta/netfind.py` — same change for the Python client.

### AP → hostapd + dnsmasq (WPS off, WPA2-CCMP, own DHCP/DNS/NAT)

- `dji_link_beta/pi/ap.sh` (new) — brings the AP up/down on `uap0`:
  - hostapd: `ssid=PI_DJI_LINK-<id>`, `wpa=2`, `wpa_key_mgmt=WPA-PSK`,
    `rsn_pairwise=CCMP`, **`wps_state=0`**, channel synced to `wlan0`'s uplink (single
    radio) or channel 6, country code from `iw reg get` when set.
  - dnsmasq bound to `uap0` only (`bind-dynamic`): DHCP `10.42.0.50–150`, DNS forwarded
    to `1.1.1.1` / `8.8.8.8` so clients resolve regardless of the Pi's resolv.conf.
  - iptables MASQUERADE + FORWARD so AP clients reach the internet whenever the Pi does.
- `dji_link_beta/pi/netctl.py` — the AP is now the `dji-ap` systemd unit:
  - `hotspot()` / `status()` / `connect()` drive `dji-ap` via `systemctl`;
  - `connect()` restarts `dji-ap` after joining an uplink so hostapd re-tunes the channel;
  - `hostapd_mode()` (detected by the unit file `/etc/systemd/system/dji-ap.service`)
    falls back to the old NM `ipv4.method=shared` AP when the unit is absent — no
    regression on a Pi not yet re-set-up, and removing the unit is the manual escape hatch.
- `dji_link_beta/pi/setup_pi.sh` — installs `hostapd`; creates `dji-ap.service`; writes
  a NetworkManager drop-in marking **only** `uap0` unmanaged (`interface-name:uap0`) so
  `wlan0` stays managed for scanning + uplink; disables Debian's stock hostapd service.

The Windows join profile (`WPA2PSK` / `AES`) already matches hostapd's WPA2-CCMP, so no
client-side profile change was needed.

## Verify on the Pi

```bash
systemctl status dji-ap                    # active = AP up
iw dev                                      # expect uap0 (type AP)
grep -E 'wps_state|wpa|channel' /run/dji-ap/hostapd.conf
journalctl -u dji-ap -n 40 --no-pager
```

Fallback / rollback to the NetworkManager AP is documented in
[`dji_link_beta/pi/README.md`](../dji_link_beta/pi/README.md#wi-fi-access-point-why-hostapd-not-networkmanager).

## Caveats

- **Untested on hardware.** All local CI checks pass (`bash -n`, `py_compile`,
  `clang-format --Werror`, C++ `-fsyntax-only`, a mocked netctl dispatch test), but the
  hostapd path needs on-device verification — hence the NM fallback and the verify steps.
- **Single-radio limit.** "AP + Wi-Fi uplink at the same time" stays fragile on the Pi
  Zero 2 W. Field use (no uplink) is solid; the home/uplink mode is best-effort with a
  channel re-tune.
- **Follow-up.** The C++ discovery screen only shows "configure Wi-Fi on the Pi"; it has
  no uplink scan/connect UI yet (the Python client does, via netctl `/scan` + `/connect`).

## CI/CD impact

None expected. C++ changes compile and pass clang-format; Pi `.py`/`.sh` changes pass
the release `pi-installer` checks (`python3 -m py_compile`, `bash -n`); `ap.sh` is picked
up automatically by the bundle glob. No version bump (`UPDATE.md` untouched).
