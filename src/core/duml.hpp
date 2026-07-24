// DUML transport codec (DJI Universal Markup Language), ported from
// dji_link_beta/duml.py. Frame layout, CRCs and the SIMPLE cipher are all
// verified against real WM160 frames — see the docstring in duml.py.
#pragma once

#include "core/bytes.hpp"

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace djilink {

inline constexpr std::uint8_t DUML_MAGIC = 0x55;

// One decoded DUML packet. Field defaults match duml.py's DumlPacket.
struct DumlPacket {
    std::uint8_t sender = 0;
    std::uint8_t receiver = 0;
    std::uint8_t cmd_set = 0;
    std::uint8_t cmd_id = 0;
    Bytes payload;
    std::uint16_t seq = 0;
    std::uint8_t cmd_type = 0x00; // 0x40 = ACK required; 0x00 = normal
    std::uint8_t version = 1;     // real DJI frames use version 1

    // Encode to a wire frame. Throws std::length_error if the frame exceeds 1023 bytes.
    Bytes encode() const;

    // Decode a whole frame; std::nullopt if it is malformed (bad magic/length/CRC).
    static std::optional<DumlPacket> decode(const Bytes& frame);

    std::string str() const;
};

// --- DUML "SIMPLE" encryption (cmd_type 0x43) for FLYC config/param frames ---
// Self-inverse keystream XOR reversed from libGroudStation.so (see duml.py).
Bytes simple_filter(const Bytes& buf, std::uint16_t seq);
Bytes encrypt_frame(const Bytes& frame); // plaintext 0x40 frame -> SIMPLE 0x43 frame
Bytes decrypt_frame(const Bytes& frame); // inverse for 0x43/0xC3 frames

// Accumulator of bulk-channel bytes -> whole DUML frames. AOA does not guarantee
// one read == one frame, so we cut the stream by magic 0x55 + the header length.
class DumlStream {
public:
    std::vector<DumlPacket> feed(const Bytes& data);
    std::vector<DumlPacket> feed(const std::uint8_t* data, std::size_t len);

private:
    Bytes buf_;
};

} // namespace djilink
