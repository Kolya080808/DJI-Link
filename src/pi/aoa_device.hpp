// AOA device (phone side) on Linux USB Gadget via raw-gadget. Ported from
// dji_link_beta/pi/aoa_device.py.
//
// The Pi pretends to be an Android device. The remote controller (USB host) initiates AOA itself:
//   1. reads our descriptors;
//   2. sends GET_PROTOCOL(51)  -> we reply with the AOA version (2);
//   3. sends SEND_STRING(52) with ITS OWN strings (manufacturer="DJI", model="com.dji.logiclink");
//   4. sends START(53) -> we re-enumerate as an accessory (18d1:2d01) with bulk IN/OUT;
//   5. from then on DUML flows over bulk.
//
// Phase 1 (before START): an ordinary "phone". Phase 2 (after START): accessory with two bulk
// endpoints.
//
// NOTE: a USB gadget is always finalized on real hardware (dmesg, behavior of the specific
// UDC). The code is a working beta, but expect iterations on the Pi itself. A good test BEFORE the
// remote controller: connect the Pi to an ordinary PC/phone host and exercise it with our
// laptop-side aoa.py.
#pragma once

#include "pi/raw_gadget.hpp"

#include <array>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <map>
#include <mutex>
#include <string>

namespace djilink::pi {

// AOA vendor requests
inline constexpr int AOA_GET_PROTOCOL = 51;
inline constexpr int AOA_SEND_STRING = 52;
inline constexpr int AOA_START = 53;

inline constexpr std::uint16_t AOA_VID = 0x18D1;
// 0x2D00 = accessory only: one interface with two bulk endpoints, which is exactly what
// we advertise (bNumInterfaces=1). 0x2D01 means accessory+adb, i.e. TWO interfaces.
inline constexpr std::uint16_t AOA_PID_ACCESSORY = 0x2D00;

// HS bulk max packet
inline constexpr int BULK_MPS = 512;

// Bulk carries video, so logging every packet drowns the console. By default only the
// first few packets of each direction are dumped (enough to confirm the link and to see
// whether the framing is composite/DUML); AOA_DEBUG_BULK=1 logs all of them.
inline constexpr int BULK_LOG_FIRST = 10;

// Verbose USB tracing (env AOA_DEBUG, off when "0" or "" / unset semantics of the Python
// `os.environ.get(..., default)` map). Exposed as functions because env is read at startup.
bool aoa_debug();      // DEBUG in the Python module (AOA_DEBUG, default "1")
bool aoa_bulk_debug(); // BULK_DEBUG in the Python module (AOA_DEBUG_BULK, default "0")

// Python queue.Queue stand-in: put() never blocks (default maxsize=0, same as the .py),
// get() blocks, get(timeout) reports "empty" the way queue.Empty does.
class ByteQueue {
public:
    void put(Bytes item);
    Bytes get();
    bool get_for(Bytes& out, double timeout_s); // false == queue.Empty
    bool try_get(Bytes& out);                   // get_nowait(); false == queue.Empty
    bool empty();

private:
    std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<Bytes> items_;
};

// Full process restart where the Python code did os.execv(sys.executable, ...)
// (SUSPEND / dirty-disconnect recovery). bridge main() must call set_saved_argv(argc, argv)
// once at startup so the AOA worker can re-exec /proc/self/exe with the same arguments.
void set_saved_argv(int argc, char** argv);
[[noreturn]] void restart_process();

class AoaDevice {
public:
    // rx_queue is filled with bytes from the remote controller (bulk OUT),
    // tx via send() goes to the remote controller (bulk IN).
    explicit AoaDevice(std::string udc_driver, std::string udc_device,
                       std::array<std::string, 3> strings = {"Pi", "DJI-Link", "BETA0001"});

    // True only when accessory bulk endpoints are enabled and IO threads may move data.
    bool ready() const;
    void send(const Bytes& data);
    // Outer loop: phase1 -> (START) -> phase2. Between phases we reopen raw-gadget to force
    // re-enumeration on the bus.
    void run_forever();
    void stop();

    std::string udc_driver;
    std::string udc_device;
    std::array<std::string, 3> strings;
    ByteQueue rx_queue;
    // what the remote controller sent via AOA_SEND_STRING (wIndex -> value)
    std::map<int, std::string> accessory_strings;

private:
    ByteQueue tx_queue_;
    RawGadget* g_ = nullptr; // protected by g_mutex_
    std::mutex g_mutex_;
    int phase_ = 1;
    int ep_in_ = -1;  // -1 == Python's None
    int ep_out_ = -1; // -1 == Python's None
    std::atomic<bool> configured_{false};
    std::atomic<bool> running_{false};

    Bytes descriptors();
    RawGadget* open_gadget();
    bool teardown_eps(RawGadget& g);
    bool run_phase();
    bool handle_control(RawGadget& g, const UsbCtrlRequest& r);
    void start_bulk(RawGadget& g);
};

} // namespace djilink::pi
