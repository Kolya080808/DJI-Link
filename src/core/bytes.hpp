// Small byte-buffer helpers: the C++ stand-ins for Python's bytes/struct/.hex().
#pragma once

#include <cstdint>
#include <cstring>
#include <optional>
#include <string>
#include <vector>

namespace djilink {

using Bytes = std::vector<std::uint8_t>;

// ---- hex <-> bytes (Python bytes.hex() / bytes.fromhex()) ----
std::string to_hex(const std::uint8_t* data, std::size_t len);
inline std::string to_hex(const Bytes& b) {
    return to_hex(b.data(), b.size());
}
// Parse a hex string; std::nullopt if it is not valid hex (odd length / bad char).
std::optional<Bytes> from_hex(const std::string& s);

// ---- little-endian writers (struct.pack("<...")) ----
inline void put_u8(Bytes& b, std::uint8_t v) {
    b.push_back(v);
}
inline void put_u16(Bytes& b, std::uint16_t v) {
    b.push_back(static_cast<std::uint8_t>(v & 0xFF));
    b.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));
}
inline void put_u32(Bytes& b, std::uint32_t v) {
    for (int i = 0; i < 4; ++i)
        b.push_back(static_cast<std::uint8_t>((v >> (8 * i)) & 0xFF));
}
inline void put_f32(Bytes& b, float v) {
    std::uint32_t u;
    std::memcpy(&u, &v, 4);
    put_u32(b, u);
}
inline void put_f64(Bytes& b, double v) {
    std::uint64_t u;
    std::memcpy(&u, &v, 8);
    for (int i = 0; i < 8; ++i)
        b.push_back(static_cast<std::uint8_t>((u >> (8 * i)) & 0xFF));
}

// ---- little-endian readers, bounds-checked (return nullopt past the end) ----
// Mirror telemetry.py's u8/s8/u16/s16/u32/s32 helpers.
std::optional<std::uint8_t> get_u8(const Bytes& b, std::size_t off);
std::optional<std::int8_t> get_s8(const Bytes& b, std::size_t off);
std::optional<std::uint16_t> get_u16(const Bytes& b, std::size_t off);
std::optional<std::int16_t> get_s16(const Bytes& b, std::size_t off);
std::optional<std::uint32_t> get_u32(const Bytes& b, std::size_t off);
std::optional<std::int32_t> get_s32(const Bytes& b, std::size_t off);
std::optional<float> get_f32(const Bytes& b, std::size_t off);
std::optional<double> get_f64(const Bytes& b, std::size_t off);

} // namespace djilink
