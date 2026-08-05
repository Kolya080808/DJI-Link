// See aoa_device.hpp — ported from dji_link_beta/pi/aoa_device.py.
#include "pi/aoa_device.hpp"

#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <linux/usb/ch9.h>
#include <thread>
#include <unistd.h>

namespace djilink::pi {

namespace {

// Verbose USB tracing. On by default: this stack is bring-up code and the usual
// failure ("nothing happens") is indistinguishable from a hang without a trace.
// Silence with AOA_DEBUG=0.
bool DEBUG = true;
bool BULK_DEBUG = false;

void init_debug_from_env() {
    // os.environ.get(name, default) not in ("0", "") — unset means *default*
    // ("1" for AOA_DEBUG, "0" for AOA_DEBUG_BULK), not truthy!
    auto truthy = [](const char* v, bool def) {
        if (!v)
            return def;
        return !(std::strcmp(v, "0") == 0 || v[0] == '\0');
    };
    DEBUG = truthy(std::getenv("AOA_DEBUG"), true);
    BULK_DEBUG = truthy(std::getenv("AOA_DEBUG_BULK"), false);
}

// Python `print("[aoa]", *a, flush=True)` — fragments joined with a space.
template <typename... A> void log(const char* fmt, A&&... a) {
    if (DEBUG) {
        std::fprintf(stdout, "[aoa] ");
        std::fprintf(stdout, fmt, std::forward<A>(a)...);
        std::fputc('\n', stdout);
        std::fflush(stdout);
    }
}

// Raw ioctl machinery is build-time checked below against <linux/usb/raw_gadget.h>,
// so no own USB structs appear here; descriptor bodies are assembled byte-wise.

void put_u16le(Bytes& b, std::uint16_t v) {
    b.push_back(static_cast<std::uint8_t>(v & 0xFF));
    b.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));
}

// struct.pack("<BBHBBBBHHHBB") + bNumConfig appended — same body as _dev_desc().
Bytes dev_desc(std::uint16_t vid, std::uint16_t pid) {
    Bytes b;
    b.push_back(
        18); // bLength,bDescType,bcdUSB,class,sub,proto,mps0,vid,pid,bcdDev,iMan,iProd,iSer,+bNumConfig
    b.push_back(1);
    put_u16le(b, 0x0200);
    b.push_back(0);
    b.push_back(0);
    b.push_back(0);
    b.push_back(64);
    put_u16le(b, vid);
    put_u16le(b, pid);
    put_u16le(b, 0x0100);
    b.push_back(1);
    b.push_back(2);
    b.push_back(3);
    b.push_back(1); // bNumConfigurations
    return b;
}

// Standard endpoint descriptor part of config payload: "<BBBBHB" (7 bytes).
void pack_ep7(Bytes& b, std::uint8_t addr) {
    b.push_back(7);
    b.push_back(5); // ENDPOINT
    b.push_back(addr);
    b.push_back(0x02); // bulk
    put_u16le(b, BULK_MPS);
    b.push_back(0); // bInterval
}

// config(9) + interface(9) + ep_in(7) + ep_out(7) = 32
Bytes cfg_desc() {
    Bytes b;
    // "<BBHBBBBB" configuration header — 0x80, 50 = bus powered, max 100mA (in 2 mA units)
    b.push_back(9);
    b.push_back(2); // CONFIGURATION
    put_u16le(b, 9 + 9 + 7 + 7);
    b.push_back(1);    // bNumInterfaces
    b.push_back(1);    // bConfigurationValue
    b.push_back(0);    // iConfiguration
    b.push_back(0x80); // bmAttributes
    b.push_back(50);   // bMaxPower — 100mA
    // "<BBBBBBBBB" interface
    b.push_back(9);
    b.push_back(4); // INTERFACE
    b.push_back(0);
    b.push_back(0);
    b.push_back(2);
    b.push_back(0xFF);
    b.push_back(0xFF);
    b.push_back(0x00);
    b.push_back(0);
    pack_ep7(b, 0x81); // ep_in
    pack_ep7(b, 0x01); // ep_out
    return b;
}

