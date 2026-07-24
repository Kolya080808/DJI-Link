// DJI DUML CRC-8 / CRC-16 (reflected tables), ported from dji_link_beta/duml.py.
//
// CRC-8  : refl(0x31) == poly 0x8C, seed 0x77  — DUML header CRC.
// CRC-16 : refl(0x1021) == poly 0x8408, seed 0x3692 — DUML frame CRC.
// Verified against the first elements of known DJI tables (crc8[1]=0x5e,
// crc16[1]=0x1189).
#pragma once

#include <cstddef>
#include <cstdint>

namespace djilink {

inline constexpr std::uint8_t CRC8_SEED = 0x77;
inline constexpr std::uint16_t CRC16_SEED = 0x3692;

std::uint8_t crc8(const std::uint8_t* data, std::size_t len, std::uint8_t seed = CRC8_SEED);
std::uint16_t crc16(const std::uint8_t* data, std::size_t len, std::uint16_t seed = CRC16_SEED);

} // namespace djilink
