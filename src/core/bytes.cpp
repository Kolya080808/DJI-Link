#include "core/bytes.hpp"

namespace djilink {

std::string to_hex(const std::uint8_t* data, std::size_t len) {
    static const char* kDigits = "0123456789abcdef";
    std::string out;
    out.reserve(len * 2);
    for (std::size_t i = 0; i < len; ++i) {
        out.push_back(kDigits[(data[i] >> 4) & 0xF]);
        out.push_back(kDigits[data[i] & 0xF]);
    }
    return out;
}

std::optional<Bytes> from_hex(const std::string& s) {
    if (s.size() % 2 != 0)
        return std::nullopt;
    auto nib = [](char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    Bytes out;
    out.reserve(s.size() / 2);
    for (std::size_t i = 0; i < s.size(); i += 2) {
        int hi = nib(s[i]), lo = nib(s[i + 1]);
        if (hi < 0 || lo < 0)
            return std::nullopt;
        out.push_back(static_cast<std::uint8_t>((hi << 4) | lo));
    }
    return out;
}

std::optional<std::uint8_t> get_u8(const Bytes& b, std::size_t off) {
    if (off >= b.size())
        return std::nullopt;
    return b[off];
}
std::optional<std::int8_t> get_s8(const Bytes& b, std::size_t off) {
    if (off >= b.size())
        return std::nullopt;
    return static_cast<std::int8_t>(b[off]);
}
std::optional<std::uint16_t> get_u16(const Bytes& b, std::size_t off) {
    if (off + 2 > b.size())
        return std::nullopt;
    return static_cast<std::uint16_t>(b[off] | (b[off + 1] << 8));
}
std::optional<std::int16_t> get_s16(const Bytes& b, std::size_t off) {
    auto v = get_u16(b, off);
    if (!v)
        return std::nullopt;
    return static_cast<std::int16_t>(*v);
}
std::optional<std::uint32_t> get_u32(const Bytes& b, std::size_t off) {
    if (off + 4 > b.size())
        return std::nullopt;
    return static_cast<std::uint32_t>(b[off]) | (static_cast<std::uint32_t>(b[off + 1]) << 8) |
           (static_cast<std::uint32_t>(b[off + 2]) << 16) |
           (static_cast<std::uint32_t>(b[off + 3]) << 24);
}
std::optional<std::int32_t> get_s32(const Bytes& b, std::size_t off) {
    auto v = get_u32(b, off);
    if (!v)
        return std::nullopt;
    return static_cast<std::int32_t>(*v);
}
std::optional<float> get_f32(const Bytes& b, std::size_t off) {
    auto v = get_u32(b, off);
    if (!v)
        return std::nullopt;
    float f;
    std::uint32_t u = *v;
    std::memcpy(&f, &u, 4);
    return f;
}
std::optional<double> get_f64(const Bytes& b, std::size_t off) {
    if (off + 8 > b.size())
        return std::nullopt;
    std::uint64_t u = 0;
    for (int i = 0; i < 8; ++i)
        u |= static_cast<std::uint64_t>(b[off + i]) << (8 * i);
    double d;
    std::memcpy(&d, &u, 8);
    return d;
}

} // namespace djilink