// bLength,3 + s as utf-16-le — _str_desc(). ASCII only, like the .py strings.
Bytes str_desc(const std::string& s) {
    Bytes b;
    b.push_back(static_cast<std::uint8_t>(s.size() * 2 + 2));
    b.push_back(3);
    for (char c : s) {
        b.push_back(static_cast<std::uint8_t>(c));
        b.push_back(0);
    }
    return b;
}

// _LANGID = bytes([4, 3, 0x09, 0x04]) — 0x0409 en-US
const Bytes LANGID{4, 3, 0x09, 0x04};

// 9-byte endpoint descriptors for EP_ENABLE (bRefresh/bSynchAddress=0 follow the 7
// standard bytes so the buffer matches the old _SZ_EP_DESC size even though the
// descriptor itself is 7 bytes long).
const Bytes EP_IN_9{7, 5, 0x81, 0x02, 0x00, 0x02 /* 512 LE */, 0, 0, 0};
const Bytes EP_OUT_9{7, 5, 0x01, 0x02, 0x00, 0x02 /* 512 LE */, 0, 0, 0};

// usb_raw_event.type -> name, for readable traces
const char* event_name(std::uint32_t t) {
    switch (t) {
        case USB_RAW_EVENT_CONNECT:
            return "CONNECT";
        case USB_RAW_EVENT_CONTROL:
            return "CONTROL";
        case USB_RAW_EVENT_RESET:
            return "RESET";
        case USB_RAW_EVENT_DISCONNECT:
            return "DISCONNECT";
        case USB_RAW_EVENT_SUSPEND:
            return "SUSPEND";
        case USB_RAW_EVENT_RESUME:
            return "RESUME";
        default:
            return nullptr;
    }
}

// Standard requests, for readable traces — _REQ_NAMES / _DESC_NAMES in the .py.
const char* req_name(int b) {
    switch (b) {
        case 0:
            return "GET_STATUS";
        case 1:
            return "CLEAR_FEATURE";
        case 3:
            return "SET_FEATURE";
        case 5:
            return "SET_ADDRESS";
        case 6:
            return "GET_DESCRIPTOR";
        case 7:
            return "SET_DESCRIPTOR";
        case 8:
            return "GET_CONFIGURATION";
        case 9:
            return "SET_CONFIGURATION";
        case 10:
            return "GET_INTERFACE";
        case 11:
            return "SET_INTERFACE";
        default:
            return nullptr;
    }
}
const char* desc_name(int t) {
    switch (t) {
        case 1:
            return "DEVICE";
        case 2:
            return "CONFIG";
        case 3:
            return "STRING";
        case 6:
            return "DEV_QUALIFIER";
        case 7:
            return "OTHER_SPEED";
        case 15:
            return "BOS";
        default:
            return nullptr;
    }
}
const char* vendor_req_name(int b) {
    switch (b) {
        case AOA_GET_PROTOCOL:
            return "AOA_GET_PROTOCOL";
        case AOA_SEND_STRING:
            return "AOA_SEND_STRING";
        case AOA_START:
            return "AOA_START";
        default:
            return nullptr;
    }
}

// argv saved by bridge main() so restart_process() can execv("/proc/self/exe", ...).
std::vector<char*> g_saved_argv;

} // namespace

bool aoa_debug() {
    return DEBUG;
}
bool aoa_bulk_debug() {
    return BULK_DEBUG;
}

void set_saved_argv(int argc, char** argv) {
    init_debug_from_env();
    g_saved_argv.assign(argv, argv + argc);
    g_saved_argv.push_back(nullptr);
}

void restart_process() {
    // os.execv(sys.executable, [sys.executable] + sys.argv) — full process restart.
    ::execv("/proc/self/exe", g_saved_argv.data());
    // execv only returns on failure; Python raises OSError there — do the same instead
    // of killing the process, so the run_forever loop treats it like startup's retry.
    int e = errno;
    std::fprintf(stderr, "[aoa] execv(/proc/self/exe) failed: %s\n", std::strerror(e));
    throw UsbError(e, "execv /proc/self/exe");
}

// ---- ByteQueue ---------------------------------------------------------
void ByteQueue::put(Bytes item) {
    std::lock_guard<std::mutex> lk(mutex_);
    items_.push_back(std::move(item));
    cv_.notify_one();
}

Bytes ByteQueue::get() {
    std::unique_lock<std::mutex> lk(mutex_);
    cv_.wait(lk, [&] { return !items_.empty(); });
    Bytes out = std::move(items_.front());
    items_.pop_front();
    return out;
}

