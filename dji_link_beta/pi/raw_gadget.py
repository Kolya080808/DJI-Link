"""
Thin wrapper over the Linux USB Raw Gadget (/dev/raw-gadget).

Raw Gadget lets us fully emulate a USB device from userspace: we respond to all
control requests ourselves (including the AOA vendor requests 51/52/53) and drive
the bulk traffic ourselves. This is exactly what is needed for the Pi Zero to
pretend to be a phone in front of the DJI remote controller.

Requirements:
  - a kernel with CONFIG_USB_RAW_GADGET (Raspberry Pi OS has the raw_gadget module)
  - dwc2 in peripheral mode (dtoverlay=dwc2); the UDC is usually named "20980000.usb"
  - modprobe raw_gadget ; modprobe dwc2

UAPI: include/uapi/linux/usb/raw_gadget.h — the ioctl numbers and structures below
have been checked against this header.
"""

from __future__ import annotations
import ctypes
import fcntl
import os
import struct

# ---- _IOC encoding (asm-generic/ioctl.h) ----
_IOC_NRBITS, _IOC_TYPEBITS = 8, 8
_IOC_SIZEBITS, _IOC_DIRBITS = 14, 2
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _IOC(d, t, nr, size):
    return (d << _IOC_DIRSHIFT) | (ord(t) << _IOC_TYPESHIFT) | \
           (nr << _IOC_NRSHIFT) | (size << _IOC_SIZESHIFT)


def _IO(t, nr):        return _IOC(_IOC_NONE, t, nr, 0)
def _IOW(t, nr, sz):   return _IOC(_IOC_WRITE, t, nr, sz)
def _IOR(t, nr, sz):   return _IOC(_IOC_READ, t, nr, sz)
def _IOWR(t, nr, sz):  return _IOC(_IOC_WRITE | _IOC_READ, t, nr, sz)

# structure sizes
_SZ_INIT = 128 + 128 + 1           # usb_raw_init
_SZ_EVENT = 4 + 4                  # usb_raw_event (without data[])
_SZ_EP_IO = 2 + 2 + 4              # usb_raw_ep_io (without data[])
_SZ_EP_DESC = 9                    # struct usb_endpoint_descriptor (packed, with audio fields)
_SZ_U32 = 4

USB_RAW_IOCTL_INIT        = _IOW('U', 0, _SZ_INIT)
USB_RAW_IOCTL_RUN         = _IO('U', 1)
USB_RAW_IOCTL_EVENT_FETCH = _IOR('U', 2, _SZ_EVENT)
USB_RAW_IOCTL_EP0_WRITE   = _IOW('U', 3, _SZ_EP_IO)
USB_RAW_IOCTL_EP0_READ    = _IOWR('U', 4, _SZ_EP_IO)
USB_RAW_IOCTL_EP_ENABLE   = _IOW('U', 5, _SZ_EP_DESC)
USB_RAW_IOCTL_EP_DISABLE  = _IOW('U', 6, _SZ_U32)
USB_RAW_IOCTL_EP_WRITE    = _IOW('U', 7, _SZ_EP_IO)
USB_RAW_IOCTL_EP_READ     = _IOWR('U', 8, _SZ_EP_IO)
USB_RAW_IOCTL_CONFIGURE   = _IO('U', 9)
USB_RAW_IOCTL_VBUS_DRAW   = _IOW('U', 10, _SZ_U32)
USB_RAW_IOCTL_EP0_STALL   = _IO('U', 12)

# usb_raw_event.type
USB_RAW_EVENT_INVALID = 0
USB_RAW_EVENT_CONNECT = 1
USB_RAW_EVENT_CONTROL = 2
USB_RAW_EVENT_SUSPEND = 3
USB_RAW_EVENT_RESUME = 4
USB_RAW_EVENT_RESET = 5
USB_RAW_EVENT_DISCONNECT = 6

# usb_device_speed
USB_SPEED_FULL = 2
USB_SPEED_HIGH = 3

USB_RAW_IO_FLAGS_ZERO = 0x0001


class UsbCtrlRequest(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("bRequestType", ctypes.c_uint8),
        ("bRequest", ctypes.c_uint8),
        ("wValue", ctypes.c_uint16),
        ("wIndex", ctypes.c_uint16),
        ("wLength", ctypes.c_uint16),
    ]


