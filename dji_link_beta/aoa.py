"""
Android Open Accessory (AOA) — the HOST (accessory) side.

Our Linux box plays the role of DJI hardware (remote controller/drone): it is the USB host, and the phone with
DJI Fly is the USB device. We switch the phone into accessory mode, presenting ourselves
as  manufacturer="DJI", model="com.dji.logiclink"  (values from
res/xml/accessory_filter.xml of the decompiled APK), after which Android itself
launches DJI Fly and binds it to our bulk channel.

AOA 1.0 protocol (control transfers on the phone's EP0):
  51  GET_PROTOCOL   (IN,  0xC0) -> 2 bytes of protocol version (>=1 means AOA is supported)
  52  SEND_STRING    (OUT, 0x40) wIndex=id, data=utf8+\0
        id: 0=manufacturer 1=model 2=description 3=version 4=uri 5=serial
  53  START          (OUT, 0x40) -> the device reconnects as an accessory
                     (Google VID 0x18D1, PID 0x2D00/0x2D01/0x2D04/0x2D05)

Requires pyusb + libusb. The phone must be plugged into a HOST port (or via
OTG/hub), NOT into a charger. On Linux you may need to detach the kernel driver and
handle permissions (udev / sudo).
"""

from __future__ import annotations
import time

try:
    import usb.core
    import usb.util
    HAVE_PYUSB = True
except Exception:                       # pyusb not installed
    HAVE_PYUSB = False

# Accessory identity — it MUST match accessory_filter.xml,
# otherwise Android won't launch DJI Fly.
DJI_IDENTITY = {
    0: "DJI",                 # manufacturer
    1: "com.dji.logiclink",   # model  (variants: "WM160", "com.dji.link")
    2: "DJI LogicLink (beta emulator)",  # description
    3: "1.0",                 # version
    4: "https://www.dji.com", # uri
    5: "BETA000000000001",    # serial
}

AOA_VID = 0x18D1
AOA_PIDS = (0x2D00, 0x2D01, 0x2D04, 0x2D05)

_AOA_GET_PROTOCOL = 51
_AOA_SEND_STRING = 52
_AOA_START = 53


class AoaError(Exception):
    pass


def _require_pyusb():
    if not HAVE_PYUSB:
        raise AoaError(
            "pyusb/libusb not installed.\n"
            "  sudo apt install libusb-1.0-0\n"
            "  pip install pyusb"
        )


def find_candidate_devices():
    """All USB devices except those already in accessory mode. For iterating over phone candidates."""
    _require_pyusb()
    devs = []
    for d in usb.core.find(find_all=True):
        if d.idVendor == AOA_VID and d.idProduct in AOA_PIDS:
            continue
        devs.append(d)
    return devs


def get_protocol(dev) -> int:
    """The AOA version supported by the device (0 = not supported)."""
    try:
        ret = dev.ctrl_transfer(0xC0, _AOA_GET_PROTOCOL, 0, 0, 2)
    except Exception:
        return 0
    if len(ret) < 2:
        return 0
    return ret[0] | (ret[1] << 8)


def switch_to_accessory(dev, identity: dict[int, str] = DJI_IDENTITY,
                        settle: float = 2.0) -> int:
    """Full AOA handshake. Returns the protocol version. After the call the device
    reconnects with the accessory VID/PID — it must be re-found via open_accessory()."""
    proto = get_protocol(dev)
    if proto < 1:
        raise AoaError("the device does not support AOA (GET_PROTOCOL returned 0)")

    for sid, val in identity.items():
        data = val.encode("utf-8") + b"\x00"
        dev.ctrl_transfer(0x40, _AOA_SEND_STRING, 0, sid, data)
        time.sleep(0.01)

    dev.ctrl_transfer(0x40, _AOA_START, 0, 0, b"")
    time.sleep(settle)   # wait for re-enumeration
    return proto


def open_accessory(retries: int = 10, delay: float = 0.5):
    """Finds the device already in accessory mode, claims the interface,
    returns (dev, ep_in, ep_out)."""
    _require_pyusb()
    dev = None
    for _ in range(retries):
        for pid in AOA_PIDS:
            dev = usb.core.find(idVendor=AOA_VID, idProduct=pid)
            if dev is not None:
                break
        if dev is not None:
            break
        time.sleep(delay)
    if dev is None:
        raise AoaError("accessory device (18d1:2d0x) not found after START")

    try:
        if dev.is_kernel_driver_active(0):
            dev.detach_kernel_driver(0)
    except Exception:
        pass

    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]

    ep_in = usb.util.find_descriptor(
        intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
    ep_out = usb.util.find_descriptor(
        intf, custom_match=lambda e:
        usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)

    if ep_in is None or ep_out is None:
        raise AoaError("bulk IN/OUT endpoints not found on the accessory")
    return dev, ep_in, ep_out