bool ByteQueue::get_for(Bytes& out, double timeout_s) {
    std::unique_lock<std::mutex> lk(mutex_);
    if (!cv_.wait_for(lk, std::chrono::duration<double>(timeout_s),
                      [&] { return !items_.empty(); }))
        return false; // queue.Empty
    out = std::move(items_.front());
    items_.pop_front();
    return true;
}

bool ByteQueue::try_get(Bytes& out) {
    std::lock_guard<std::mutex> lk(mutex_);
    if (items_.empty())
        return false;
    out = std::move(items_.front());
    items_.pop_front();
    return true;
}

bool ByteQueue::empty() {
    std::lock_guard<std::mutex> lk(mutex_);
    return items_.empty();
}

// ---- AoaDevice ---------------------------------------------------------
AoaDevice::AoaDevice(std::string udc_driver, std::string udc_device,
                     std::array<std::string, 3> strings)
    : udc_driver(std::move(udc_driver)), udc_device(std::move(udc_device)),
      strings(std::move(strings)) {}

bool AoaDevice::ready() const {
    return running_ && configured_ && ep_in_ >= 0 && ep_out_ >= 0;
}

// Python: send() just .put()s into tx_queue regardless of readiness; the bulk
// writer drains it as soon as it comes up. Never drop here.
void AoaDevice::send(const Bytes& data) {
    tx_queue_.put(data);
}

void AoaDevice::run_forever() {
    // Outer loop: phase1 -> (START) -> phase2. Between phases we reopen
    // raw-gadget to force re-enumeration on the bus.
    running_ = true;
    try {
        while (running_) {
            bool switched = false;
            try {
                switched = run_phase();
            } catch (const std::exception& e) {
                // traceback.print_exc() — print what we can and keep going
                std::fprintf(stderr,
                             "[aoa] Traceback (most recent call last):\n"
                             "std::exception in run_phase: %s\n",
                             e.what());
                switched = false;
            }
            if (!running_)
                break;
            if (switched) {
                phase_ = 2; // after START we run as an accessory
                // The gadget is now closed, i.e. detached from the bus. Give the host a
                // moment to register the disconnect before we re-attach as the accessory,
                // which is exactly the re-enumeration AOA expects after START.
                log("re-enumerating as accessory in 0.5s");
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
            } else {
                // Phase ended without START — the RC was unplugged / the host went away.
                // run_phase's finally already closed the gadget fd (releasing the UDC);
                // re-initialise from phase 1 so a re-plugged RC re-enumerates cleanly
                // instead of hitting "UDC busy" from a stale binding.
                log("RC disconnected / host gone — releasing device, re-init from phase 1 in 1s");
                phase_ = 1;
                std::this_thread::sleep_for(std::chrono::seconds(1));
            }
        }
    } catch (...) {
        // This runs in a background thread; without this the traceback is easy to miss.
        std::fprintf(stderr, "[aoa] FATAL in AOA thread:\n");
        std::fflush(stderr);
        // unexpected non-std exception; Python printed the traceback — we print what we can
        try {
            throw;
        } catch (const std::exception& e) {
            std::fprintf(stderr, "std::exception: %s\n", e.what());
        } catch (...) {
            std::fprintf(stderr, "<non-std exception>\n");
        }
    }
}

void AoaDevice::stop() {
    running_ = false;
    std::lock_guard<std::mutex> lk(g_mutex_);
    if (g_)
        g_->close();
}

Bytes AoaDevice::descriptors() {
    if (phase_ == 1) {
        // pretend to be a phone (Google VID); the remote controller will go through AOA anyway
        return dev_desc(0x18D1, 0x4EE1);
    }
    return dev_desc(AOA_VID, AOA_PID_ACCESSORY);
}

RawGadget* AoaDevice::open_gadget() {
    // Open raw-gadget and bind the UDC.
    //
    // The previous phase's fd may not have released the UDC yet, so retry on EBUSY
    // instead of dying on a race we know is transient.
    UsbError last(0, "open_gadget");
    for (int attempt = 0; attempt < 20; attempt++) {
        // Python: g = RawGadget() sits INSIDE the loop too — the /dev/raw-gadget open
        // is retried on EBUSY along with init/run, not just the UDC binding.
        RawGadget* g = nullptr;
        try {
            g = new RawGadget();
            g->init(udc_driver, udc_device, USB_SPEED_HIGH);
            g->run();
            return g;
        } catch (const UsbError& e) {
            if (g) {
                g->close();
                delete g;
            }
            last = e;
            if (e.errnum != EBUSY)
                throw;
            log("UDC busy, retry %d/20", attempt + 1);
            std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }
    }
    throw last;
}

