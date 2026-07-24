#include "core/updater.hpp"

#include "core/applog.hpp"

#include <array>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <vector>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#else
#include <limits.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace djilink::updater {
namespace {

#ifndef DJI_LINK_VERSION_STR
#define DJI_LINK_VERSION_STR "0.0.0"
#endif
#ifndef DJI_LINK_REPO
#define DJI_LINK_REPO "Kolya080808/DJI-Link"
#endif

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

// Parse "1.2.3" (or "v1.2.3-rc1") into up to three numeric components.
std::array<int, 3> parse_ver(std::string s) {
    if (!s.empty() && (s[0] == 'v' || s[0] == 'V'))
        s.erase(0, 1);
    std::array<int, 3> out{0, 0, 0};
    std::size_t idx = 0, start = 0;
    for (std::size_t i = 0; i <= s.size() && idx < 3; ++i) {
        if (i == s.size() || s[i] == '.' || s[i] == '-') {
            if (i > start) {
                try {
                    out[idx] = std::stoi(s.substr(start, i - start));
                } catch (...) {
                    out[idx] = 0;
                }
            }
            ++idx;
            start = i + 1;
            if (i < s.size() && s[i] == '-')
                break;
        }
    }
    return out;
}

bool newer(const std::string& a, const std::string& b) { // a > b ?
    auto va = parse_ver(a), vb = parse_ver(b);
    for (int i = 0; i < 3; ++i) {
        if (va[i] != vb[i])
            return va[i] > vb[i];
    }
    return false;
}

// Extract every `"browser_download_url": "..."` value from the GitHub JSON.
std::vector<std::string> extract_urls(const std::string& json) {
    std::vector<std::string> urls;
    const std::string key = "\"browser_download_url\"";
    std::size_t pos = 0;
    while ((pos = json.find(key, pos)) != std::string::npos) {
        std::size_t q1 = json.find('"', pos + key.size());
        if (q1 == std::string::npos)
            break;
        q1 = json.find('"', q1 + 1); // opening quote of the value
        if (q1 == std::string::npos)
            break;
        std::size_t q2 = json.find('"', q1 + 1);
        if (q2 == std::string::npos)
            break;
        urls.push_back(json.substr(q1 + 1, q2 - q1 - 1));
        pos = q2 + 1;
    }
    return urls;
}

std::string extract_field(const std::string& json, const std::string& name) {
    const std::string key = "\"" + name + "\"";
    std::size_t pos = json.find(key);
    if (pos == std::string::npos)
        return {};
    std::size_t colon = json.find(':', pos + key.size());
    if (colon == std::string::npos)
        return {};
    std::size_t q1 = json.find('"', colon);
    if (q1 == std::string::npos)
        return {};
    std::size_t q2 = json.find('"', q1 + 1);
    if (q2 == std::string::npos)
        return {};
    return json.substr(q1 + 1, q2 - q1 - 1);
}

std::string basename_of(const std::string& url) {
    auto p = url.find_last_of('/');
    return p == std::string::npos ? url : url.substr(p + 1);
}

bool ends_with(const std::string& s, const std::string& suf) {
    return s.size() >= suf.size() && s.compare(s.size() - suf.size(), suf.size(), suf) == 0;
}

// Rank an installer asset for THIS platform (higher = preferred; <0 = unusable).
int asset_score(const std::string& name) {
#if defined(_WIN32)
#if defined(_M_ARM64) || defined(__aarch64__)
    const char* arch = "arm64";
#elif defined(_WIN64) || defined(_M_X64) || defined(__x86_64__)
    const char* arch = "x64";
#else
    const char* arch = "x86";
#endif
    const bool arch_ok = name.find(arch) != std::string::npos;
    if (ends_with(name, ".msi"))
        return arch_ok ? 100 : 40;
    if (ends_with(name, ".zip"))
        return arch_ok ? 30 : 10;
    return -1;
#elif defined(__APPLE__)
    if (ends_with(name, ".dmg"))
        return 100;
    if (ends_with(name, ".tar.gz"))
        return 30;
    return -1;
#else
#if defined(__aarch64__)
    const char* arch = "arm64";
#else
    const char* arch = "x86_64";
#endif
    const bool arch_ok = name.find(arch) != std::string::npos;
    if (ends_with(name, ".deb"))
        return arch_ok ? 100 : 40;
    if (ends_with(name, ".rpm"))
        return arch_ok ? 90 : 35;
    if (ends_with(name, ".tar.gz"))
        return arch_ok ? 30 : 10;
    return -1;
#endif
}

int run_sync(const std::string& cmd) {
    return std::system(cmd.c_str());
}

void run_detached(const std::string& shell_cmd) {
#ifdef _WIN32
    std::string full = "cmd /c " + shell_cmd;
    STARTUPINFOA si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    std::vector<char> mut(full.begin(), full.end());
    mut.push_back('\0');
    if (CreateProcessA(nullptr, mut.data(), nullptr, nullptr, FALSE,
                       DETACHED_PROCESS | CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
#else
    // Run the helper backgrounded + detached from our stdio so it outlives us.
    run_sync("nohup sh -c \"" + shell_cmd + "\" >/dev/null 2>&1 &");
#endif
}

std::string quote(const std::string& s) {
#ifdef _WIN32
    return "\"" + s + "\"";
#else
    std::string out = "'";
    for (char c : s) {
        if (c == '\'')
            out += "'\\''";
        else
            out += c;
    }
    out += "'";
    return out;
#endif
}

} // namespace

std::string current_version() {
    return DJI_LINK_VERSION_STR;
}
std::string repo() {
    return DJI_LINK_REPO;
}

std::string temp_dir() {
    return (fs::temp_directory_path() / "dji-link-update").string();
}

void wipe_temp() {
    std::error_code ec;
    fs::remove_all(temp_dir(), ec);
}

std::optional<Release> check(std::string& err) {
    std::error_code ec;
    fs::create_directories(temp_dir(), ec);
    const std::string json_path = (fs::path(temp_dir()) / "latest.json").string();
    const std::string url = "https://api.github.com/repos/" + repo() + "/releases/latest";
    const std::string cmd = "curl -fsSL --retry 2 -H \"Accept: application/vnd.github+json\" " +
                            quote(url) + " -o " + quote(json_path);
    if (run_sync(cmd) != 0) {
        err = "network error contacting GitHub (is curl available / are you online?)";
        return std::nullopt;
    }
    std::ifstream f(json_path, std::ios::binary);
    std::stringstream ss;
    ss << f.rdbuf();
    const std::string json = ss.str();
    if (json.empty()) {
        err = "empty response from GitHub";
        return std::nullopt;
    }
    const std::string tag = extract_field(json, "tag_name");
    if (tag.empty()) {
        err = "no releases published yet";
        return std::nullopt;
    }
    std::string version = tag;
    if (!version.empty() && (version[0] == 'v' || version[0] == 'V'))
        version.erase(0, 1);
    if (!newer(version, current_version())) {
        err = "already up to date (v" + current_version() + ")";
        return std::nullopt;
    }
    // pick the best installer asset for this platform
    std::string best_url;
    int best = -1;
    for (const auto& u : extract_urls(json)) {
        const std::string name = basename_of(u);
        const int sc = asset_score(name);
        if (sc > best) {
            best = sc;
            best_url = u;
        }
    }
    if (best < 0 || best_url.empty()) {
        err = "release v" + version + " has no installer for this platform";
        return std::nullopt;
    }
    Release r;
    r.tag = tag;
    r.version = version;
    r.asset_url = best_url;
    r.asset_name = basename_of(best_url);
    r.prerelease = extract_field(json, "prerelease") == "true";
    return r;
}

std::optional<std::string> download(const Release& r, std::string& err) {
    std::error_code ec;
    fs::create_directories(temp_dir(), ec);
    const std::string dest = (fs::path(temp_dir()) / r.asset_name).string();
    const std::string cmd = "curl -fL --retry 2 " + quote(r.asset_url) + " -o " + quote(dest);
    if (run_sync(cmd) != 0) {
        err = "failed to download " + r.asset_name;
        return std::nullopt;
    }
    applog::info("[update] downloaded " + dest);
    return dest;
}

bool install_and_relaunch(const std::string& installer, std::string& err) {
    const std::string self = self_path();
#if defined(_WIN32)
    // Exit first (a running .exe can't be replaced), so the helper waits ~1s, runs
    // the MSI silently, then relaunches us.
    if (ends_with(installer, ".msi")) {
        std::string cmd = "\"ping -n 2 127.0.0.1 >nul & msiexec /i " + quote(installer) +
                          " /qb & start \"\" " + quote(self) + "\"";
        run_detached(cmd);
        return true;
    }
    err = "unsupported installer type for auto-install: " + installer;
    return false;
#elif defined(__APPLE__)
    // Mount the .dmg, copy the .app into /Applications, then reopen it.
    std::string mp = "/tmp/dji-link-update-mnt";
    std::string cmd = "sleep 1; hdiutil attach " + quote(installer) + " -nobrowse -mountpoint " +
                      quote(mp) + "; cp -R " + mp + "/*.app /Applications/ 2>/dev/null; " +
                      "hdiutil detach " + quote(mp) + " >/dev/null 2>&1; open -a \"dji-link\" || " +
                      "open " + quote(self);
    run_detached(cmd);
    return true;
#else
    // Install the .deb/.rpm via a privileged helper (pkexec/sudo), else hand off to
    // the desktop's software installer, then relaunch.
    std::string install;
    if (ends_with(installer, ".deb"))
        install = "pkexec dpkg -i " + quote(installer) + " || sudo dpkg -i " + quote(installer) +
                  " || xdg-open " + quote(installer);
    else if (ends_with(installer, ".rpm"))
        install = "pkexec rpm -U " + quote(installer) + " || sudo rpm -U " + quote(installer) +
                  " || xdg-open " + quote(installer);
    else {
        err = "unsupported installer type for auto-install: " + installer;
        return false;
    }
    std::string cmd = "sleep 1; " + install + "; " + quote(self);
    run_detached(cmd);
    return true;
#endif
}

void discard(const std::string& installer) {
    std::error_code ec;
    fs::remove(installer, ec);
}

} // namespace djilink::updater
