// DJI FC parameter-name hash (hashFromString), ported from param_hash.py.
// Base-256 polynomial hash reduced modulo the prime 2**32 - 5, seeded at 0.
// Used as the key in DUML 0x03/0xF8 (read) and 0x03/0xF9 (write) FC params.
#pragma once

#include <cstdint>
#include <string>

namespace djilink {

// The param names are ASCII, for which GBK (what the Java side encodes) is
// byte-identical to ASCII, so the std::string bytes are exactly what the native
// code hashes.
std::uint32_t param_hash(const std::string& name);

} // namespace djilink
