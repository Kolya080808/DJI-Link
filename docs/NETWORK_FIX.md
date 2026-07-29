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
up automatically by the bundle glob. No version bump for *this* change (`UPDATE.md`
untouched) — the v0.8.2 work below does bump it.

---

# v0.8.2 — uplink re-join, and the access point that must never go away

The change above got the AP working. Two problems survived it, and one was created by
the attempted fix in v0.8.1.

## Symptoms reported

1. Joining a Wi-Fi network through the API worked once. Disconnecting, joining another
   network, disconnecting again and going back to the first one failed with
   `connect failed: Error: 802-11-wireless-security.key-mgmt: property is missing.`
2. After installing **v0.8.1** the Pi lost networking completely: no `PI_DJI_LINK-*`
   access point at all, and no answer on the LAN either.
3. While the Pi had no uplink, the PC could not reach it on the Pi's own network — the
   one thing that is supposed to work when nothing else does.

## Root causes

### A. `nmcli dev wifi connect <ssid> password <psk>` is not a way to set security

That command sends NetworkManager a profile that carries a PSK and **no `key-mgmt`**,
expecting the daemon to fill it in from the scan entry for that SSID. When the AP is not
in the scan cache at that instant — right after a disconnect, on a re-join, on a hidden
SSID, or while the single radio is busy holding the AP up — there is nothing to infer
from and NM's `verify()` rejects the profile with exactly the reported message. The same
error is reported across distributions and has regressed outright in more than one
NetworkManager release, so "it works on my machine" is not a defence.

The fix is the documented one: state the security in the profile.
`nmcli con add type wifi … 802-11-wireless-security.key-mgmt wpa-psk
802-11-wireless-security.psk … 802-11-wireless-security.psk-flags 0`, then `con up`.
`psk-flags 0` marks the secret system-owned; the default (agent-owned) makes NM wait for
a secret agent that does not exist on a headless Pi and fail with *"(7) Secrets were
required, but not provided"*.

v0.8.1 already had this as a **fallback** after `dev wifi connect` had failed. It is now
the only path, and `dev wifi connect` is not used at all.

### B. v0.8.1 deleted the Pi's saved profiles before it knew it could replace them

`_delete_profiles_for_ssid()` ran first, then the join was attempted. A join that failed
for any reason (wrong password, AP out of range, radio busy) left the Pi with no profile
for that network — so it never rejoined at boot either, and the LAN path to the Pi was
gone for good. Combined with (C) below, that is a Pi with nothing left.

Now nothing is deleted up front. The new profile is built under a staging name, the
competing profiles for that SSID are only *parked* (`autoconnect no`), and the join is
attempted. On success the duplicates are deleted and the profile is renamed to the SSID;
on failure the staging profile is removed, the parked profiles are un-parked and the
connection that was active before is brought back up. A failed join now leaves the Pi
exactly as it found it.

### C. The AP followed the uplink onto channels it is not allowed to beacon on

`ap.sh` copied `wlan0`'s channel into `hostapd.conf` unchecked. Two ways that kills the AP:

* **5 GHz uplink.** The Zero 2 W radio (CYW43438) is 2.4 GHz only, so `hw_mode=a` is a
  config hostapd cannot start with.
* **Channel 12 or 13.** With no WLAN country set the kernel is in the world regulatory
  domain (`00`), which marks 12/13 **NO-IR** — receive only, no beaconing. hostapd
  refuses to start. Channels 12/13 are ordinary router channels in Europe and Russia.

Then `dji-ap.service` had `Restart=always` / `RestartSec=3` and `ExecStopPost=ap.sh post`
**deleted `uap0`**. So a config hostapd could not start became a create-and-destroy loop
on the virtual AP interface every three seconds — on a single-radio brcmfmac chip that
does not just fail quietly, it takes the station interface down with it. No AP, no LAN:
the reported "everything fell".

The channel is now intersected with what the kernel says this radio may beacon on
(`iw phy … info`, minus `disabled` / `no IR` / `radar detection`) and falls back to
6/1/11 otherwise; `post` no longer deletes `uap0`; two short hostapd runs in a row pin a
safe channel until a run lasts.

### D. The AP was restarted when it did not need to be

