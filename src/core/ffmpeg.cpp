#include "core/ffmpeg.hpp"

#include <filesystem>
#include <string>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#elif defined(__APPLE__)
#include <limits.h>
#include <mach-o/dyld.h>
#else
#include <limits.h>
#include <unistd.h>
#endif

namespace djilink::ffmpeg {
namespace {

namespace fs = std::filesystem;

std::string self_path() {
#ifdef _WIN32
    char buf[MAX_PATH];
    DWORD n = GetModuleFileNameA(nullptr, buf, MAX_PATH);
    return std::string(buf, n);
#elif defined(__APPLE__)
    char buf[PATH_MAX];
    uint32_t sz = sizeof(buf);
    if (_NSGetExecutablePath(buf, &sz) == 0)
        return std::string(buf);
    return {};
#else
    char buf[PATH_MAX];
    ssize_t n = ::readlink("/proc/self/exe", buf, sizeof(buf));
    return n > 0 ? std::string(buf, static_cast<std::size_t>(n)) : std::string();
#endif
}

bool exists_file(const fs::path& p) {
    std::error_code ec;
    return fs::exists(p, ec) && fs::is_regular_file(p, ec);
}

std::string exe_name() {
#ifdef _WIN32
    return "ffmpeg.exe";
#else
    return "ffmpeg";
#endif
}

} // namespace

std::string executable() {
    const fs::path self = self_path();
    const fs::path dir = self.empty() ? fs::current_path() : self.parent_path();
    const std::string name = exe_name();

    const fs::path same_dir = dir / name;
    if (exists_file(same_dir))
        return same_dir.string();

#ifdef _WIN32
    const fs::path tools = dir / "tools" / name;
    if (exists_file(tools))
        return tools.string();
#elif defined(__APPLE__)
    // App bundle layout:
    // dji-link.app/Contents/MacOS/dji-link
    // dji-link.app/Contents/Resources/bin/ffmpeg
    const fs::path bundle = dir.parent_path() / "Resources" / "bin" / name;
    if (exists_file(bundle))
        return bundle.string();
#endif

    return name; // PATH fallback for local/dev builds.
}

bool available() {
    const auto exe = executable();
    if (exe == exe_name())
        return true; // PATH fallback; process launch reports the actual failure.
    return exists_file(exe);
}

} // namespace djilink::ffmpeg
