#include "core/duml.hpp"

#include "core/crc.hpp"

#include <array>
#include <cstdio>
#include <stdexcept>

namespace djilink {
namespace {

// Static SIMPLE key: 21 hex bytes + a trailing NUL = 22 bytes (duml.py::_SIMPLE_KEY).
const std::array<std::uint8_t, 22> kSimpleKey = {0x78, 0x4f, 0x24, 0x33, 0x28, 0x2d, 0x32, 0x40,
                                                 0x23, 0x6c, 0x64, 0x2a, 0x76, 0x69, 0x41, 0x51,
                                                 0x7e, 0x69, 0x78, 0x46, 0x45, 0x00};

} // namespace

Bytes DumlPacket::encode() const {
    Bytes body;
    body.reserve(7 + payload.size());
    body.push_back(sender);
    body.push_back(receiver);
    body.push_back(static_cast<std::uint8_t>(seq & 0xFF));
    body.push_back(static_cast<std::uint8_t>((seq >> 8) & 0xFF));
    body.push_back(cmd_type);
    body.push_back(cmd_set);
    body.push_back(cmd_id);
    body.insert(body.end(), payload.begin(), payload.end());

    const std::size_t total = 1 + 2 + 1 + body.size() + 2; // magic+len2+crc8+body+crc16
    if (total > 0x3FF) {
        throw std::length_error("DUML frame too long (max 1023 bytes)");
    }
    const std::uint16_t len_ver =
        static_cast<std::uint16_t>((total & 0x3FF) | ((version & 0x3F) << 10));

    Bytes frame;
    frame.reserve(total);
    frame.push_back(DUML_MAGIC);
    frame.push_back(static_cast<std::uint8_t>(len_ver & 0xFF));
    frame.push_back(static_cast<std::uint8_t>((len_ver >> 8) & 0xFF));
    frame.push_back(crc8(frame.data(), 3));
    frame.insert(frame.end(), body.begin(), body.end());
    const std::uint16_t c = crc16(frame.data(), frame.size());
    frame.push_back(static_cast<std::uint8_t>(c & 0xFF));
    frame.push_back(static_cast<std::uint8_t>((c >> 8) & 0xFF));
    return frame;
}

std::optional<DumlPacket> DumlPacket::decode(const Bytes& frame) {
    if (frame.size() < 13)
        return std::nullopt;
    if (frame[0] != DUML_MAGIC)
        return std::nullopt;
    const std::uint16_t len_ver = static_cast<std::uint16_t>(frame[1] | (frame[2] << 8));
    const std::size_t total = len_ver & 0x3FF;
    const std::uint8_t version = static_cast<std::uint8_t>((len_ver >> 10) & 0x3F);
    if (crc8(frame.data(), 3) != frame[3])
        return std::nullopt;
    if (total != frame.size())
        return std::nullopt;
    const std::uint16_t got = crc16(frame.data(), frame.size() - 2);
    const std::uint16_t want =
        static_cast<std::uint16_t>(frame[frame.size() - 2] | (frame[frame.size() - 1] << 8));
    if (got != want)
        return std::nullopt;

    DumlPacket p;
    p.sender = frame[4];
    p.receiver = frame[5];
    p.seq = static_cast<std::uint16_t>(frame[6] | (frame[7] << 8));
    p.cmd_type = frame[8];
    p.cmd_set = frame[9];
    p.cmd_id = frame[10];
    p.payload.assign(frame.begin() + 11, frame.end() - 2);
    p.version = version;
    return p;
}

std::string DumlPacket::str() const {
    char head[128];
    std::snprintf(head, sizeof(head),
                  "DUML seq=%u 0x%02x->0x%02x set=0x%02x id=0x%02x type=0x%02x len=%zu data=", seq,
                  sender, receiver, cmd_set, cmd_id, cmd_type, payload.size());
    return std::string(head) + to_hex(payload);
}

Bytes simple_filter(const Bytes& buf, std::uint16_t seq) {
    Bytes out(buf.size());
    int keyidx = 1;
    const std::uint8_t slo = static_cast<std::uint8_t>(seq & 0xFF);
    const std::uint8_t shi = static_cast<std::uint8_t>((seq >> 8) & 0xFF);
    for (std::size_t i = 0; i < buf.size(); ++i) {
        if (keyidx >= 22)
            keyidx = 0;
        const std::uint8_t mix = (i & 1) ? shi : slo;
        out[i] =
            static_cast<std::uint8_t>(kSimpleKey[static_cast<std::size_t>(keyidx)] ^ buf[i] ^ mix);
        keyidx = (static_cast<int>((i + 1) & 0xF)) ^ (keyidx + 1);
    }
    return out;
}

Bytes encrypt_frame(const Bytes& frame) {
    Bytes f = frame;
    const std::size_t n = f.size();
    const std::uint16_t seq = static_cast<std::uint16_t>(f[6] | (f[7] << 8));
    Bytes region(f.begin() + 9, f.begin() + (n - 2));
    Bytes enc = simple_filter(region, seq);
    std::copy(enc.begin(), enc.end(), f.begin() + 9);
    f[8] |= 0x03; // EncryptType SIMPLE -> cmd_type 0x40 becomes 0x43
    const std::uint16_t c = crc16(f.data(), n - 2);
    f[n - 2] = static_cast<std::uint8_t>(c & 0xFF);
    f[n - 1] = static_cast<std::uint8_t>((c >> 8) & 0xFF);
    return f;
}

Bytes decrypt_frame(const Bytes& frame) {
    Bytes f = frame;
    const std::size_t n = f.size();
    const std::uint16_t seq = static_cast<std::uint16_t>(f[6] | (f[7] << 8));
    Bytes region(f.begin() + 9, f.begin() + (n - 2));
    Bytes dec = simple_filter(region, seq);
    std::copy(dec.begin(), dec.end(), f.begin() + 9);
    f[8] &= static_cast<std::uint8_t>(~0x07); // clear EncryptType bits
    const std::uint16_t c = crc16(f.data(), n - 2);
    f[n - 2] = static_cast<std::uint8_t>(c & 0xFF);
    f[n - 1] = static_cast<std::uint8_t>((c >> 8) & 0xFF);
    return f;
}

std::vector<DumlPacket> DumlStream::feed(const std::uint8_t* data, std::size_t len) {
    buf_.insert(buf_.end(), data, data + len);
    std::vector<DumlPacket> out;
    while (true) {
        // discard garbage up to the nearest magic
        std::size_t start = 0;
        bool found = false;
        for (; start < buf_.size(); ++start) {
            if (buf_[start] == DUML_MAGIC) {
                found = true;
                break;
            }
        }
        if (!found) {
            buf_.clear();
            break;
        }
        if (start > 0)
            buf_.erase(buf_.begin(), buf_.begin() + start);
        if (buf_.size() < 4)
            break;
        if (crc8(buf_.data(), 3) != buf_[3]) {
            buf_.erase(buf_.begin()); // false magic — shift by 1
            continue;
        }
        const std::size_t total = (buf_[1] | (buf_[2] << 8)) & 0x3FF;
        if (total < 13 || total > 0x3FF) {
            buf_.erase(buf_.begin());
            continue;
        }
        if (buf_.size() < total)
            break; // wait for the rest of the frame
        Bytes frame(buf_.begin(), buf_.begin() + total);
        buf_.erase(buf_.begin(), buf_.begin() + total);
        // SIMPLE-encrypted config replies — decrypt first.
        if (frame[8] & 0x07) {
            frame = decrypt_frame(frame);
        }
        if (auto p = DumlPacket::decode(frame)) {
            out.push_back(std::move(*p));
        }
    }
    return out;
}

std::vector<DumlPacket> DumlStream::feed(const Bytes& data) {
    return feed(data.data(), data.size());
}

} // namespace djilink
