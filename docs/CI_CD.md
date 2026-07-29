# CI/CD

Automated build, checks, and releases for the C++ version of DJI Link on GitHub Actions.
All C++ code lives at the repository root (the `dji_link_beta/` folder is the old Python
beta; CI ignores it).

## TL;DR

| What | File | When it runs |
|------|------|--------------|
| **CI** — compile-check + tests on **every release platform** | `.github/workflows/ci.yml` | push to `main` / PR, **only when C++/CMake changed** |
| **Lint** — formatting (clang-format) | `.github/workflows/lint.yml` | same, C++ only |
| **Release** — binaries/installers + checksums | `.github/workflows/release.yml` | **only on a git tag `vX.Y.Z`**, and only if `UPDATE.md` exists |

Small edits, feature experiments, and changes to the Python beta do **not** trigger heavy builds.

CI's build matrix **mirrors the release matrix 1:1** (same runners and architectures —
Linux x86_64/arm64/x86, macOS arm64/x86_64, Windows x64/arm64/x86), so a platform can
never build for the first time only during a tagged release. CI stops at build + `ctest`;
packaging, ffmpeg bundling and installers stay in `release.yml`. The best-effort targets
(32-bit and Windows-arm64) are `continue-on-error` in both, exactly like the release
`experimental` jobs. **Keep the two matrices in sync when you add or drop a platform.**

## How to cut a release

1. Create/edit **`UPDATE.md`** at the repo root: `title`, `version`, `prerelease`, and the changelog.
   **No `UPDATE.md` = no release:** if the file is absent when
   the tag is pushed, the workflow skips gracefully (a green run, nothing published). Add `UPDATE.md`
   and push the tag again to release.
2. Commit it.
3. Tag with the same version and push:
   ```bash
   git tag v1.2.0
   git push origin v1.2.0
   ```

`release.yml` then:

- reads `UPDATE.md` and **verifies its `version` matches the tag** (otherwise the release
  fails — this guards against a version / changelog mismatch);
- builds and packages every platform (see the matrix below);
- packages the SDL2 GUI by default and bundles the `ffmpeg` video runtime;
- computes `SHA256SUMS.txt`;
- publishes a GitHub Release with the title and body from `UPDATE.md`
  (`prerelease: true` marks it as a pre-release).

A tag like `v1.2.0-rc1` + `prerelease: true` produces a release candidate.

## Build matrix

| Platform | Runner | Artifacts |
|----------|--------|-----------|
| Linux x86_64 | `ubuntu-22.04` | `.tar.gz`, `.deb`, `.rpm` |
| Linux arm64 | `ubuntu-24.04-arm` | `.tar.gz`, `.deb`, `.rpm` |
| Linux x86 (32-bit) | `ubuntu-22.04` | `.tar.gz` — *best-effort*¹ |
| macOS arm64 | `macos-14` | `.dmg`, `.tar.gz` |
| macOS x86_64 | `macos-15-intel` | `.dmg`, `.tar.gz` |
| Windows x64 | `windows-latest` | `.msi` (WiX installer), `.zip` |
| Windows arm64 | `windows-latest` (cross-compiled) | `.msi`, `.zip` — *best-effort*¹ (built via the MSVC `amd64_arm64` toolchain because only the x64 runner has WiX) |
| Windows x86 (32-bit) | `windows-latest` | `.msi`, `.zip` — *best-effort*¹ |

`.deb` covers Debian/Ubuntu, `.rpm` covers Fedora/RHEL/openSUSE, `.tar.gz` is the generic
fallback for any distro. Windows ships a native **MSI** installer (WiX) plus a portable ZIP.
macOS ships separate Intel and Apple Silicon DMGs so each bundle carries a matching
`ffmpeg` binary.

> **macOS is 64-bit only (arm64 + x86_64) — there is no 32-bit target, by design.**
> Apple removed the ability to run 32-bit (i386) apps in macOS 10.15 Catalina (2019);
> macOS 10.14 Mojave was the last version that could. No current macOS runs 32-bit code,
> no hosted runner or modern SDK can build an i386 slice, and the deployment target
> (11.0) has no i386 support. So `macos-arm64` + `macos-x86_64` is the maximum coverage
> macOS can have — unlike Linux/Windows, there is no third (32-bit) macOS arch to add.

