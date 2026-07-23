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
import errno
import os
import struct
import sys
import threading
import queue
import time
import traceback

from raw_gadget import (
    RawGadget, UsbCtrlRequest,
    USB_RAW_EVENT_CONNECT, USB_RAW_EVENT_CONTROL, USB_RAW_EVENT_RESET,
    USB_RAW_EVENT_DISCONNECT, USB_RAW_EVENT_SUSPEND, USB_SPEED_HIGH,
)

# Verbose USB tracing. On by default: this stack is bring-up code and the usual
# failure ("nothing happens") is indistinguishable from a hang without a trace.
# Silence with AOA_DEBUG=0.
DEBUG = os.environ.get("AOA_DEBUG", "1") not in ("0", "")

# Bulk carries video, so logging every packet drowns the console. By default only the
# first few packets of each direction are dumped (enough to confirm the link and to see
# whether the framing is composite/DUML); AOA_DEBUG_BULK=1 logs all of them.
BULK_DEBUG = os.environ.get("AOA_DEBUG_BULK", "0") not in ("0", "")
BULK_LOG_FIRST = 10

_EVENT_NAMES = {
    USB_RAW_EVENT_CONNECT: "CONNECT",
    USB_RAW_EVENT_CONTROL: "CONTROL",
    USB_RAW_EVENT_RESET: "RESET",
    USB_RAW_EVENT_DISCONNECT: "DISCONNECT",
    USB_RAW_EVENT_SUSPEND: "SUSPEND",
}

# Standard requests, for readable traces
_REQ_NAMES = {
    0: "GET_STATUS", 1: "CLEAR_FEATURE", 3: "SET_FEATURE", 5: "SET_ADDRESS",
    6: "GET_DESCRIPTOR", 7: "SET_DESCRIPTOR", 8: "GET_CONFIGURATION",
    9: "SET_CONFIGURATION", 10: "GET_INTERFACE", 11: "SET_INTERFACE",
}
_DESC_NAMES = {1: "DEVICE", 2: "CONFIG", 3: "STRING", 6: "DEV_QUALIFIER",
               7: "OTHER_SPEED", 15: "BOS"}


def log(*a):
    if DEBUG:
        print("[aoa]", *a, flush=True)


def _ack_out(g: "RawGadget", length: int = 0):
    """Complete a host->device (OUT) control transfer.

    raw-gadget records the direction of the pending EP0 transfer: an OUT request
    sets ep0_out_pending, and calling ep0_write() on it fails with EBUSY
    ("wrong direction"). OUT requests are completed with ep0_read, matching the
    raw-gadget examples (write for IN, read for OUT). The read must cover the data
    stage, so pass wLength when the request carries one.
    """
    g.ep0_read(length)


def _reply_in(g: "RawGadget", r: "UsbCtrlRequest", data: bytes):
    """Answer a device->host (IN) control request.

    A zero-length IN is the exception: the driver only sets ep0_in_pending when
    wLength is non-zero (it tests `(bRequestType & USB_DIR_IN) && wLength`), so a
    zero-length IN is pending as OUT and ep0_write would return EBUSY.
    """
    if r.wLength == 0:
        g.ep0_read(0)
    else:
        g.ep0_write(data[:r.wLength])

# AOA vendor requests
AOA_GET_PROTOCOL = 51
AOA_SEND_STRING = 52
AOA_START = 53

