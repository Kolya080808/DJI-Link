// Thin wrapper over the Linux USB Raw Gadget (/dev/raw-gadget). Ported from
// dji_link_beta/pi/raw_gadget.py.
//
// Raw Gadget lets us fully emulate a USB device from userspace: we respond to all
// control requests ourselves (including the AOA vendor requests 51/52/53) and drive
// the bulk traffic ourselves. This is exactly what is needed for the Pi Zero to
// pretend to be a phone in front of the DJI remote controller.
//
// Requirements:
//   - a kernel with CONFIG_USB_RAW_GADGET (Raspberry Pi OS has the raw_gadget module)
//   - dwc2 in peripheral mode (dtoverlay=dwc2); the UDC is usually named "20980000.usb"
//   - modprobe raw_gadget ; modprobe dwc2
//
// UAPI: include/uapi/linux/usb/raw_gadget.h — the ioctl constants and structures here
// are taken directly from that header via <linux/usb/raw_gadget.h>.
#pragma once

#include <cstdint>
#include <linux/usb/ch9.h>
#include <linux/usb/raw_gadget.h>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace djilink::pi {

using Bytes = std::vector<std::uint8_t>;

// Data capacity passed to event_fetch(): usb_ctrlrequest is 8 bytes and Raw Gadget
// examples (raw-gadget.c / tests) take up to 256 bytes of event data.
inline constexpr std::uint32_t kEventDataCap = 256;

// ---- exception with errno attached -------------------------------------------
// Every ioctl failure carries its errno so error paths can test EBUSY/ESHUTDOWN,
// like Python's OSError.
class UsbError : public std::runtime_error {
public:
    UsbError(int err, const char* what);
    int errnum; // errno at the time of the failed syscall
};

// usb_ctrlrequest from <linux/usb/ch9.h> is packed and its 16-bit fields are
// little-endian (native on the Pi, matching the Python ctypes struct with _pack_=1).
using UsbCtrlRequest = struct usb_ctrlrequest;

struct Event {
    std::uint32_t type; // usb_raw_event.type (USB_RAW_EVENT_*)
    // SETUP packet for USB_RAW_EVENT_CONTROL, empty for the other events; the Python
    // version returned the raw event data and parsed the control request from it.
    Bytes data;
    // The control request parsed from data (when >= 8 bytes), like UsbCtrlRequest
    // in the Python version; nullopt otherwise.
    std::optional<UsbCtrlRequest> ctrl;
};

class RawGadget {
public:
    explicit RawGadget(const std::string& path = "/dev/raw-gadget");
    ~RawGadget();

    RawGadget(const RawGadget&) = delete;
    RawGadget& operator=(const RawGadget&) = delete;

    void close();

    // usb_raw_init takes driver_name/device_name as 128-byte char arrays.
    void init(const std::string& udc_driver, const std::string& udc_device,
              int speed = USB_SPEED_HIGH);
    void run();

    // Blocks fetching the next event; the SETUP packet of CONTROL events in .ctrl.
    Event event_fetch(std::uint32_t data_cap = kEventDataCap);

    // --- EP0 ---
    // NOTE: usb_raw_ep_io is built as a variable-length buffer (header + data) just
    // like the Python bytearray(_SZ_EP_IO + len(data)); the driver only looks at
    // .length/.flags and copies .data[] itself.
    int ep0_write(const Bytes& data = {}, std::uint16_t flags = 0);
    Bytes ep0_read(std::uint32_t length, std::uint16_t flags = 0);
    void ep0_stall();

    // --- config / bulk ---
    void configure();
    // VBUS_DRAW takes the current limit in 2 mA units (hence ma / 2).
    void vbus_draw(int ma);
    // ep_desc9 — a raw 9-byte usb_endpoint_descriptor. Returns the endpoint handle.
    // The kernel returns the handle as the ioctl return value and never looks past
    // struct usb_endpoint_descriptor, so the two trailing bytes the Python version
    // padded bRefresh/bSynchAddress with (audio-sync fields, not part of the
    // descriptor on the wire) are dropped here.
    int ep_enable(const Bytes& ep_desc9);
    // Drop an endpoint. Required after a bus reset: the UDC disables the endpoints
    // itself, so the handles go stale and must be re-enabled on the next
    // SET_CONFIGURATION.
    void ep_disable(int handle);

    // irq-pumped bulk transfers on a handle returned by ep_enable().
    int ep_write(int handle, const Bytes& data, std::uint16_t flags = 0);
    Bytes ep_read(int handle, std::uint32_t length, std::uint16_t flags = 0);

    int fd() const {
        return fd_;
    }

private:
    int fd_ = -1;
};

// ---- helpers shared with aoa_device (module-level functions in the .py) --------
// Complete a host->device (OUT) control transfer. raw-gadget records the direction
// of the pending EP0 transfer: an OUT request sets ep0_out_pending, and calling
// ep0_write() on it fails with EBUSY ("wrong direction"). OUT requests are
// completed with ep0_read, matching the raw-gadget examples (write for IN, read
// for OUT). The read must cover the data stage, so pass wLength when the request
// carries one.
void ack_out(RawGadget& g, std::uint32_t length = 0);

// Answer a device->host (IN) control request. A zero-length IN is the exception:
// the driver only sets ep0_in_pending when wLength is non-zero (it tests
// `(bRequestType & USB_DIR_IN) && wLength`), so a zero-length IN is pending as OUT
// and ep0_write would return EBUSY.
void reply_in(RawGadget& g, const UsbCtrlRequest& r, const Bytes& data);

} // namespace djilink::pi