class RawGadget:
    def __init__(self, path: str = "/dev/raw-gadget"):
        self.fd = os.open(path, os.O_RDWR)

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

    def init(self, udc_driver: str, udc_device: str, speed: int = USB_SPEED_HIGH):
        buf = bytearray(_SZ_INIT)
        buf[0:len(udc_driver)] = udc_driver.encode()
        buf[128:128 + len(udc_device)] = udc_device.encode()
        buf[256] = speed
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_INIT, bytes(buf))

    def run(self):
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_RUN)

    def event_fetch(self, data_cap: int = 256):
        """Return (event_type, ctrl_request_or_None, raw_data)."""
        buf = bytearray(_SZ_EVENT + data_cap)
        struct.pack_into("<II", buf, 0, 0, data_cap)   # type=0, length=cap
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_EVENT_FETCH, buf, True)
        etype, length = struct.unpack_from("<II", buf, 0)
        data = bytes(buf[_SZ_EVENT:_SZ_EVENT + length])
        ctrl = None
        if etype == USB_RAW_EVENT_CONTROL and len(data) >= 8:
            ctrl = UsbCtrlRequest.from_buffer_copy(data[:8])
        return etype, ctrl, data

    # --- EP0 ---
    # NOTE: every ioctl below passes a mutable bytearray with mutate_flag=True.
    # An immutable bytes arg makes fcntl.ioctl return the buffer instead of the
    # syscall's return value, and it is additionally capped at 1024 bytes
    # ("ioctl string arg too long"), which would break large transfers.
    def ep0_write(self, data: bytes = b"", flags: int = 0) -> int:
        buf = bytearray(_SZ_EP_IO + len(data))
        struct.pack_into("<HHI", buf, 0, 0, flags, len(data))
        buf[_SZ_EP_IO:] = data
        return fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP0_WRITE, buf, True)

    def ep0_read(self, length: int, flags: int = 0) -> bytes:
        buf = bytearray(_SZ_EP_IO + length)
        struct.pack_into("<HHI", buf, 0, 0, flags, length)
        n = fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP0_READ, buf, True)
        return bytes(buf[_SZ_EP_IO:_SZ_EP_IO + n])

    def ep0_stall(self):
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP0_STALL)

    # --- config / bulk ---
    def configure(self):
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_CONFIGURE)

    def vbus_draw(self, ma: int):
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_VBUS_DRAW, struct.pack("<I", ma // 2))

    def ep_enable(self, ep_desc9: bytes) -> int:
        """ep_desc9 — a 9-byte usb_endpoint_descriptor. Returns the endpoint handle.

        The kernel returns the handle as the ioctl return value, so the buffer must be
        mutable with mutate_flag set: given an immutable bytes arg, fcntl.ioctl hands
        back the buffer contents instead of the return value.
        """
        assert len(ep_desc9) == _SZ_EP_DESC
        buf = bytearray(ep_desc9)
        handle = fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP_ENABLE, buf, True)
        if not isinstance(handle, int):
            raise RuntimeError(f"EP_ENABLE returned {type(handle).__name__}, expected an int handle")
        return handle

    def ep_disable(self, handle: int):
        """Drop an endpoint. Required after a bus reset: the UDC disables the endpoints
        itself, so the handles go stale and must be re-enabled on the next
        SET_CONFIGURATION."""
        fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP_DISABLE, bytearray(struct.pack("<I", handle)), True)

    def ep_write(self, handle: int, data: bytes, flags: int = 0) -> int:
        buf = bytearray(_SZ_EP_IO + len(data))
        struct.pack_into("<HHI", buf, 0, handle, flags, len(data))
        buf[_SZ_EP_IO:] = data
        return fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP_WRITE, buf, True)

    def ep_read(self, handle: int, length: int, flags: int = 0) -> bytes:
        buf = bytearray(_SZ_EP_IO + length)
        struct.pack_into("<HHI", buf, 0, handle, flags, length)
        n = fcntl.ioctl(self.fd, USB_RAW_IOCTL_EP_READ, buf, True)
        return bytes(buf[_SZ_EP_IO:_SZ_EP_IO + n])
