// In-app auto-updater, pulling installers from the project's GitHub Releases.
// See memory: cpp-auto-update. Flow driven by the GUI "Check for updates" button:
//   startup: wipe_temp()  ->  check()  ->  download()  ->  install_and_relaunch()
//                                                     \->  discard()
// HTTPS is done via the system `curl` (present on Win10+/Linux/macOS), so we link
// no TLS library; the platform installer is launched via a small detached helper.
#pragma once

#include <optional>
#include <string>

namespace djilink::updater {

// The running app version (from DJI_LINK_VERSION_STR) and target repo.
std::string current_version();
std::string repo();

// Delete leftover installers from the updater's temp directory. Call on startup.
void wipe_temp();
std::string temp_dir();

struct Release {
    std::string tag;        // e.g. "v1.2.0"
    std::string version;    // "1.2.0"
    std::string asset_name; // installer filename for THIS platform
    std::string asset_url;  // browser_download_url
    bool prerelease = false;
};

// Query the latest GitHub release; returns it only if it is NEWER than the running
// version and has an installer asset for this platform. `err` gets a message on failure.
std::optional<Release> check(std::string& err);

// Download the release's installer into temp_dir(); returns the local path.
std::optional<std::string> download(const Release& r, std::string& err);

// Launch the installer and, once it finishes, relaunch this app; on success the
// caller should quit immediately (the detached helper does the install + restart).
bool install_and_relaunch(const std::string& installer_path, std::string& err);

// User said "No": remove the downloaded installer.
void discard(const std::string& installer_path);

} // namespace djilink::updater
