// See raw_gadget.hpp — ported from dji_link_beta/pi/raw_gadget.py.
#include "pi/raw_gadget.hpp"

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <sys/ioctl.h>
#include <unistd.h>

namespace djilink::pi {

UsbError::UsbError(int err, const char* what)
    : std::runtime_error(std::string(what) + ": " + std::strerror(err)), errnum(err) {}

namespace {
// Throw UsbError with the live errno for a failed ioctl, like Python's fcntl.ioctl
// raising OSError.
[[noreturn]] void throw_io(const char* what) {
    throw UsbError(errno, what);
}
// Build the header+data buffer the ioctl expects (usb_raw_ep_io + data[]).
std::vector<std::uint8_t> make_ep_io(std::uint16_t ep, std::uint16_t flags, std::uint32_t length,
                                     const std::uint8_t* data) {
    std::vector<std::uint8_t> buf(sizeof(usb_raw_ep_io) + length, 0);
    auto* io = reinterpret_cast<usb_raw_ep_io*>(buf.data());
    io->ep = ep;
    io->flags = flags;
    io->length = length;
    if (data && length)
        std::memcpy(io->data, data, length);
    return buf;
}
} // namespace

RawGadget::RawGadget(const std::string& path) {
    // O_CLOEXEC: Python 3 creates all fds with O_CLOEXEC (PEP 446), so its
    // os.execv(sys.executable, ...) restart (see aoa_device) drops the gadget fd.
    // Without it we inherit the fd into the exec'd process and still own the already-running
    // UDC — the new process then hits EBUSY on open_gadget() forever.
    fd_ = ::open(path.c_str(), O_RDWR | O_CLOEXEC);
    if (fd_ < 0)
        throw_io((std::string("open ") + path).c_str());
}

RawGadget::~RawGadget() {
    close();
}

void RawGadget::close() {
    if (fd_ < 0)
        return;
    // Python swallowed OSError on close; the fd is usually dead after SUSPEND.
    ::close(fd_);
    fd_ = -1;
}

void RawGadget::init(const std::string& udc_driver, const std::string& udc_device, int speed) {
    usb_raw_init args{};
    // Layout: driver_name[128], device_name[128], speed — the Python version poked the
    // strings into one flat 257-byte buffer at offsets 0/128/256.
    std::memcpy(args.driver_name, udc_driver.c_str(),
                std::min<std::size_t>(udc_driver.size() + 1, sizeof(args.driver_name)));
    std::memcpy(args.device_name, udc_device.c_str(),
                std::min<std::size_t>(udc_device.size() + 1, sizeof(args.device_name)));
    args.speed = static_cast<std::uint8_t>(speed);
    if (::ioctl(fd_, USB_RAW_IOCTL_INIT, &args) < 0)
        throw_io("USB_RAW_IOCTL_INIT");
}

void RawGadget::run() {
    if (::ioctl(fd_, USB_RAW_IOCTL_RUN) < 0)
        throw_io("USB_RAW_IOCTL_RUN");
}

Event RawGadget::event_fetch(std::uint32_t data_cap) {
    // type=0, length=cap on entry (the driver fills both back in).
    std::vector<std::uint8_t> buf(sizeof(usb_raw_event) + data_cap, 0);
    auto* ev = reinterpret_cast<usb_raw_event*>(buf.data());
    ev->type = 0;
    ev->length = data_cap;
    if (::ioctl(fd_, USB_RAW_IOCTL_EVENT_FETCH, ev) < 0)
        throw_io("USB_RAW_IOCTL_EVENT_FETCH");
    Event out;
    out.type = ev->type;
    // Python did not clamp: it returned bytes(ev.data[:ev.length]) in full, i.e. a
    // kernel length beyond data_cap would blow up loudly rather than truncate. Match
    // that instead of silently misparsing a control event.
    if (ev->length > data_cap)
        throw std::runtime_error("USB_RAW_IOCTL_EVENT_FETCH: event data longer than data_cap (" +
                                 std::to_string(ev->length) + " > " + std::to_string(data_cap) +
                                 ")");
    std::uint32_t n = ev->length;
    out.data.assign(ev->data, ev->data + n);
    if (out.type == USB_RAW_EVENT_CONTROL && n >= sizeof(UsbCtrlRequest)) {
        UsbCtrlRequest ctrl;
        std::memcpy(&ctrl, ev->data, sizeof(ctrl));
        out.ctrl = ctrl;
    }
    return out;
}

int RawGadget::ep0_write(const Bytes& data, std::uint16_t flags) {
    auto buf = make_ep_io(0, flags, static_cast<std::uint32_t>(data.size()), data.data());
    int rc = ::ioctl(fd_, USB_RAW_IOCTL_EP0_WRITE, buf.data());
    if (rc < 0)
        throw_io("USB_RAW_IOCTL_EP0_WRITE");
    return rc;
}

Bytes RawGadget::ep0_read(std::uint32_t length, std::uint16_t flags) {
    auto buf = make_ep_io(0, flags, length, nullptr);
    int rc = ::ioctl(fd_, USB_RAW_IOCTL_EP0_READ, buf.data());
    if (rc < 0)
        throw_io("USB_RAW_IOCTL_EP0_READ");
    auto* io = reinterpret_cast<usb_raw_ep_io*>(buf.data());
    return Bytes(io->data, io->data + rc);
}

void RawGadget::ep0_stall() {
    if (::ioctl(fd_, USB_RAW_IOCTL_EP0_STALL) < 0)
        throw_io("USB_RAW_IOCTL_EP0_STALL");
}

void RawGadget::configure() {
    if (::ioctl(fd_, USB_RAW_IOCTL_CONFIGURE) < 0)
        throw_io("USB_RAW_IOCTL_CONFIGURE");
}

void RawGadget::vbus_draw(int ma) {
    std::uint32_t units = static_cast<std::uint32_t>(ma / 2); // in 2 mA units
    if (::ioctl(fd_, USB_RAW_IOCTL_VBUS_DRAW, &units) < 0)
        throw_io("USB_RAW_IOCTL_VBUS_DRAW");
}

int RawGadget::ep_enable(const Bytes& ep_desc9) {
    if (ep_desc9.size() != sizeof(usb_endpoint_descriptor))
        throw std::runtime_error("ep_enable expects a 9-byte usb_endpoint_descriptor");
    usb_endpoint_descriptor desc;
    std::memcpy(&desc, ep_desc9.data(), sizeof(desc));
    int handle = ::ioctl(fd_, USB_RAW_IOCTL_EP_ENABLE, &desc);
    if (handle < 0)
        throw_io("USB_RAW_IOCTL_EP_ENABLE");
    return handle;
}

void RawGadget::ep_disable(int handle) {
    std::uint32_t h = static_cast<std::uint32_t>(handle);
    if (::ioctl(fd_, USB_RAW_IOCTL_EP_DISABLE, &h) < 0)
        throw_io("USB_RAW_IOCTL_EP_DISABLE");
}

int RawGadget::ep_write(int handle, const Bytes& data, std::uint16_t flags) {
    auto buf = make_ep_io(static_cast<std::uint16_t>(handle), flags,
                          static_cast<std::uint32_t>(data.size()), data.data());
    int rc = ::ioctl(fd_, USB_RAW_IOCTL_EP_WRITE, buf.data());
    if (rc < 0)
        throw_io("USB_RAW_IOCTL_EP_WRITE");
    return rc;
}

Bytes RawGadget::ep_read(int handle, std::uint32_t length, std::uint16_t flags) {
    auto buf = make_ep_io(static_cast<std::uint16_t>(handle), flags, length, nullptr);
    int rc = ::ioctl(fd_, USB_RAW_IOCTL_EP_READ, buf.data());
    if (rc < 0)
        throw_io("USB_RAW_IOCTL_EP_READ");
    auto* io = reinterpret_cast<usb_raw_ep_io*>(buf.data());
    return Bytes(io->data, io->data + rc);
}

void ack_out(RawGadget& g, std::uint32_t length) {
    g.ep0_read(length);
}

void reply_in(RawGadget& g, const UsbCtrlRequest& r, const Bytes& data) {
    if (r.wLength == 0) {
        g.ep0_read(0);
    } else {
        Bytes trimmed(data.begin(), data.begin() + std::min<std::size_t>(r.wLength, data.size()));
        g.ep0_write(trimmed);
    }
}

} // namespace djilink::pi
