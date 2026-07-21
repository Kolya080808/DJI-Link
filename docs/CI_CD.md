# CI/CD

Automated build, checks, and releases for the C++ version of DJI Link on GitHub Actions.
All C++ code lives at the repository root (the `dji_link_beta/` folder is the old Python
beta; CI ignores it).

## TL;DR

| What | File | When it runs |
|------|------|--------------|
| **CI** — compile-check + tests | `.github/workflows/ci.yml` | push to `main` / PR, **only when C++/CMake changed** |
| **Lint** — formatting (clang-format) | `.github/workflows/lint.yml` | same, C++ only |
| **Release** — binaries/installers + checksums | `.github/workflows/release.yml` | **only on a git tag `vX.Y.Z`** |

Small edits, feature experiments, and changes to the Python beta do **not** trigger heavy builds.

## How to cut a release

1. Edit **`UPDATE.md`** at the repo root: `title`, `version`, `prerelease`, and the changelog.
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
| macOS universal2 (Intel + Apple Silicon) | `macos-14` | `.dmg`, `.tar.gz` |
| Windows x64 | `windows-latest` | `.msi` (WiX installer), `.zip` |
| Windows arm64 | `windows-11-arm` | `.msi`, `.zip` — *best-effort*¹ |
| Windows x86 (32-bit) | `windows-latest` | `.msi`, `.zip` — *best-effort*¹ |

`.deb` covers Debian/Ubuntu, `.rpm` covers Fedora/RHEL/openSUSE, `.tar.gz` is the generic
fallback for any distro. Windows ships a native **MSI** installer (WiX) plus a portable ZIP.
macOS `universal2` runs on both Intel and Apple Silicon from a single binary.

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

To get **native installers** (`.msi`, `.dmg`, `.deb`, `.rpm`, not just archives), your
`CMakeLists.txt` must contain `include(CPack)`. Until it does, the release workflow falls
back to `cmake --install` + `.tar.gz`/`.zip`, so the Linux binary and archives ship anyway,
and installers appear as soon as you add CPack.

Minimal working snippet for `CMakeLists.txt`:

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
| `dji-link-pi.tar.gz` | the `pi/` bundle (bridge scripts + `setup_pi.sh`) it downloads |

On a **clean** Raspberry Pi (Zero 2 W), bring it up in one line:

```bash
curl -fsSL https://github.com/OWNER/REPO/releases/latest/download/install-pi.sh | sudo bash
```

The installer downloads the matching `dji-link-pi.tar.gz`, unpacks it to `/opt/dji-link`,
then runs `setup_pi.sh` which:

- enables `dwc2` in peripheral mode and builds the `raw_gadget` kernel module (the Pi kernel
  ships it disabled);
- installs a **systemd service** (`dji-bridge.service`) and `enable`s it, so the AOA↔TCP
  bridge **starts automatically on every boot / power-up** — after the install finishes you
  can unplug power, and on the next power-up the service is running with nothing to launch by
  hand;
- starts the service immediately if no reboot is pending (a first-time `dwc2` change needs one
  reboot, after which it comes up on its own).

Check it with `systemctl status dji-bridge` and `journalctl -u dji-bridge -f`.

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
