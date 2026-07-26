# Pi Zero 2 W — release bridge to the DJI remote controller

The Pi pretends to be the DJI Fly phone accessory in front of the Mavic Mini 1 remote
controller (`DJI/com.dji.logiclink`). The native C++ desktop app sends commands and
receives telemetry/video over Wi-Fi; the Pi only forwards bytes.

```
[PC: dji-link C++ app] --TCP/Wi-Fi--> [Pi: dji-bridge.service] --USB(AOA)--> [Remote controller] ))) [Drone]
```

## Files

| File | What |
|------|------|
| `install.sh` | stamped GitHub Release bootstrap published as `install-pi.sh` |
| `setup_pi.sh` | full Pi bring-up: packages, `dwc2`, `raw_gadget`, services |
| `update_pi.sh` | non-interactive updater run by `dji-update.timer` when internet is available |
| `netctl.py` | Wi-Fi/AP HTTP API on `:9911` used by the C++ discovery screen |
| `bridge.py` | AOA ↔ TCP bridge on `:9910` |
| `aoa_device.py` | AOA device emulator: 51/52/53 handshake, re-enumeration, bulk endpoints |
| `raw_gadget.py` | wrapper over `/dev/raw-gadget` |
| `build_raw_gadget.sh` / `setup_gadget.sh` | low-level gadget helpers used by setup/debugging |

## One-line install from the latest release

On a clean Pi this installs everything in one pass: `dwc2`, the `raw_gadget` module,
NetworkManager Wi-Fi/AP support, the AOA bridge service, and the auto-update timer.

```bash
curl -fsSL https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh | sudo bash
```

Direct latest links:
- `https://github.com/Kolya080808/DJI-Link/releases/latest`
- `https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh`
- `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-pi.tar.gz`

After install, `dji-netctl.service`, `dji-bridge.service`, and `dji-update.timer` are
enabled. A first-time `dwc2` change requires one reboot; after that the Pi is ready on
every power-up without manual commands.

Running the same command again upgrades in place — that is exactly what `dji-update.timer`
does. The services are stopped, the previous bundle is kept as `/opt/dji-link/pi.old`, the
new one is unpacked, and both services are restarted on the new code and their state is
printed. `/opt/dji-link/VERSION` holds the installed tag. To roll back:

```bash
sudo rm -rf /opt/dji-link/pi && sudo mv /opt/dji-link/pi.old /opt/dji-link/pi
sudo systemctl restart dji-netctl dji-bridge
```

## Manual installation on the Pi

Use this only while developing the Pi bundle locally:

```bash
sudo bash setup_pi.sh --service
sudo systemctl status dji-netctl dji-bridge dji-update.timer
```

For gadget-only debugging:

```bash
sudo bash setup_gadget.sh
sudo reboot          # only if setup reports that dwc2 changed
sudo bash setup_gadget.sh
```

Plug the Pi Zero into the remote controller through the **USB** port, not **PWR**. Only
the USB port carries data.

## Services

- `dji-netctl.service` runs `netctl.py serve` and exposes Pi Wi-Fi/AP control on `:9911`.
- `dji-bridge.service` runs `bridge.py` and exposes the AOA byte stream on `:9910`.
- `dji-update.timer` runs every 6 hours, checks GitHub Releases when internet is
  available, and re-runs `install-pi.sh` only when the latest tag changed.

Useful logs:

```bash
journalctl -u dji-netctl -f
journalctl -u dji-bridge -f
journalctl -u dji-update -f
```

## Hardware test

1. Without the remote controller, plug the Pi into a normal PC/phone host and watch
   `dmesg -w` on both sides. The host should first see `18d1:4ee1`, then `18d1:2d01`
   after the AOA handshake.
2. Then plug the Pi into the remote controller. If the bridge log prints
   `Remote controller identified itself: {0:'DJI',1:'com.dji.logiclink'...}`, the remote
   accepted the Pi as the phone accessory.
3. Launch the installed desktop app (`dji-link`) on the PC. The discovery screen should
   find the Pi and connect through `:9910` / `:9911`.

## Scope

The Pi bundle intentionally does not parse DUML, telemetry, video, GPS, or media. Those
belong to the native C++ app on the PC. The Pi is a jump-host: USB accessory emulation,
Wi-Fi/AP control, AOA↔TCP forwarding, boot services, and release auto-update.
