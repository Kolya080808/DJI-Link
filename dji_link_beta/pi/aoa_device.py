"""
AOA device (phone side) on Linux USB Gadget via raw-gadget.

The Pi pretends to be an Android device. The remote controller (USB host) initiates AOA itself:
  1. reads our descriptors;
  2. sends GET_PROTOCOL(51)  -> we reply with the AOA version (2);
  3. sends SEND_STRING(52) with ITS OWN strings (manufacturer="DJI", model="com.dji.logiclink");
  4. sends START(53) -> we re-enumerate as an accessory (18d1:2d01) with bulk IN/OUT;
  5. from then on DUML flows over bulk.

Phase 1 (before START): an ordinary "phone". Phase 2 (after START): accessory with two bulk endpoints.

NOTE: a USB gadget is always finalized on real hardware (dmesg, behavior of the specific
UDC). The code is a working beta, but expect iterations on the Pi itself. A good test BEFORE the
remote controller: connect the Pi to an ordinary PC/phone host and exercise it with our laptop-side aoa.py.
"""

from __future__ import annotations
import struct
import threading
import queue

from raw_gadget import (
    RawGadget, UsbCtrlRequest,
    USB_RAW_EVENT_CONNECT, USB_RAW_EVENT_CONTROL, USB_RAW_EVENT_RESET,
    USB_RAW_EVENT_DISCONNECT, USB_SPEED_HIGH,
)

# AOA vendor requests
AOA_GET_PROTOCOL = 51
AOA_SEND_STRING = 52
AOA_START = 53

AOA_VID = 0x18D1
AOA_PID_ACCESSORY = 0x2D01     # accessory + adb; 0x2D00 = accessory only

# HS bulk max packet
BULK_MPS = 512


def _dev_desc(vid, pid):
    return struct.pack("<BBHBBBBHHHBBB",
        18, 1, 0x0200, 0, 0, 0, 64, vid, pid, 0x0100, 1, 2, 3, ) + bytes([1])
    # bLength,bDescType,bcdUSB,class,sub,proto,mps0,vid,pid,bcdDev,iMan,iProd,iSer,+bNumConfig


def _cfg_desc():
    # config(9) + interface(9) + ep_in(7) + ep_out(7) = 32
    ep_in = struct.pack("<BBBBHB", 7, 5, 0x81, 0x02, BULK_MPS, 0)
    ep_out = struct.pack("<BBBBHB", 7, 5, 0x01, 0x02, BULK_MPS, 0)
    intf = struct.pack("<BBBBBBBBB", 9, 4, 0, 0, 2, 0xFF, 0xFF, 0x00, 0)
    total = 9 + 9 + 7 + 7
    cfg = struct.pack("<BBHBBBBB", 9, 2, total, 1, 1, 0, 0x80, 50)  # 100mA
    return cfg + intf + ep_in + ep_out


def _str_desc(s: str) -> bytes:
    body = s.encode("utf-16-le")
    return bytes([len(body) + 2, 3]) + body


_LANGID = bytes([4, 3, 0x09, 0x04])   # 0x0409 en-US

# 9-byte endpoint descriptors for EP_ENABLE (with audio fields bRefresh/bSynchAddress=0)
_EP_IN_9 = struct.pack("<BBBBHBBB", 7, 5, 0x81, 0x02, BULK_MPS, 0, 0, 0)
_EP_OUT_9 = struct.pack("<BBBBHBBB", 7, 5, 0x01, 0x02, BULK_MPS, 0, 0, 0)


