# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Язык / Language

**Всё рассуждение, объяснения и рабочие комментарии в этом репозитории — только на русском языке.** Владелец репозитория читает по-русски и хочет понимать ход мыслей: любые размышления (в т.ч. видимый thinking), выводы, планы и ответы веди на русском. Код, идентификаторы и коммиты — по обычным правилам проекта (английский), но пояснения к ним — на русском.

## What this is

DJI Link is a native C++20 desktop ground station for the DJI Mavic Mini 1 (WM160). It gives a PC live video, telemetry, and full flight control without the phone app, speaking the drone's reverse-engineered **DUML** protocol. A Raspberry Pi acts as a USB/AOA bridge between the DJI remote controller and the PC (over Wi-Fi/TCP); the PC does all the protocol work.

The shipping code is the C++ under `src/`. `dji_link_beta/` is the **older Python beta** (`pc_client.py` and friends) kept as historical reference and reverse-engineering docs — CI ignores it, and most C++ modules are ports of a matching `dji_link_beta/*.py`. Do not treat the Python as the source of truth for current behavior.

## Current goal — correct flight-mode selection & switching

The active, top-priority feature work is fixing flight-mode selection, switching, and display (Cine / Normal / Sport) on the WM160. It runs on branch `feature/flight-mode-switch` and is decomposed task-by-task in `docs/FLIGHT_MODE_ROADMAP.md`.

**Problem being fixed.** Flight mode on the Mini is *not* a writable FC parameter — it is the choice of which pre-loaded FC config block is active (`mode_normal_cfg` = Position, `mode_sport_cfg` = Sport, `mode_gentle_cfg` = CineSmooth, `mode_tripod_cfg` = Tripod), selected by the RC gear channel. DJI Fly emulates that gear on the Mini with the KeyValue key `RemoteController/SoftSwitchMode` (enum POSITION/SPORT/TRIPOD) routed to the RC component (cmdset `0x06`) — **not** a FLYC `0x03` write. The current code (`Drone::set_flight_mode`, `src/core/drone.cpp`) instead overwrites one Normal-block tilt parameter (`g_config.mode_normal_cfg.tilt_atti_range_0`); that only yields a "sped-up Normal", never activates the Sport block, so Normal→Sport never switches. `set_horizontal_speed` (tilt = a speed setting) must stay separate from mode selection.

**Definition of done.** `set_flight_mode` sends a real SoftSwitchMode gear frame on cmdset `0x06`; the HUD's current mode is derived from the live `FLYC_STATE` OSD field (SPORT=31, Cinematic=19, TRIPOD_GPS=38, else Normal), not the gear channel; switching is observable on `--sim`; covered by unit tests and subagent review.

**Known blocker.** The exact SoftSwitchMode DUML frame (cmdset/cmdid/payload/receiver) is not fixed statically. Per owner decision we build against the three candidate cmd_ids (`0x06/0x06`, `0x06/0x19`, `0x06/0x11`) with config selection + auto-detection from the OSD `FLYC_STATE` response; the winning cmd_id is confirmed only on real hardware by the owner.

