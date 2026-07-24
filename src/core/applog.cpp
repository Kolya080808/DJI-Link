#include "core/applog.hpp"

#include <chrono>
#include <cstdio>
#include <ctime>
#include <deque>
#include <filesystem>
#include <fstream>
#include <mutex>

namespace fs = std::filesystem;

namespace djilink::applog {
namespace {

std::mutex g_mu;
std::deque<std::string> g_tail; // maxlen 400
constexpr std::size_t kTailMax = 400;
std::ofstream g_file;
fs::path g_dir;
fs::path g_latest;
bool g_verbose = false;
bool g_configured = false;

std::string now_hms() {
    const std::time_t t = std::time(nullptr);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[16];
    std::strftime(buf, sizeof(buf), "%H:%M:%S", &tm);
    return buf;
}

std::string stamp_from_time(std::time_t t) {
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%d_%H-%M-%S", &tm);
    return buf;
}

// Rename an existing latest.log to a dated file, named by its last write time.
void archive_previous() {
    std::error_code ec;
    if (!fs::exists(g_latest, ec))
        return;
    std::time_t mtime = std::time(nullptr);
    auto ft = fs::last_write_time(g_latest, ec);
    if (!ec) {
        const auto sys = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
            ft - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
        mtime = std::chrono::system_clock::to_time_t(sys);
    }
    std::string base = stamp_from_time(mtime);
    fs::path target = g_dir / (base + ".log");
    int n = 1;
    while (fs::exists(target, ec)) {
        target = g_dir / (base + "_" + std::to_string(n++) + ".log");
    }
    fs::rename(g_latest, target, ec); // if it fails we just overwrite latest.log below
}

// Delete dated archives older than KEEP_DAYS. Never touches latest.log.
void cleanup_old() {
    std::error_code ec;
    const auto cutoff = std::chrono::system_clock::now() - std::chrono::hours(24 * KEEP_DAYS);
    for (auto& e : fs::directory_iterator(g_dir, ec)) {
        if (ec)
            break;
        if (e.path().extension() != ".log")
            continue;
        if (e.path().filename() == "latest.log")
            continue;
        std::error_code ec2;
        auto ft = fs::last_write_time(e.path(), ec2);
        if (ec2)
            continue;
        const auto sys = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
            ft - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
        if (sys < cutoff)
            fs::remove(e.path(), ec2);
    }
}

void write_line(const std::string& level, const std::string& line, bool to_tail) {
    std::lock_guard<std::mutex> lk(g_mu);
    if (g_file.is_open()) {
        g_file << now_hms() << " " << level << " " << line << "\n";
        g_file.flush();
    }
    if (to_tail) {
        g_tail.push_back(now_hms() + " " + line);
        while (g_tail.size() > kTailMax)
            g_tail.pop_front();
    }
}

} // namespace

void setup(bool verbose) {
    std::lock_guard<std::mutex> lk(g_mu);
    if (g_configured)
        return;
    g_verbose = verbose;
    g_dir = fs::current_path() / "logs";
    g_latest = g_dir / "latest.log";
    std::error_code ec;
    fs::create_directories(g_dir, ec);
    archive_previous();
    cleanup_old();
    g_file.open(g_latest, std::ios::out | std::ios::trunc);
    g_configured = true;
    if (g_file.is_open()) {
        const std::time_t t = std::time(nullptr);
        std::tm tm{};
#ifdef _WIN32
        localtime_s(&tm, &t);
#else
        localtime_r(&t, &tm);
#endif
        char buf[32];
        std::strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", &tm);
        g_file << now_hms() << " INFO    === log start " << buf << " (keep " << KEEP_DAYS
               << " days) ===\n";
        g_file.flush();
    }
}

void info(const std::string& line) {
    write_line("INFO   ", line, true);
}
void debug(const std::string& line) {
    write_line("DEBUG  ", line, g_verbose);
}

std::vector<std::string> tail() {
    std::lock_guard<std::mutex> lk(g_mu);
    return {g_tail.begin(), g_tail.end()};
}

std::string latest_path() {
    return g_latest.string();
}

} // namespace djilink::applog