class AoaDevice:
    """AOA device emulator. rx_queue is filled with bytes from the remote controller (bulk OUT),
    tx via send() goes to the remote controller (bulk IN)."""

    def __init__(self, udc_driver: str, udc_device: str,
                 strings=("Pi", "DJI-Link", "BETA0001")):
        self.udc_driver = udc_driver
        self.udc_device = udc_device
        self.strings = strings
        self.rx_queue: "queue.Queue[bytes]" = queue.Queue()
        self._tx_queue: "queue.Queue[bytes]" = queue.Queue()
        self.accessory_strings: dict[int, str] = {}   # what the remote controller sent
        self._g: RawGadget | None = None
        self._phase = 1
        self._ep_in = None
        self._ep_out = None
        self._configured = False
        self._running = False

    # ---- public API ----
    def send(self, data: bytes):
        self._tx_queue.put(data)

    def run_forever(self):
        """Outer loop: phase1 -> (START) -> phase2. Between phases we reopen
        raw-gadget to force re-enumeration on the bus."""
        self._running = True
        while self._running:
            switched = self._run_phase()
            if not switched:
                break
            self._phase = 2   # after START we run as an accessory

    def stop(self):
        self._running = False
        if self._g:
            self._g.close()

    # ---- internal ----
    def _descriptors(self):
        if self._phase == 1:
            # pretend to be a phone (Google VID); the remote controller will go through AOA anyway
            return _dev_desc(0x18D1, 0x4EE1)
        return _dev_desc(AOA_VID, AOA_PID_ACCESSORY)

    def _run_phase(self) -> bool:
        g = RawGadget()
        self._g = g
        self._configured = False
        self._ep_in = self._ep_out = None
        started = False
        try:
            g.init(self.udc_driver, self.udc_device, USB_SPEED_HIGH)
            g.run()
            while self._running:
                etype, ctrl, data = g.event_fetch()
                if etype == USB_RAW_EVENT_CONNECT:
                    continue
                if etype in (USB_RAW_EVENT_RESET, USB_RAW_EVENT_DISCONNECT):
                    if self._phase == 2:
                        # the remote controller toggled the bus while already in accessory mode — just continue
                        continue
                    continue
                if etype != USB_RAW_EVENT_CONTROL or ctrl is None:
                    continue
                if self._handle_control(g, ctrl):
                    started = True
                    break   # got START -> exit for re-enumeration
        finally:
            if not started:
                g.close()
        return started

    def _handle_control(self, g: RawGadget, r: UsbCtrlRequest) -> bool:
        """Returns True if this was an AOA START (time to switch to phase 2)."""
        d2h = (r.bRequestType & 0x80) != 0
        vendor = (r.bRequestType & 0x60) == 0x40

        # --- AOA vendor ---
        if vendor:
            if r.bRequest == AOA_GET_PROTOCOL and d2h:
                g.ep0_write(struct.pack("<H", 2))       # AOA v2
                return False
            if r.bRequest == AOA_SEND_STRING and not d2h:
                s = g.ep0_read(r.wLength)
                self.accessory_strings[r.wIndex] = s.split(b"\x00")[0].decode("utf-8", "replace")
                g.ep0_write(b"")                         # status
                return False
            if r.bRequest == AOA_START and not d2h:
                g.ep0_write(b"")
                print(f"[aoa] START. Remote controller identified itself: {self.accessory_strings}")
                return True
            g.ep0_stall()
            return False

        # --- standard ---
        if r.bRequest == 6 and d2h:                      # GET_DESCRIPTOR
            dtype = r.wValue >> 8
            didx = r.wValue & 0xFF
            if dtype == 1:
                g.ep0_write(self._descriptors()[:r.wLength])
            elif dtype == 2:
                g.ep0_write(_cfg_desc()[:r.wLength])
            elif dtype == 3:
                if didx == 0:
                    g.ep0_write(_LANGID[:r.wLength])
                else:
                    s = self.strings[didx - 1] if didx - 1 < len(self.strings) else "?"
                    g.ep0_write(_str_desc(s)[:r.wLength])
            else:
                g.ep0_stall()
            return False

        if r.bRequest == 9 and not d2h:                  # SET_CONFIGURATION
            g.configure()
            if self._phase == 2 and not self._configured:
                self._ep_in = g.ep_enable(_EP_IN_9)
                self._ep_out = g.ep_enable(_EP_OUT_9)
                self._configured = True
                self._start_bulk(g)
            g.ep0_write(b"")
            return False

        if r.bRequest == 5 and not d2h:                  # SET_ADDRESS (handled by UDC itself, just ack)
            g.ep0_write(b"")
            return False

        if r.bRequest == 0 and d2h:                      # GET_STATUS
            g.ep0_write(struct.pack("<H", 0)[:r.wLength])
            return False

        # everything else — ack for host->dev, stall for dev->host
        if d2h:
            g.ep0_stall()
        else:
            g.ep0_write(b"")
        return False

    def _start_bulk(self, g: RawGadget):
        def reader():
            while self._running and self._configured:
                try:
                    data = g.ep_read(self._ep_out, BULK_MPS)
                except OSError:
                    break
                if data:
                    self.rx_queue.put(data)

        def writer():
            while self._running and self._configured:
                try:
                    data = self._tx_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                try:
                    g.ep_write(self._ep_in, data)
                except OSError:
                    break

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=writer, daemon=True).start()
        print(f"[aoa] accessory configured, bulk IN/OUT active")