For README/direct-download stability, the release workflow publishes both versioned assets
(`dji-link-<version>-<slug>.<ext>`) and stable aliases
(`dji-link-<slug>.<ext>`). The stable aliases make permanent latest links possible:

| Platform | Stable latest asset |
|----------|---------------------|
| Windows x64 MSI | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-x64.msi` |
| Windows x64 ZIP | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-x64.zip` |
| Windows x86 MSI | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-x86.msi` |
| Windows arm64 MSI | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-arm64.msi` |
| Windows arm64 ZIP | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-windows-arm64.zip` |
| macOS arm64 DMG | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-macos-arm64.dmg` |
| macOS arm64 TGZ | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-macos-arm64.tar.gz` |
| macOS x86_64 DMG | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-macos-x86_64.dmg` |
| macOS x86_64 TGZ | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-macos-x86_64.tar.gz` |
| Linux x86_64 DEB | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-x86_64.deb` |
| Linux arm64 DEB | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-arm64.deb` |
| Linux x86_64 RPM | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-x86_64.rpm` |
| Linux arm64 RPM | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-arm64.rpm` |
| Linux x86_64 TGZ | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-x86_64.tar.gz` |
| Linux arm64 TGZ | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-linux-arm64.tar.gz` |
| Pi installer | `https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh` |

¹ Marked `experimental` (`continue-on-error`): if that toolchain/runner fails, the release
still ships from the other platforms. The 32-bit and Windows-arm64 jobs are the likeliest to
need attention on the first real build. Drop or promote them in `release.yml` any time.

## CMake contract (matters for installers)

The workflows call your CMake in the standard way:

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release --parallel
ctest --test-dir build --no-tests=ignore     # tests; having none is not an error yet
cpack --config build/CPackConfig.cmake        # packaging (see below)
```

Native installers (`.msi`, `.dmg`, `.deb`, `.rpm`, not just archives) are produced through
CPack from the repository `CMakeLists.txt`. The release workflow also runs a staged
`cmake --install` check and fails if the installed app or bundled `ffmpeg` is missing.

The current app builds the GUI by default (`DJI_LINK_GUI=ON`). SDL2 is fetched by CMake
with `FetchContent`, so CI runners do not need system GUI packages. Set
`-DDJI_LINK_GUI=OFF` only for a headless/console-only build.

Live video is decoded through an `ffmpeg` process. The app does **not** install ffmpeg at
runtime; release packaging handles it before the user launches the app:

| Platform | ffmpeg handling |
|----------|-----------------|
| Linux `.deb` / `.rpm` / `.tar.gz` | release workflow downloads a static ffmpeg build for the target arch and installs it into `bin/` |
| Windows `.msi` / `.zip` | release workflow installs Chocolatey ffmpeg (x64 — the only build the package ships) and bundles the real `ffmpeg.exe` folder into `bin/`. ffmpeg runs as a separate process, so the x64 binary works for the x86 and arm64 apps too on any x64 Windows host |
| macOS `.dmg` | release workflow installs Homebrew ffmpeg on the matching-arch runner, copies `ffmpeg` plus its dylibs into the `.app`, and patches install names |
| Portable `.zip` / `.tar.gz` | bundled ffmpeg is included in release artifacts; local developer builds fall back to `PATH` |

Minimal CPack shape for a future stripped-down `CMakeLists.txt`:

```cmake
cmake_minimum_required(VERSION 3.21)

# The version can come from CI (release.yml passes -DDJI_LINK_VERSION=...).
if(NOT DEFINED DJI_LINK_VERSION)
    set(DJI_LINK_VERSION "0.0.0")
endif()

project(dji-link VERSION ${DJI_LINK_VERSION} LANGUAGES CXX)

add_executable(dji-link src/main.cpp)
target_compile_features(dji-link PRIVATE cxx_std_20)

install(TARGETS dji-link RUNTIME DESTINATION bin BUNDLE DESTINATION .)

# --- Packaging / installers ---
set(CPACK_PACKAGE_NAME "dji-link")
set(CPACK_PACKAGE_VENDOR "DJI Link")
set(CPACK_PACKAGE_CONTACT "you@example.com")     # required for .deb
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Control a DJI Mavic Mini 1 from your computer")
set(CPACK_PACKAGE_VERSION "${DJI_LINK_VERSION}")
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)           # auto dependencies for .deb

# Windows MSI (WiX). Generate the GUID ONCE (e.g. `uuidgen`) and keep it forever —
# it's what lets a new .msi upgrade an installed older version in place.
set(CPACK_WIX_UPGRADE_GUID "REPLACE-WITH-A-STABLE-GUID")
include(CPack)
```