bool AoaDevice::teardown_eps(RawGadget& g) {
    // Release the bulk endpoints after a bus reset/disconnect.
    //
    // Clearing configured_ also stops the IO threads (it is their loop condition);
    // whichever one is parked in a blocking transfer wakes up with ESHUTDOWN.
    //
    // Returns true if all endpoints were disabled cleanly, false if ep_disable
    // returned EBUSY (meaning the UDC already tore them down itself — the gadget
    // fd is in a dirty state and the caller should restart the phase).
    if (!configured_ && ep_in_ < 0 && ep_out_ < 0)
        return true;
    configured_ = false;
    bool clean = true;
    for (int h : {ep_in_, ep_out_}) {
        if (h >= 0) {
            try {
                g.ep_disable(h);
            } catch (const UsbError& e) {
                log("  ep_disable failed (harmless after disconnect): %s", e.what());
                clean = false;
            }
        }
    }
    ep_in_ = ep_out_ = -1;
    log("bus reset/disconnect: endpoints disabled, bulk threads stopping");
    return clean;
}

bool AoaDevice::run_phase() {
    configured_ = false;
    ep_in_ = ep_out_ = -1;
    bool started = false;
    std::uint16_t vid = phase_ == 1 ? 0x18D1 : AOA_VID;
    std::uint16_t pid = phase_ == 1 ? 0x4EE1 : AOA_PID_ACCESSORY;
    log("phase %d: init udc=%s driver=%s as %04x:%04x", phase_, udc_device.c_str(),
        udc_driver.c_str(), vid, pid);
    RawGadget* g = open_gadget();
    {
        std::lock_guard<std::mutex> lk(g_mutex_);
        g_ = g;
    }
    try {
        log("gadget running — waiting for USB events from the host");
        log("  (no events at all => the host is not enumerating us: check that the cable is a "
            "DATA cable, that it is in the Pi's middle 'USB' port, and that the host supplies "
            "VBUS)");
        while (running_) {
            Event ev;
            try {
                ev = g->event_fetch();
            } catch (const UsbError& e) {
                // Host physically gone (RC unplugged): stop this phase so run_forever
                // closes the fd and re-inits — otherwise the fd lingers holding the UDC.
                log("event_fetch failed (%s) — RC/host gone, ending phase to release UDC",
                    e.what());
                break;
            }
            const char* en = event_name(ev.type);
            log("event: %s", en ? en : "?");
            if (ev.type == USB_RAW_EVENT_CONNECT)
                continue;
            if (ev.type == USB_RAW_EVENT_SUSPEND) {
                // Physical RC disconnect: SUSPEND fires before DISCONNECT.
                // Restart the whole process — the UDC will be in a dirty state
                // by the time DISCONNECT arrives, so recover immediately.
                log("SUSPEND — RC physically disconnected, restarting bridge process");
                try {
                    g->close();
                } catch (...) {
                }
                restart_process();
            }
            if (ev.type == USB_RAW_EVENT_RESET || ev.type == USB_RAW_EVENT_DISCONNECT) {
                // dwc2 reports DISCONNECT where other UDCs report RESET; either way the
                // UDC has disabled our endpoints, so tear them down and let the next
                // SET_CONFIGURATION re-enable them and respawn the IO threads.
                bool clean = teardown_eps(*g);
                if (!clean) {
                    // ep_disable failed with EBUSY — UDC is in a dirty state.
                    // Trying to re-configure endpoints will also hang/fail.
                    // Full process restart is the only reliable recovery.
                    log("dirty disconnect — restarting bridge process now");
                    try {
                        g->close();
                    } catch (...) {
                    }
                    restart_process();
                }
                continue;
            }
            if (ev.type != USB_RAW_EVENT_CONTROL || !ev.ctrl)
                continue;
            try {
                if (handle_control(*g, *ev.ctrl)) {
                    started = true;
                    break; // got START -> exit for re-enumeration
                }
            } catch (const UsbError& e) {
                // One failed control request must not kill the whole AOA thread.
                log("  control request failed: %s — continuing", e.what());
            }
        }
    } catch (...) {
        // Always close (Python's finally). The fd owns the UDC, so phase 2 cannot bind
        // it while this one is open (that was an EBUSY on run()). Closing is also what
        // detaches us from the bus, which is precisely the disconnect AOA re-enumeration
        // relies on.
        configured_ = false;
        {
            std::lock_guard<std::mutex> lk(g_mutex_);
            g_ = nullptr;
        }
        g->close();
        delete g;
        throw;
    }
    configured_ = false;
    {
        std::lock_guard<std::mutex> lk(g_mutex_);
        g_ = nullptr;
    }
    g->close();
    delete g;
    return started;
}

