#include "core/param_hash.hpp"

namespace djilink {

std::uint32_t param_hash(const std::string& name) {
    constexpr std::uint64_t MOD = (1ULL << 32) - 5; // 0xFFFFFFFB
    std::uint64_t h = 0;
    for (unsigned char c : name) {
        h = (static_cast<std::uint64_t>(c) + (h << 8)) % MOD;
    }
    return static_cast<std::uint32_t>(h);
}

} // namespace djilink
