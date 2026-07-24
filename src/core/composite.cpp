#include "core/composite.hpp"

namespace djilink {
namespace {
constexpr std::uint16_t kVideoA = 0x574A;
constexpr std::uint16_t kVideoD = 0x574D;
} // namespace

Bytes composite_wrap(const Bytes& payload, std::uint16_t typ) {
    Bytes out;
    out.reserve(8 + payload.size());
    out.push_back(COMPOSITE_SOF0);
    out.push_back(COMPOSITE_SOF1);
    put_u16(out, typ);
    put_u32(out, static_cast<std::uint32_t>(payload.size()));
    out.insert(out.end(), payload.begin(), payload.end());
    return out;
}

CompositeDemux::CompositeDemux(DumlCb on_duml, VideoCb on_video, UnitCb on_unit,
                               std::size_t max_unit)
    : on_duml_(std::move(on_duml)), on_video_(std::move(on_video)), on_unit_(std::move(on_unit)),
      max_unit_(max_unit) {}

void CompositeDemux::feed(const std::uint8_t* data, std::size_t len) {
    buf_.insert(buf_.end(), data, data + len);
    while (true) {
        if (buf_.size() < 8)
            break;
        if (!(buf_[0] == COMPOSITE_SOF0 && buf_[1] == COMPOSITE_SOF1)) {
            // resync: look for the next 55 CC starting at index 1
            std::size_t idx = std::string::npos;
            for (std::size_t i = 1; i + 1 < buf_.size(); ++i) {
                if (buf_[i] == COMPOSITE_SOF0 && buf_[i + 1] == COMPOSITE_SOF1) {
                    idx = i;
                    break;
                }
            }
            if (idx == std::string::npos) {
                const std::size_t keep = (buf_.back() == COMPOSITE_SOF0) ? 1 : 0; // partial SOF
                buf_.erase(buf_.begin(), buf_.end() - static_cast<std::ptrdiff_t>(keep));
                break;
            }
            buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(idx));
            continue;
        }
        const std::uint16_t typ = static_cast<std::uint16_t>(buf_[2] | (buf_[3] << 8));
        const std::size_t length =
            static_cast<std::size_t>(buf_[4]) | (static_cast<std::size_t>(buf_[5]) << 8) |
            (static_cast<std::size_t>(buf_[6]) << 16) | (static_cast<std::size_t>(buf_[7]) << 24);
        if (length > max_unit_) {
            buf_.erase(buf_.begin()); // garbage — shift
            continue;
        }
        const std::size_t total = 8 + length;
        if (buf_.size() < total)
            break; // wait for the rest (split between reads)
        Bytes payload(buf_.begin() + 8, buf_.begin() + static_cast<std::ptrdiff_t>(total));
        buf_.erase(buf_.begin(), buf_.begin() + static_cast<std::ptrdiff_t>(total));
        ++units_;
        type_counts_[typ] += 1;
        if (on_unit_)
            on_unit_(typ, payload);
        if (typ == COMPOSITE_TYPE_DUML && on_duml_) {
            on_duml_(payload);
        } else if ((typ == kVideoA || typ == kVideoD) && on_video_) {
            on_video_(payload);
        }
    }
}

void CompositeDemux::feed(const Bytes& data) {
    feed(data.data(), data.size());
}

} // namespace djilink
