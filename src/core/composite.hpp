// DUSS composite-mux demux for the raw AOA stream, ported from composite.py.
//
// Raw bulk = a stream of units: [0]=0x55 [1]=0xCC | type u16 LE | len u32 LE | payload.
// Routing by type: 0x5749 = DUML, 0x574A/0x574D = video, others reported via on_unit.
#pragma once

#include "core/bytes.hpp"

#include <cstdint>
#include <functional>
#include <map>

namespace djilink {

inline constexpr std::uint8_t COMPOSITE_SOF0 = 0x55;
inline constexpr std::uint8_t COMPOSITE_SOF1 = 0xCC;
inline constexpr std::uint16_t COMPOSITE_TYPE_DUML = 0x5749;

// Wrap a payload (e.g. a DUML frame) into a composite unit for sending over AOA.
Bytes composite_wrap(const Bytes& payload, std::uint16_t typ = COMPOSITE_TYPE_DUML);

class CompositeDemux {
public:
    using DumlCb = std::function<void(const Bytes&)>;
    using VideoCb = std::function<void(const Bytes&)>;
    using UnitCb = std::function<void(std::uint16_t, const Bytes&)>;

    CompositeDemux(DumlCb on_duml, VideoCb on_video, UnitCb on_unit,
                   std::size_t max_unit = 0x200000);

    void feed(const Bytes& data);
    void feed(const std::uint8_t* data, std::size_t len);

    std::size_t units() const {
        return units_;
    }
    const std::map<std::uint16_t, std::size_t>& type_counts() const {
        return type_counts_;
    }

private:
    Bytes buf_;
    DumlCb on_duml_;
    VideoCb on_video_;
    UnitCb on_unit_;
    std::size_t max_unit_;
    std::size_t units_ = 0;
    std::map<std::uint16_t, std::size_t> type_counts_;
};

} // namespace djilink
