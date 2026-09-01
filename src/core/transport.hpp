// Transport abstraction: where DUML bytes physically go to / come from.
// Ported from transport.py. Decouples the Drone API from the link (TCP to the Pi,
// serial to the RC, or a loopback log for the simulator).
#pragma once

#include "core/bytes.hpp"

#include <cstdint>
#include <memory>
#include <mutex>
#include <string>

namespace djilink {

class Transport {
public:
    virtual ~Transport() = default;
    virtual void send(const Bytes& frame) = 0;
    // Return the raw bytes read (may be empty on timeout).
    virtual Bytes recv(int timeout_ms = 1000) = 0;
    virtual void close() {}
};

// Loopback for the simulator (--sim): prints outgoing frames, and — for the flight-mode work
// (roadmap T6) — models the one drone reaction the roadmap needs. It tracks a current FLYC_STATE
// and, whenever send() sees a SoftSwitchMode gear frame (cmd_set 0x06), flips that state to match
// the selected gear; recv() then streams a minimal OSD-common push carrying it, so switching
// Normal/Sport/Cine is observable on --sim (previously recv() returned nothing, so the sim never
// produced telemetry and the HUD mode stayed blank). Not a full drone model — only FLYC_STATE.
class LogTransport : public Transport {
public:
    explicit LogTransport(bool verbose = true, bool silent_repeat = true);
    void send(const Bytes& frame) override;
    Bytes recv(int timeout_ms = 1000) override;

private:
    bool verbose_;
    bool silent_repeat_;
    Bytes last_;
    // send() runs on the sender thread and recv() on the rx thread, so the reported state they
    // share is mutex-guarded. Defaults to a decisive Normal (GPS_Atti) until a gear frame arrives.
    std::mutex sim_mu_;
    std::uint8_t flyc_state_;
};

// TCP client to the Pi bridge (bin/dji-bridge in the pi bundle). Transparently shuffles DUML bytes.
class NetTransport : public Transport {
public:
    NetTransport(const std::string& host, int port = 9910);
    ~NetTransport() override;
    void send(const Bytes& frame) override;
    Bytes recv(int timeout_ms = 1000) override;
    void close() override;

private:
    // Holds a POSIX fd or a Windows SOCKET (which is 64-bit on Win64, so int is
    // too narrow); -1 == invalid on both. Cast to the platform socket type in .cpp.
    std::intptr_t fd_ = -1;
};

// Wrap the AOA/Pi path: pack outgoing DUML frames into a composite unit (0x5749);
// received data is returned raw (demux happens client-side).
class CompositeTransport : public Transport {
public:
    explicit CompositeTransport(std::unique_ptr<Transport> inner);
    void send(const Bytes& frame) override;
    Bytes recv(int timeout_ms = 1000) override;
    void close() override;

private:
    std::unique_ptr<Transport> inner_;
};

// DUML straight into the USB Virtual COM of the RC/drone (e.g. COM4, /dev/ttyACM0).
class SerialTransport : public Transport {
public:
    explicit SerialTransport(const std::string& port, int baudrate = 115200);
    ~SerialTransport() override;
    void send(const Bytes& frame) override;
    Bytes recv(int timeout_ms = 1000) override;
    void close() override;

private:
#ifdef _WIN32
    void* handle_ = nullptr; // HANDLE
#else
    int fd_ = -1;
#endif
};

} // namespace djilink
