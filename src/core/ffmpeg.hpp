// ffmpeg runtime lookup.
//
// The WM160 liveview is H.265/HEVC. The GUI feeds Annex-B HEVC to an ffmpeg
// process and displays raw RGB frames inside the SDL window. The release
// installers bundle/install ffmpeg; application code never installs it.
#pragma once

#include <string>

namespace djilink::ffmpeg {

// Prefer the binary installed by the package/installer; fall back to PATH for
// developer builds.
std::string executable();
bool available();

} // namespace djilink::ffmpeg
