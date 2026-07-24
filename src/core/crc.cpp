#include "core/crc.hpp"

#include <array>

namespace djilink {
namespace {

// Reflected (LSB-first) CRC table of 256 values, matching duml.py::_gen_table.
template <typename T> std::array<T, 256> gen_table(T poly) {
    std::array<T, 256> table{};
    for (int i = 0; i < 256; ++i) {
        T c = static_cast<T>(i);
        for (int k = 0; k < 8; ++k) {
            c = (c & 1) ? static_cast<T>((c >> 1) ^ poly) : static_cast<T>(c >> 1);
        }
        table[static_cast<std::size_t>(i)] = c;
    }
    return table;
}

const std::array<std::uint8_t, 256> kCrc8 = gen_table<std::uint8_t>(0x8C);      // refl(0x31)
const std::array<std::uint16_t, 256> kCrc16 = gen_table<std::uint16_t>(0x8408); // refl(0x1021)

} // namespace

std::uint8_t crc8(const std::uint8_t* data, std::size_t len, std::uint8_t seed) {
    std::uint8_t c = seed;
    for (std::size_t i = 0; i < len; ++i) {
        c = kCrc8[(c ^ data[i]) & 0xFF];
    }
    return c;
}

std::uint16_t crc16(const std::uint8_t* data, std::size_t len, std::uint16_t seed) {
    std::uint16_t c = seed;
    for (std::size_t i = 0; i < len; ++i) {
        c = static_cast<std::uint16_t>((c >> 8) ^ kCrc16[(c ^ data[i]) & 0xFF]);
    }
    return c;
}

} // namespace djilink