> The generators (`TGZ`, `DEB`, `RPM`, `WIX`, `DragNDrop`, `ZIP`) are chosen by the workflow
> via `cpack -G ...`; you don't need to list `CPACK_GENERATOR` in CMake. The WiX architecture
> (x64/arm64/x86) is passed by the workflow too, so one snippet covers every Windows arch.

### Tests

CI runs `ctest`. While there are no tests it's not an error (`--no-tests=ignore`). Once you
add some, register them with `enable_testing()` + `add_test(...)` (or `gtest_discover_tests`)
and they'll be checked automatically on every PR.

## Raspberry Pi installer

Every release also ships a one-command installer for the Pi jump-host (the `pi-installer`
job in `release.yml`). It publishes two assets:

| Asset | What |
|-------|------|
| `install-pi.sh` | self-contained `curl \| bash` bootstrap, stamped with this repo + tag |
| `dji-link-pi.tar.gz` | the `pi/` bundle it downloads — bridge scripts, `setup_pi.sh`, the access point (`ap.sh`) and `rescue.sh` |

On a **clean** Raspberry Pi (Zero 2 W), bring it up in one line:

```bash
curl -fsSL https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh | sudo bash
```

Latest release links:
- `https://github.com/Kolya080808/DJI-Link/releases/latest`
- `https://github.com/Kolya080808/DJI-Link/releases/latest/download/install-pi.sh`
- `https://github.com/Kolya080808/DJI-Link/releases/latest/download/dji-link-pi.tar.gz`

The installer downloads the matching `dji-link-pi.tar.gz`, unpacks it to `/opt/dji-link`,
then runs `setup_pi.sh` which:

- enables `dwc2` in peripheral mode and builds the `raw_gadget` kernel module (the Pi kernel
  ships it disabled);
- installs and enables **systemd services**:
  - `dji-ap.service` — the Wi-Fi access point itself (hostapd + dnsmasq on `uap0`); this is
    the control path to the Pi in the field, so it is the one service that must never stay
    down, and `install-pi.sh` rolls the whole bundle back if an upgrade leaves it broken;
  - `dji-netctl.service` — Pi Wi-Fi/AP HTTP API on `:9911` for the PC discovery screen;
  - `dji-bridge.service` — AOA↔TCP bridge on `:9910`;
  - `dji-update.timer` / `dji-update.service` — checks the latest GitHub Release every 6
    hours when internet is available and re-runs the Pi installer only when the tag changed;
  both **start automatically on every boot / power-up** — after the install finishes you can
  unplug power, and on the next power-up the Pi is ready with nothing to launch by hand;
- starts the service immediately if no reboot is pending (a first-time `dwc2` change needs one
  reboot, after which it comes up on its own).

Check it with `systemctl status dji-netctl dji-bridge dji-update.timer` and
`journalctl -u dji-netctl -f` / `journalctl -u dji-bridge -f` /
`journalctl -u dji-update -f`.

> `raw_gadget` is an out-of-tree module, so it must be rebuilt after a kernel upgrade —
> re-run the installer (or `sudo bash /opt/dji-link/pi/setup_pi.sh --dir /opt/dji-link/pi --service`).

## Under the hood

- **Compiler cache** — `ccache` (Linux/macOS) and `sccache` (Windows) in CI, so repeat builds
  are fast. Disabled in releases for a clean, reproducible build.
- **Ninja + CMake** installed by `lukka/get-cmake`; the MSVC environment on Windows by
  `ilammy/msvc-dev-cmd`.
- **concurrency** — a new push cancels the previous in-progress CI on the same branch;
  releases are never cancelled midway.
- **Dependabot** (`.github/dependabot.yml`) bumps GitHub Actions versions weekly.

## Reproduce locally, like CI

```bash
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure --no-tests=ignore
cpack --config build/CPackConfig.cmake      # if you added include(CPack)
```
