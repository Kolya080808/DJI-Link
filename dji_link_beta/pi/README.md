# Pi Zero 2 W — USB-gadget bridge to the DJI remote controller

The Pi pretends to be a phone in front of the Mavic Mini 1 remote controller (passes AOA as
`DJI/com.dji.logiclink`), while the laptop sends commands over Wi-Fi. The remote controller remains the radio bridge
to the drone.

```
[Laptop: dji_accessory.py --keyboard --pi PI_IP] --TCP/Wi-Fi--> [Pi: bridge.py] --USB(AOA)--> [Remote controller] ))) [Drone]
```

## Files
| file | what |
|------|------|
| `raw_gadget.py` | wrapper over `/dev/raw-gadget` (ioctls checked against the UAPI) |
| `aoa_device.py` | AOA device emulator: 51/52/53 handshake + re-enumeration into accessory + bulk |
| `bridge.py` | AOA ↔ TCP bridge for the laptop |
| `setup_gadget.sh` | enables dwc2/raw_gadget, finds the UDC |

## Installation on the Pi
```bash
sudo bash setup_gadget.sh          # the first time it will ask for a reboot
# after the reboot:
sudo bash setup_gadget.sh          # shows the UDC name (e.g. 20980000.usb)
```
Important: plug the Pi Zero into the remote controller **via the middle port (USB), not PWR** — only that one carries data.

## Running
On the Pi:
```bash
sudo python3 bridge.py --udc 20980000.usb
```
On the laptop:
```bash
python3 ../dji_accessory.py --keyboard --pi 192.168.x.x
```
Press WASD/Space/Shift — frames go through the Pi to the remote controller.

## How to test step by step (important — a USB gadget is always finalized on hardware)
1. **Without the remote controller, against an ordinary PC/phone host.** Plug the Pi into your laptop/PC.
   On the PC: `lsusb` should first show `18d1:4ee1`, and after the AOA handshake —
   `18d1:2d01`. You can run the handshake with our laptop-side `aoa.py`
   (`python3 ../dji_accessory.py --scan`). This proves the gadget works,
   without any risk to the drone.
2. **Watch `dmesg -w` on the Pi and on the host** — there you can see enumeration, reset, EP errors.
3. **Then the remote controller.** If after `START` the bridge log shows
   `Remote controller identified itself: {0:'DJI',1:'com.dji.logiclink'...}` — the remote controller has accepted the
   Pi as a phone. Next we check DUML.

## What is still NOT finished (honestly)
- **Exact WM160 stick DUML commands** — stubs in `../drone.py`/`../control.py`.
  This is exactly the rig for capturing them: we log what the remote controller sends when the
  real sticks move (if we temporarily leave a real phone in place and sniff), or we
  correlate the responses.
- `aoa_device.py` — a working beta, but the specific UDC (dwc2) may require tweaks
  (timings, ZLP, max packet). That is the normal process for gadget code.
- Video stream (H.264) — a separate layer, we are not touching it yet.