**Working method (owner's process).** Take one task from `docs/FLIGHT_MODE_ROADMAP.md` at a time; set an explicit goal for it (decompose further if needed); write the code; spin up a subagent for code review; when clean, open a PR from a sub-branch **into** `feature/flight-mode-switch`. The final merge of `feature/flight-mode-switch` into `main` is done by the owner after on-drone verification. Do **not** bypass any safety check or geofence while doing this (see Safety).

## Build, test, run

The build is CMake + Ninja, C++20. The GUI is on by default.

```bash
# Configure + build (Release, mirrors CI)
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel

# Debug build (for development)
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug && cmake --build build --parallel

# Tests
ctest --test-dir build --output-on-failure --no-tests=ignore
ctest --test-dir build -R netctl_parse    # run a single test by name (also: home_rth)

# Lint (must pass — LLVM base, 100 col, 4-space indent; see .clang-format)
clang-format --dry-run --Werror <files>   # excludes build/, third_party/, dji_link_beta/
```

Run the app (flags mirror the old `pc_client.py`):

```bash
./build/dji-link --sim --windowed   # simulator, no hardware needed — best for UI work
./build/dji-link --console          # headless console client (no display / debugging)
./build/dji-link --pi <host>        # connect via the Pi bridge
./build/dji-link --wifi --pi <host> --wifi-ssid <s> --wifi-psk <p>  # headless Pi Wi-Fi setup
```

Other flags: `--serial <dev>`, `--dry`, `--verbose`, `--no-video`.

- **SDL2 is fetched by CMake (FetchContent)** — no system GUI packages needed. `-DDJI_LINK_GUI=OFF` builds a console-only binary (then `--console` is the only mode).
- **ffmpeg is never installed at runtime.** Release packaging bundles a per-platform binary into `bin/`; local dev builds fall back to `ffmpeg` on `PATH`. Video decode runs ffmpeg as a separate process.

## Architecture

Three cooperating parts (see `docs/ARCHITECTURE.md`):

```
PC (this app) ──Wi-Fi/TCP──> Raspberry Pi (USB/AOA bridge + AP) ──USB──> DJI remote ──radio──> WM160
```

Data flow into the app: `Pi bridge → TCP → CompositeDemux → { DUML → telemetry/control/camera/gimbal ; video → HEVC reassembly → ffmpeg → GUI }`.

### `src/core/` — `djilink_core` static library
Deliberately **SDL-free, display-free, and unit-testable without hardware or a network**, so it compiles identically on Linux/macOS/Windows. Contains the protocol stack: `duml` (DUML framing/CRC via `crc`, `param_hash`), `composite` (stream demux), `telemetry`, `control`, `diag_codes(_full)`, `transport` (serial/TCP/sim), `drone` (high-level command API), `client` (session object), `netfind` (Pi discovery), `applog`, `updater`, `ffmpeg`.

- **`Client`** (`client.hpp`) is the session object shared by both the console and the GUI. It owns the transport, `Drone`, telemetry, and the RX/TX/stats threads, plus HEVC parameter-set (VPS/SPS/PPS) caching that keeps the decoder fed. The GUI plugs in a `VideoOut` for the picture and drives the stick axes; the core stays SDL-free on purpose.
- **`Drone`** (`drone.hpp`) is the single point through which any command source controls the aircraft.

### `src/gui/` — SDL2 app (`gui.cpp`)
The double-click app: preflight/connect menu, in-flight window with video + HUD + settings + console + updater. Built as a separate `djilink_gui` target, linked only when `DJI_LINK_GUI=ON`.

### `src/pi/` — Raspberry Pi jump-host services (C++)
`dji-bridge` (`bridge.cpp` + `aoa_device.cpp` + `raw_gadget.cpp`) — AOA↔TCP bridge on **:9910**. `dji-netctl` (`netctl.cpp`) — Wi-Fi/AP HTTP API on **:9911** + CLI. **POSIX/Linux-only, gated behind `-DDJI_LINK_PI=ON` (OFF by default)** — the PC/CI matrix never compiles them (a 32-bit/x86 build would fail against the raw_gadget headers). They are cross-compiled for aarch64 via `cmake/pi-aarch64.toolchain.cmake` and packaged separately by `release.yml`, never installed with the PC client.

### `src/main.cpp`
Entry point: parses args, then branches to `--wifi` setup → GUI (`gui::run_app`) → console fallback. On Windows the GUI build is a Windows-subsystem app, so `WinMain` forwards to `main` using `__argc`/`__argv`.

## Protocol gotchas (read before touching the drone/control path)

- **DUML addressing:** the app speaks as the **MOBILE APP (`0x02`)**. Using `0x0a` (PC/Assistant) makes the flight controller lock the motors (`AssistantProtected`). `drone.hpp` documents the `DEV_*` addresses.
- **`Drone::encrypt_config`:** FC config/param frames are encrypted on the app/radio path but sent **plaintext over serial**. The console client sets `encrypt_config = false`.
- **Media commands are intentionally omitted** (project scope). The media protocol is the biggest known unknown and the top research priority — see `CONTRIBUTING.md` and `dji_link_beta/reverse_docs/`. Prefer verified research over speculative implementation here.
- **Mouse look:** the GUI accumulates relative mouse dx at ~60 Hz but sticks are sent at 20 Hz; use `Client::add_mouse_dx`/`take_mouse_dx` (accumulate vs. read-and-clear) so no motion is dropped.

## CI / release contract

- **`ci.yml`** (build + `ctest`) and **`lint.yml`** (clang-format) run **only when C++/CMake files change** and only on push to `main` / PRs. Changes to `dji_link_beta/`, `*.md`, or docs do **not** trigger a build; feature branches without a PR don't run CI. Reproduce CI locally with the Release commands above.
- CI's build matrix **mirrors the release matrix 1:1** (Linux x86_64/arm64/x86, macOS arm64/x86_64, Windows x64/arm64/x86). **Keep them in sync when adding/dropping a platform.**
- **`release.yml`** runs **only on a git tag `vX.Y.Z`** and only if `UPDATE.md` exists. It reads `UPDATE.md` frontmatter, **verifies `version:` matches the tag**, builds installers (.msi/.dmg/.deb/.rpm), bundles ffmpeg, computes checksums, and publishes the release. To cut a release: edit `UPDATE.md`, then `git tag vX.Y.Z && git push origin vX.Y.Z`. See `docs/CI_CD.md`.
- The version is injected by CI via `-DDJI_LINK_VERSION=...`; a bare local configure defaults to `0.0.0`. `CPACK_WIX_UPGRADE_GUID` in `CMakeLists.txt` must **never** change (it's what lets a new .msi upgrade an old install).

## C++ coding guidelines

Distilled from the Google C++ Style Guide, C++ Core Guidelines, LLVM Coding Standards, Mozilla, Chromium, WebKit, NASA, and OceanBase C++ standards, adapted to this codebase. The rule that beats all others: **match the surrounding code** — a new line should be indistinguishable from the file it lives in.

- **Formatting (CI-enforced).** `clang-format` must pass (`.clang-format`: LLVM base, 100-column limit, 4-space indent, no tabs, left-aligned pointers `T* p`). Run `clang-format --dry-run --Werror` before every commit; never hand-format against it.
- **Naming (as already in `src/`).** Types/classes/enums `PascalCase` (`DumlPacket`, `OsdState`); functions and methods `snake_case` (`set_flight_mode`, `next_seq`); member variables trailing underscore (`t_`, `seq_`, `alive_`); constants / `constexpr` `kPascalCase` (`kPi`, `kTilt`). Names state intent; no abbreviations a new reader must decode.
- **Types & safety.** Prefer `enum class` over bare `enum` and over magic ints for a fixed set (e.g. flight mode). Fixed-width types on the wire (`std::uint8_t`, …). `const` everything that does not change; `constexpr` for compile-time constants. Initialise every variable at declaration; keep it in the narrowest scope. No owning raw pointers, no manual `new`/`delete` — RAII and smart pointers; obey the Rule of 0/5.
- **Functions.** Small and single-purpose. Validate arguments at the boundary — this is a control-path project (`set_home_point` already throws on out-of-range input; follow that). Prefer early return over deep nesting. Keep hot paths (the 20 Hz stick loop, `cmd()`) allocation- and lock-contention-aware.
- **Comments explain WHY, not what.** A comment earns its place by recording a non-obvious reason — a protocol quirk, a race, a hardware constraint (see the `0x0a` motor-lock and atomic-seq notes in `drone.cpp`). Keep them truthful and current; delete stale ones. No commented-out code in commits.
- **Protocol / byte code.** Never leave a bare wire constant unexplained — annotate what each offset/opcode means and cite the source (jar/firmware/capture), as the telemetry and drone code do. Prefer the `put_*`/`get_*` helpers over ad-hoc byte math.
- **Headers & includes.** Guard with `#pragma once`. Include order matches the files here: own header first, then project headers, then C/C++ standard headers, each group sorted. Include what you use; forward-declare to cut dependencies (`struct DumlPacket;` in `telemetry.hpp`).
- **Concurrency.** Document which mutex guards which data (as `drone.hpp` does for `tx_mu_`). Prefer `std::atomic` for simple flags/counters. Never send a torn or duplicated frame — hold the tx mutex across encode+send.
- **Errors.** `throw` (e.g. `std::invalid_argument`) for programmer/argument errors on control commands; never throw on the hot telemetry path — use `std::optional`, as the decoders already do. Every code path returns a defined value.
- **Tests.** New core logic ships with a portable unit test wired into `ctest` (display-free, network-free), matching `tests/`. A bug fix starts with a failing test.

## Safety

Do not submit or generate changes that bypass the drone's safety checks or geofencing. This is unofficial software controlling real flight hardware — control-path and takeoff/land/RTH changes warrant extra care and testing (use `--sim` first).