AOA_VID = 0x18D1
# 0x2D00 = accessory only: one interface with two bulk endpoints, which is exactly what
# we advertise (bNumInterfaces=1). 0x2D01 means accessory+adb, i.e. TWO interfaces.
AOA_PID_ACCESSORY = 0x2D00

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
        try:
            while self._running:
                try:
                    switched = self._run_phase()
                except Exception:
                    traceback.print_exc()
                    switched = False
                if not self._running:
                    break
                if switched:
                    self._phase = 2   # after START we run as an accessory
                    # The gadget is now closed, i.e. detached from the bus. Give the host a
                    # moment to register the disconnect before we re-attach as the accessory,
                    # which is exactly the re-enumeration AOA expects after START.
                    log("re-enumerating as accessory in 0.5s")
                    time.sleep(0.5)
                else:
                    # Phase ended without START — the RC was unplugged / the host went away.
                    # _run_phase's finally already closed the gadget fd (releasing the UDC);
                    # re-initialise from phase 1 so a re-plugged RC re-enumerates cleanly
                    # instead of hitting "UDC busy" from a stale binding.
                    log("RC disconnected / host gone — releasing device, re-init from phase 1 in 1s")
                    self._phase = 1
                    time.sleep(1.0)
        except Exception:
            # This runs in a background thread; without this the traceback is easy to miss.
            print("[aoa] FATAL in AOA thread:", flush=True)
            traceback.print_exc()

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

    def _open_gadget(self) -> RawGadget:
        """Open raw-gadget and bind the UDC.

        The previous phase's fd may not have released the UDC yet, so retry on EBUSY
        instead of dying on a race we know is transient.
        """
        last: OSError | None = None
        for attempt in range(20):
            g = RawGadget()
            try:
                g.init(self.udc_driver, self.udc_device, USB_SPEED_HIGH)
                g.run()
                return g
            except OSError as e:
                g.close()
                last = e
                if e.errno != errno.EBUSY:
                    raise
                log(f"UDC busy, retry {attempt + 1}/20")
                time.sleep(0.25)
        raise last   # type: ignore[misc]

    def _teardown_eps(self, g: RawGadget) -> bool:
        """Release the bulk endpoints after a bus reset/disconnect.

        Clearing _configured also stops the IO threads (it is their loop condition);
        whichever one is parked in a blocking transfer wakes up with ESHUTDOWN.

        Returns True if all endpoints were disabled cleanly, False if ep_disable
        returned EBUSY (meaning the UDC already tore them down itself — the gadget
        fd is in a dirty state and the caller should restart the phase).
        """
        if not self._configured and self._ep_in is None and self._ep_out is None:
            return True
        self._configured = False
        clean = True
        for h in (self._ep_in, self._ep_out):
            if h is not None:
                try:
                    g.ep_disable(h)
                except OSError as e:
                    log(f"  ep_disable failed (harmless after disconnect): {e}")
                    clean = False
        self._ep_in = self._ep_out = None
        log("bus reset/disconnect: endpoints disabled, bulk threads stopping")
        return clean

    def _run_phase(self) -> bool:
        self._configured = False
        self._ep_in = self._ep_out = None
        started = False
        vid, pid = (0x18D1, 0x4EE1) if self._phase == 1 else (AOA_VID, AOA_PID_ACCESSORY)
        log(f"phase {self._phase}: init udc={self.udc_device} driver={self.udc_driver} "
            f"as {vid:04x}:{pid:04x}")
        g = self._open_gadget()
        self._g = g
        try:
            log("gadget running — waiting for USB events from the host")
            log("  (no events at all => the host is not enumerating us: check that the cable is a "
                "DATA cable, that it is in the Pi's middle 'USB' port, and that the host supplies VBUS)")
            while self._running:
                try:
                    etype, ctrl, data = g.event_fetch()
                except OSError as e:
                    # Host physically gone (RC unplugged): stop this phase so run_forever
                    # closes the fd and re-inits — otherwise the fd lingers holding the UDC.
                    log(f"event_fetch failed ({e}) — RC/host gone, ending phase to release UDC")
                    break
                log(f"event: {_EVENT_NAMES.get(etype, etype)}")
                if etype == USB_RAW_EVENT_CONNECT:
                    continue
                if etype == USB_RAW_EVENT_SUSPEND:
                    # Physical RC disconnect: SUSPEND fires before DISCONNECT.
                    # Restart the whole process — the UDC will be in a dirty state
                    # by the time DISCONNECT arrives, so recover immediately.
                    log("SUSPEND — RC physically disconnected, restarting bridge process")
                    try:
                        g.close()
                    except Exception:
                        pass
                    os.execv(sys.executable, [sys.executable] + sys.argv)
                if etype in (USB_RAW_EVENT_RESET, USB_RAW_EVENT_DISCONNECT):
                    # dwc2 reports DISCONNECT where other UDCs report RESET; either way the
                    # UDC has disabled our endpoints, so tear them down and let the next
                    # SET_CONFIGURATION re-enable them and respawn the IO threads.
                    clean = self._teardown_eps(g)
                    if not clean:
                        # ep_disable failed with EBUSY — UDC is in a dirty state.
                        # Trying to re-configure endpoints will also hang/fail.
                        # Full process restart is the only reliable recovery.
                        log("dirty disconnect — restarting bridge process now")
                        try:
                            g.close()
                        except Exception:
                            pass
                        os.execv(sys.executable, [sys.executable] + sys.argv)
                    continue
                if etype != USB_RAW_EVENT_CONTROL or ctrl is None:
                    continue
                try:
                    if self._handle_control(g, ctrl):
                        started = True
                        break   # got START -> exit for re-enumeration
                except OSError as e:
                    # One failed control request must not kill the whole AOA thread.
                    log(f"  control request failed: {e} — continuing")
        finally:
            # Always close. The fd owns the UDC, so phase 2 cannot bind it while this one
            # is open (that was an EBUSY on run()). Closing is also what detaches us from
            # the bus, which is precisely the disconnect AOA re-enumeration relies on.
            self._configured = False
            g.close()
            self._g = None
        return started

    def _handle_control(self, g: RawGadget, r: UsbCtrlRequest) -> bool:
        """Returns True if this was an AOA START (time to switch to phase 2)."""
        d2h = (r.bRequestType & 0x80) != 0
        vendor = (r.bRequestType & 0x60) == 0x40

        if vendor:
            rname = {AOA_GET_PROTOCOL: "AOA_GET_PROTOCOL", AOA_SEND_STRING: "AOA_SEND_STRING",
                     AOA_START: "AOA_START"}.get(r.bRequest, f"vendor:{r.bRequest}")
        else:
            rname = _REQ_NAMES.get(r.bRequest, str(r.bRequest))
            if r.bRequest == 6:
                rname += f"({_DESC_NAMES.get(r.wValue >> 8, r.wValue >> 8)},idx={r.wValue & 0xFF})"
        log(f"  SETUP {'IN ' if d2h else 'OUT'} {rname} "
            f"bmRequestType=0x{r.bRequestType:02x} wValue=0x{r.wValue:04x} "
            f"wIndex=0x{r.wIndex:04x} wLength={r.wLength}")

        # --- AOA vendor ---
        if vendor:
            if r.bRequest == AOA_GET_PROTOCOL and d2h:
                log("    -> replying AOA protocol v2 (host is probing for AOA!)")
                _reply_in(g, r, struct.pack("<H", 2))   # AOA v2
                return False
            if r.bRequest == AOA_SEND_STRING and not d2h:
                # The ep0_read below both receives the string and completes the transfer.
                s = g.ep0_read(r.wLength)
                val = s.split(b"\x00")[0].decode("utf-8", "replace")
                self.accessory_strings[r.wIndex] = val
                log(f"    -> host string[{r.wIndex}] = {val!r}")
                return False
            if r.bRequest == AOA_START and not d2h:
                _ack_out(g)
                print(f"[aoa] START. Remote controller identified itself: {self.accessory_strings}",
                      flush=True)
                return True
            log(f"    -> unknown vendor request {r.bRequest}, stalling")
            g.ep0_stall()
            return False

        # --- standard ---
        if r.bRequest == 6 and d2h:                      # GET_DESCRIPTOR
            dtype = r.wValue >> 8
            didx = r.wValue & 0xFF
            if dtype == 1:
                _reply_in(g, r, self._descriptors())
            elif dtype == 2:
                _reply_in(g, r, _cfg_desc())
            elif dtype == 3:
                if didx == 0:
                    _reply_in(g, r, _LANGID)
                else:
                    s = self.strings[didx - 1] if didx - 1 < len(self.strings) else "?"
                    _reply_in(g, r, _str_desc(s))
            else:
                g.ep0_stall()
            return False

        if r.bRequest == 9 and not d2h:                  # SET_CONFIGURATION
            # A host may repeat SET_CONFIGURATION. Enabling endpoints or spawning IO
            # threads twice would race on the same queues, so both sit behind this guard
            # and are only redone once _teardown_eps has cleared it.
            if self._phase == 2 and not self._configured:
                self._ep_in = g.ep_enable(_EP_IN_9)
                self._ep_out = g.ep_enable(_EP_OUT_9)
                self._configured = True
                self._start_bulk(g)
            g.vbus_draw(100)
            g.configure()                                # endpoints first, then configure
            _ack_out(g)
            log(f"    -> configured (phase {self._phase})")
            return False

        if r.bRequest == 5 and not d2h:                  # SET_ADDRESS (handled by UDC itself, just ack)
            _ack_out(g)
            return False

        if r.bRequest == 0 and d2h:                      # GET_STATUS
            _reply_in(g, r, struct.pack("<H", 0))
            return False

        if r.bRequest == 8 and d2h:                      # GET_CONFIGURATION
            _reply_in(g, r, bytes([1 if self._configured or self._phase == 1 else 0]))
            return False

        if r.bRequest == 10 and d2h:                     # GET_INTERFACE
            _reply_in(g, r, bytes([0]))
            return False

        # everything else — ack for host->dev, stall for dev->host
        if d2h:
            g.ep0_stall()
        else:
            _ack_out(g, r.wLength)   # the read must cover the data stage, if any
        return False

    def _start_bulk(self, g: RawGadget):
        ep_out, ep_in = self._ep_out, self._ep_in   # capture: _teardown_eps clears them

        def reader():
            n = 0
            total = 0
            while self._running and self._configured:
                try:
                    data = g.ep_read(ep_out, BULK_MPS)
                except OSError as e:
                    log(f"bulk reader stopped: {e}")
                    break
                if not data:
                    continue
                n += 1
                total += len(data)
                if BULK_DEBUG or n <= BULK_LOG_FIRST:
                    log(f"RC->Pi #{n} {len(data)}B: {data[:32].hex()}")
                    if n == BULK_LOG_FIRST and not BULK_DEBUG:
                        log("RC->Pi further packets not logged (AOA_DEBUG_BULK=1 for all)")
                self.rx_queue.put(data)
            log(f"bulk reader exit after {n} packets / {total} bytes")

        def writer():
            n = 0
            while self._running and self._configured:
                try:
                    data = self._tx_queue.get(timeout=0.5)
                except queue.Empty:
                    continue
                n += 1
                if BULK_DEBUG or n <= BULK_LOG_FIRST:
                    log(f"Pi->RC #{n} {len(data)}B: {data[:32].hex()}")
                try:
                    g.ep_write(ep_in, data)
                except OSError as e:
                    log(f"bulk writer stopped: {e}")
                    break

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=writer, daemon=True).start()
        print("[aoa] accessory configured, bulk IN/OUT active", flush=True)