bool AoaDevice::handle_control(RawGadget& g, const UsbCtrlRequest& r) {
    // Returns true if this was an AOA START (time to switch to phase 2).
    bool d2h = (r.bRequestType & 0x80) != 0;
    bool vendor = (r.bRequestType & 0x60) == 0x40;

    std::string rname;
    if (vendor) {
        const char* n = vendor_req_name(r.bRequest);
        rname = n ? n : "vendor:" + std::to_string(static_cast<int>(r.bRequest));
    } else {
        const char* n = req_name(r.bRequest);
        rname = n ? n : std::to_string(static_cast<int>(r.bRequest));
        if (r.bRequest == 6) {
            std::uint16_t dtype = r.wValue >> 8;
            const char* dn = desc_name(dtype);
            char tmp[64];
            if (dn)
                std::snprintf(tmp, sizeof(tmp), "(%s,idx=%u)", dn,
                              static_cast<unsigned>(r.wValue & 0xFF));
            else
                std::snprintf(tmp, sizeof(tmp), "(%u,idx=%u)", static_cast<unsigned>(dtype),
                              static_cast<unsigned>(r.wValue & 0xFF));
            rname += tmp;
        }
    }
    log("  SETUP %s %s bmRequestType=0x%02x wValue=0x%04x wIndex=0x%04x wLength=%u",
        d2h ? "IN " : "OUT", rname.c_str(), static_cast<unsigned>(r.bRequestType), r.wValue,
        r.wIndex, r.wLength);

    // --- AOA vendor ---
    if (vendor) {
        if (r.bRequest == AOA_GET_PROTOCOL && d2h) {
            log("    -> replying AOA protocol v2 (host is probing for AOA!)");
            Bytes proto;
            put_u16le(proto, 2);
            reply_in(g, r, proto); // AOA v2
            return false;
        }
        if (r.bRequest == AOA_SEND_STRING && !d2h) {
            // The ep0_read below both receives the string and completes the transfer.
            Bytes s = g.ep0_read(r.wLength);
            std::size_t end = 0;
            while (end < s.size() && s[end] != 0)
                end++;
            std::string val(reinterpret_cast<const char*>(s.data()), end);
            accessory_strings[r.wIndex] = val;
            log("    -> host string[%u] = '%s'", r.wIndex, val.c_str());
            return false;
        }
        if (r.bRequest == AOA_START && !d2h) {
            ack_out(g);
            std::string items;
            for (const auto& [idx, val] : accessory_strings) {
                if (!items.empty())
                    items += ", ";
                items += std::to_string(idx) + ": '" + val + "'";
            }
            std::fprintf(stdout, "[aoa] START. Remote controller identified itself: {%s}\n",
                         items.c_str());
            std::fflush(stdout);
            return true;
        }
        log("    -> unknown vendor request %u, stalling", static_cast<unsigned>(r.bRequest));
        g.ep0_stall();
        return false;
    }

    // --- standard ---
    if (r.bRequest == 6 && d2h) { // GET_DESCRIPTOR
        std::uint16_t dtype = r.wValue >> 8;
        std::uint16_t didx = r.wValue & 0xFF;
        if (dtype == 1) {
            reply_in(g, r, descriptors());
        } else if (dtype == 2) {
            reply_in(g, r, cfg_desc());
        } else if (dtype == 3) {
            if (didx == 0) {
                reply_in(g, r, LANGID);
            } else {
                // strings[didx - 1] if didx - 1 < len else "?"
                std::string s = "?";
                if (didx >= 1 && static_cast<std::size_t>(didx - 1) < strings.size())
                    s = strings[didx - 1];
                reply_in(g, r, str_desc(s));
            }
        } else {
            g.ep0_stall();
        }
        return false;
    }

    if (r.bRequest == 9 && !d2h) { // SET_CONFIGURATION
        // A host may repeat SET_CONFIGURATION. Enabling endpoints or spawning IO
        // threads twice would race on the same queues, so both sit behind this guard
        // and are only redone once teardown_eps has cleared it.
        if (phase_ == 2 && !configured_) {
            ep_in_ = g.ep_enable(EP_IN_9);
            ep_out_ = g.ep_enable(EP_OUT_9);
            configured_ = true;
            start_bulk(g);
        }
        g.vbus_draw(100);
        g.configure(); // endpoints first, then configure
        ack_out(g);
        log("    -> configured (phase %d)", phase_);
        return false;
    }

    if (r.bRequest == 5 && !d2h) { // SET_ADDRESS (handled by UDC itself, just ack)
        ack_out(g);
        return false;
    }

    if (r.bRequest == 0 && d2h) { // GET_STATUS
        Bytes status;
        put_u16le(status, 0);
        reply_in(g, r, status);
        return false;
    }

    if (r.bRequest == 8 && d2h) { // GET_CONFIGURATION
        Bytes v{static_cast<std::uint8_t>(configured_ || phase_ == 1 ? 1 : 0)};
        reply_in(g, r, v);
        return false;
    }

    if (r.bRequest == 10 && d2h) { // GET_INTERFACE
        Bytes v{0};
        reply_in(g, r, v);
        return false;
    }

    // everything else — ack for host->dev, stall for dev->host
    if (d2h) {
        g.ep0_stall();
    } else {
        ack_out(g, r.wLength); // the read must cover the data stage, if any
    }
    return false;
}

