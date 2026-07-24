#pragma once

#include <string>

namespace djilink::gui {

struct AppOptions {
    std::string pi;
    std::string serial;
    bool sim = false;
    bool dry = false;
    bool verbose = false;
    bool no_video = false;
    bool windowed = false;
};

int run_app(const AppOptions& opt);

} // namespace djilink::gui