`connect()` restarted `dji-ap` after every successful join and `disconnect()` restarted
it unconditionally — dropping every laptop associated to the Pi each time, including when
the uplink had just gone away and the AP was the only thing left. Restarts now happen
only when the channel actually changes or the AP is unhealthy.

### E. Wi-Fi power save, MAC randomisation, and a blocking HTTP server

`iw dev wlan0 set power_save off` at AP start is undone by NetworkManager on the next
connection. Power save on a Pi is a standing cause of "answers for a few minutes, then
goes quiet", and it destabilises AP+STA on brcmfmac. It is now off by default via
`/etc/NetworkManager/conf.d/98-dji-wifi.conf` (`wifi.powersave = 2`) and per profile.
Scan MAC randomisation is disabled in the same file: `uap0`'s address is derived from
`wlan0`'s. `ap.sh` also no longer forces a MAC on `uap0` — the driver already derives one
with the locally-administered bit set, and overriding it is a documented way to get AP
clients that associate, get a lease and cannot exchange traffic.

`netctl.py` now serves on `ThreadingHTTPServer`: `/scan` takes seconds and `/connect`
tens of seconds, and on the old single-threaded server either one blocked `/status`,
which the PC client reads as "the Pi stopped answering".

## Two paths, by design

Joining `PI_DJI_LINK-*` gives the PC the Pi at `10.42.0.1` **on-link and never NATed**,
and the internet through the Pi's uplink **via NAT out of `wlan0`**. The first does not
depend on the second: no uplink, no problem — the control path is still there. When there
*is* an uplink, the Pi is additionally reachable on that LAN, which the PC client already
prefers (`find_on_lan` / `sweep_lan`) and which is a genuinely separate route in.

## Recovery

`pi/rescue.sh` repairs a Pi that has lost everything, without using anything from the
bundle. It also runs straight off the SD card's FAT partition through the same
`systemd.run=` first-boot hook Raspberry Pi Imager uses, so a Pi with no console and no
network can still be fixed. `install-pi.sh` now verifies the AP after an upgrade and
rolls back to `pi.old` if it did not come up — `dji-update.timer` runs that installer
unattended, so a bad release must not be able to strand the Pi.

## Verify on the Pi

```bash
sudo python3 netctl.py doctor            # every check in one place
bash ap.sh health                        # "ok", or exactly what is wrong
bash ap.sh chan                          # the channel it would use right now
journalctl -u dji-ap -n 40 --no-pager
```

## Sources

* nmcli / `key-mgmt: property is missing`, and the explicit-profile fix —
  [Arch BBS 307943](https://bbs.archlinux.org/viewtopic.php?id=307943),
  [Arch BBS 307913](https://bbs.archlinux.org/viewtopic.php?id=307913),
  [Raspberry Pi forums 396405](https://forums.raspberrypi.com/viewtopic.php?t=396405),
  [balena-cli #657](https://github.com/balena-io/balena-cli/issues/657)
* AP+STA on one brcmfmac radio must share a channel; do not change the AP interface's MAC —
  [RaspAP #77](https://github.com/RaspAP/raspap-webgui/issues/77),
  [Raspberry Pi forums 212991](https://forums.raspberrypi.com/viewtopic.php?t=212991),
  [raspberrypi/firmware #1463](https://github.com/raspberrypi/firmware/issues/1463)
* Pi Zero 2 W is 2.4 GHz only —
  [product page](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
* World regulatory domain `00` marks channels 12/13 NO-IR and hostapd will not start —
  [Ubuntu forums 2295217](https://ubuntuforums.org/archive/index.php/t-2295217.html),
  [Debian #721865](https://bugs.debian.org/cgi-bin/bugreport.cgi?bug=721865)
* `wifi.powersave = 2` means "disable" —
  [NetworkManager Wi-Fi power saving notes](https://gist.github.com/jcberthon/ea8cfe278998968ba7c5a95344bc8b55)
* WLAN country lands in `cmdline.txt` on Bookworm —
  [Raspberry Pi forums 368564](https://forums.raspberrypi.com/viewtopic.php?t=368564)
* `systemd.run=` first-boot hook —
  [Raspberry Pi forums 320331](https://forums.raspberrypi.com/viewtopic.php?t=320331),
  [rpi-imager #554](https://github.com/raspberrypi/rpi-imager/issues/554)