void AoaDevice::start_bulk(RawGadget& g) {
    const int ep_out = ep_out_, ep_in = ep_in_; // capture: teardown_eps clears them

    std::thread([this, &g, ep_out] {
        int n = 0;
        std::size_t total = 0;
        while (running_ && configured_) {
            Bytes data;
            try {
                data = g.ep_read(ep_out, BULK_MPS);
            } catch (const UsbError& e) {
                log("bulk reader stopped: %s", e.what());
                break;
            }
            if (data.empty())
                continue;
            n += 1;
            total += data.size();
            if (aoa_bulk_debug() || n <= BULK_LOG_FIRST) {
                std::string hex;
                char tmp[4];
                for (std::size_t i = 0; i < std::min<std::size_t>(32, data.size()); i++) {
                    std::snprintf(tmp, sizeof(tmp), "%02x", data[i]);
                    hex += tmp;
                }
                log("RC->Pi #%d %zuB: %s", n, data.size(), hex.c_str());
                if (n == BULK_LOG_FIRST && !aoa_bulk_debug())
                    log("RC->Pi further packets not logged (AOA_DEBUG_BULK=1 for all)");
            }
            rx_queue.put(std::move(data));
        }
        log("bulk reader exit after %d packets / %zu bytes", n, total);
    }).detach();

    std::thread([this, &g, ep_in] {
        int n = 0;
        while (running_ && configured_) {
            Bytes data;
            if (!tx_queue_.get_for(data, 0.5))
                continue; // queue.Empty
            n += 1;
            if (aoa_bulk_debug() || n <= BULK_LOG_FIRST) {
                std::string hex;
                char tmp[4];
                for (std::size_t i = 0; i < std::min<std::size_t>(32, data.size()); i++) {
                    std::snprintf(tmp, sizeof(tmp), "%02x", data[i]);
                    hex += tmp;
                }
                log("Pi->RC #%d %zuB: %s", n, data.size(), hex.c_str());
            }
            try {
                g.ep_write(ep_in, data);
            } catch (const UsbError& e) {
                log("bulk writer stopped: %s", e.what());
                break;
            }
        }
    }).detach();

    std::fprintf(stdout, "[aoa] accessory configured, bulk IN/OUT active\n");
    std::fflush(stdout);
}

} // namespace djilink::pi
