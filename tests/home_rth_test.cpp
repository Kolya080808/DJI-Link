#include "core/drone.hpp"
#include "core/duml.hpp"
#include "core/telemetry.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <vector>

using namespace djilink;

namespace {
class CaptureTransport final : public Transport {
public:
    void send(const Bytes& data) override {
        frames.push_back(data);
    }
    Bytes recv(int) override {
        return {};
    }
    std::vector<Bytes> frames;
};

double read_f64(const Bytes& p, std::size_t off) {
    std::uint64_t bits = 0;
    for (int i = 0; i < 8; ++i)
        bits |= static_cast<std::uint64_t>(p[off + i]) << (8 * i);
    double value = 0;
    std::memcpy(&value, &bits, sizeof(value));
    return value;
}

void require(bool condition, const char* message) {
    if (!condition)
        throw std::runtime_error(message);
}
} // namespace

int main() {
    try {
        CaptureTransport transport;
        Drone drone(&transport);

        drone.set_home_point(12.25, -73.5);
        auto home = DumlPacket::decode(transport.frames.back());
        require(home.has_value(), "set-home frame did not decode");
        require(home->sender == DEV_APP && home->receiver == DEV_FC, "set-home route mismatch");
        require(home->cmd_set == 0x03 && home->cmd_id == 0x31, "set-home command mismatch");
        require(home->payload.size() == 18 && home->payload[0] == 2 && home->payload[17] == 0,
                "set-home payload envelope mismatch");
        require(std::abs(read_f64(home->payload, 1) - 12.25 * std::acos(-1.0) / 180.0) < 1e-12,
                "set-home latitude order or units mismatch");
        require(std::abs(read_f64(home->payload, 9) - -73.5 * std::acos(-1.0) / 180.0) < 1e-12,
                "set-home longitude order or units mismatch");

        bool rejected = false;
        try {
            drone.set_home_point(91.0, 0.0);
        } catch (const std::invalid_argument&) {
            rejected = true;
        }
        require(rejected, "invalid home coordinate was not rejected");

        drone.return_to_home();
        auto rth = DumlPacket::decode(transport.frames.back());
        require(rth && rth->cmd_set == 0x03 && rth->cmd_id == 0x2A && rth->payload == Bytes{0x06},
                "RTH command mismatch");
        drone.cancel_rth();
        auto cancel = DumlPacket::decode(transport.frames.back());
        require(cancel && cancel->payload == Bytes{0x0C}, "cancel-RTH command mismatch");

    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 1;
    }
    return 0;
}
