cmake_minimum_required(VERSION 3.21)

# ---------------------------------------------------------------------------
# DJI Link — C++ rewrite of dji_link_beta/pc_client.py (WM160 PC control).
# The version comes from CI (release.yml passes -DDJI_LINK_VERSION=...); locally
# it defaults to 0.0.0 so a bare `cmake -S . -B build` still configures.
# See docs/CI_CD.md for the CMake contract the workflows rely on.
# ---------------------------------------------------------------------------
if(NOT DEFINED DJI_LINK_VERSION)
    set(DJI_LINK_VERSION "0.0.0")
endif()

# GitHub repo the in-app updater pulls releases from (owner/name).
if(NOT DEFINED DJI_LINK_REPO)
    set(DJI_LINK_REPO "Kolya080808/DJI-Link")
endif()

project(dji-link VERSION ${DJI_LINK_VERSION} LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

# The GUI (SDL2 window: video, HUD, settings, preflight menu, updater) is the
# double-click app. SDL2 is fetched by CMake (FetchContent), so no system GUI
# package is needed on CI. Turn OFF for a headless/console-only build.
option(DJI_LINK_GUI "Build the SDL2 GUI window" ON)

# The Raspberry Pi jump-host services (dji-bridge / dji-netctl, ported 1:1 from
# dji_link_beta/pi/*.py). OFF by default: they are POSIX/Linux-only daemons for
# the aarch64 Pi, NOT part of the PC client, so the main CI/PC build should not
# even try to compile them (a 32-bit/x86 build would fail against older
# linux/usb/raw_gadget.h and serves no real Pi). Turn this ON only when you
# explicitly want them — on the Pi itself, or via cmake/pi-aarch64.toolchain.cmake
# (the release.yml pi-installer job sets it; ci.yml's pi-aarch64 job mirrors that).
option(DJI_LINK_PI "Build the Raspberry Pi AOA bridge / netctl services" OFF)

# --------------------------------------------------------------- core library
# Every module with no windowing dependency lives here, so it compiles
# identically on Linux/macOS/Windows and can be unit-tested without a display.
add_library(djilink_core STATIC
    src/core/bytes.cpp
    src/core/crc.cpp
    src/core/duml.cpp
    src/core/param_hash.cpp
    src/core/composite.cpp
    src/core/control.cpp
    src/core/telemetry.cpp
    src/core/diag_codes.cpp
    src/core/diag_codes_full.cpp
    src/core/transport.cpp
    src/core/flight_mode.cpp
    src/core/drone.cpp
    src/core/applog.cpp
    src/core/netfind.cpp
    src/core/client.cpp
    src/core/updater.cpp
    src/core/ffmpeg.cpp
)
target_include_directories(djilink_core PUBLIC src)
target_compile_features(djilink_core PUBLIC cxx_std_20)
target_compile_definitions(djilink_core PUBLIC
    DJI_LINK_VERSION_STR="${DJI_LINK_VERSION}"
    DJI_LINK_REPO="${DJI_LINK_REPO}")

find_package(Threads REQUIRED)
target_link_libraries(djilink_core PUBLIC Threads::Threads)

if(WIN32)
    # iphlpapi: GetAdaptersAddresses, used by netfind to enumerate the real
    # adapters (a PC with WSL/Hyper-V/VPN has several, and the hostname lookup
    # it replaced picked whichever came first — rarely the Wi-Fi one).
    target_link_libraries(djilink_core PUBLIC ws2_32 iphlpapi)
endif()

# ---------------------------------------------------------------- executable
add_executable(dji-link src/main.cpp)
target_link_libraries(dji-link PRIVATE djilink_core)

set(DJI_LINK_RESOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/resources")
if(WIN32)
    target_sources(dji-link PRIVATE "${DJI_LINK_RESOURCE_DIR}/dji-link.rc")
elseif(UNIX AND NOT APPLE AND DJI_LINK_GUI)
    include(GNUInstallDirs)
    install(FILES "${DJI_LINK_RESOURCE_DIR}/dji-link-icon.svg"
            DESTINATION "${CMAKE_INSTALL_DATADIR}/icons/hicolor/scalable/apps"
            RENAME dji-link.svg)
    configure_file("${DJI_LINK_RESOURCE_DIR}/dji-link.desktop.in"
                   "${CMAKE_CURRENT_BINARY_DIR}/dji-link.desktop" @ONLY)
    install(FILES "${CMAKE_CURRENT_BINARY_DIR}/dji-link.desktop"
            DESTINATION "${CMAKE_INSTALL_DATADIR}/applications")
endif()

# On Windows, a GUI app should not pop a console window when double-clicked.
if(DJI_LINK_GUI AND WIN32)
    set_target_properties(dji-link PROPERTIES WIN32_EXECUTABLE ON)
endif()
# macOS: build an .app bundle so double-click works and DragNDrop packaging is a drag-in.
if(DJI_LINK_GUI AND APPLE)
    set_target_properties(dji-link PROPERTIES MACOSX_BUNDLE ON)
    set_source_files_properties("${DJI_LINK_RESOURCE_DIR}/dji-link.icns"
                                PROPERTIES MACOSX_PACKAGE_LOCATION "Resources")
    target_sources(dji-link PRIVATE "${DJI_LINK_RESOURCE_DIR}/dji-link.icns")
    set_target_properties(dji-link PROPERTIES
        MACOSX_BUNDLE_ICON_FILE "dji-link.icns"
        MACOSX_BUNDLE_GUI_IDENTIFIER "com.kolya080808.dji-link")
endif()

if(DJI_LINK_GUI)
    add_subdirectory(src/gui)
    target_link_libraries(dji-link PRIVATE djilink_gui)
    target_compile_definitions(dji-link PRIVATE DJI_LINK_HAVE_GUI=1)
endif()

install(TARGETS dji-link RUNTIME DESTINATION bin BUNDLE DESTINATION .)

# ---------------------------------------------------------- Pi services (C++)
# The Raspberry Pi jump-host daemons, ported 1:1 from dji_link_beta/pi/*.py:
#   dji-bridge — AOA <-> TCP bridge on :9910 (raw_gadget USB gadget)
#   dji-netctl — Wi-Fi/AP HTTP API on :9911 + CLI (port of netctl.py)
# POSIX-only, so they build solely on Linux (natively on the Pi, or crossed for
# aarch64 via cmake/pi-aarch64.toolchain.cmake in the release workflow). They are
# intentionally NOT installed/packaged with the PC client — release.yml's
# pi-installer job copies the binaries into the pi/ bundle itself.
#
# Gated behind DJI_LINK_PI (OFF by default — see the option above) so the PC
# CI matrix never tries to compile them.
if(DJI_LINK_PI AND CMAKE_SYSTEM_NAME STREQUAL "Linux")
    add_executable(dji-bridge
        src/pi/bridge.cpp
        src/pi/aoa_device.cpp
        src/pi/raw_gadget.cpp
    )
    target_include_directories(dji-bridge PRIVATE src)

    add_executable(dji-netctl src/pi/netctl.cpp)

    foreach(pi_target dji-bridge dji-netctl)
        target_compile_features(${pi_target} PRIVATE cxx_std_20)
        target_link_libraries(${pi_target} PRIVATE Threads::Threads)
    endforeach()
endif()

# --------------------------------------------------------------------- tests
# Built and run by ci.yml's `ctest` step on every platform in the matrix. Only
# display-free, network-free core logic is covered here, so it runs everywhere.
# Skipped when this project is consumed as a subproject.
if(PROJECT_IS_TOP_LEVEL)
    enable_testing()
    add_executable(flight_mode_test tests/flight_mode_test.cpp)
    target_link_libraries(flight_mode_test PRIVATE djilink_core)
    add_test(NAME flight_mode COMMAND flight_mode_test)
    add_executable(netctl_parse_test tests/netctl_parse_test.cpp)
    target_link_libraries(netctl_parse_test PRIVATE djilink_core)
    add_test(NAME netctl_parse COMMAND netctl_parse_test)
    add_executable(home_rth_test tests/home_rth_test.cpp)
    target_link_libraries(home_rth_test PRIVATE djilink_core)
    add_test(NAME home_rth COMMAND home_rth_test)
    add_executable(set_flight_mode_send_test tests/set_flight_mode_send_test.cpp)
    target_link_libraries(set_flight_mode_send_test PRIVATE djilink_core)
    add_test(NAME set_flight_mode_send COMMAND set_flight_mode_send_test)
endif()

# Release-time ffmpeg bundle. The release workflow sets these paths after preparing
# platform-native ffmpeg binaries. Application code never installs ffmpeg at runtime;
# it only launches the binary installed here.
if(DEFINED DJI_LINK_BUNDLED_FFMPEG AND EXISTS "${DJI_LINK_BUNDLED_FFMPEG}")
    if(APPLE)
        install(PROGRAMS "${DJI_LINK_BUNDLED_FFMPEG}"
                DESTINATION "dji-link.app/Contents/Resources/bin")
        if(DEFINED DJI_LINK_BUNDLED_FFMPEG_LIB_DIR
           AND EXISTS "${DJI_LINK_BUNDLED_FFMPEG_LIB_DIR}")
            install(DIRECTORY "${DJI_LINK_BUNDLED_FFMPEG_LIB_DIR}/"
                    DESTINATION "dji-link.app/Contents/Resources/lib"
                    FILES_MATCHING PATTERN "*.dylib")
        endif()
    elseif(WIN32)
        if(DEFINED DJI_LINK_BUNDLED_FFMPEG_DIR
           AND EXISTS "${DJI_LINK_BUNDLED_FFMPEG_DIR}")
            install(DIRECTORY "${DJI_LINK_BUNDLED_FFMPEG_DIR}/" DESTINATION bin)
        else()
            install(PROGRAMS "${DJI_LINK_BUNDLED_FFMPEG}" DESTINATION bin)
        endif()
    else()
        install(PROGRAMS "${DJI_LINK_BUNDLED_FFMPEG}" DESTINATION bin)
    endif()
endif()

# --------------------------------------------------------------- packaging
# Native installers (.msi / .dmg / .deb / .rpm) — generators are chosen by the
# release workflow via `cpack -G ...`; we only supply the metadata here.
# Friendly product name shown in the installers / Start menu / Add-Remove Programs and
# the macOS DMG ("DJI Link", not "dji-link"). The Linux .deb/.rpm package name must stay a
# lowercase, space-free identifier, so those override it below.
set(CPACK_PACKAGE_NAME "DJI Link")
set(CPACK_DEBIAN_PACKAGE_NAME "dji-link")
set(CPACK_RPM_PACKAGE_NAME "dji-link")
# Publisher / developer = the repo owner (shown as "Manufacturer" in the MSI, etc.).
set(CPACK_PACKAGE_VENDOR "Kolya080808")
set(CPACK_PACKAGE_CONTACT "Kolya080808 <dji-link@users.noreply.github.com>")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Control a DJI Mavic Mini 1 (WM160) from your computer")
set(CPACK_PACKAGE_HOMEPAGE_URL "https://dream-catcher-project.ru/")
set(CPACK_PACKAGE_VERSION "${DJI_LINK_VERSION}")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "DJI Link")
set(CPACK_PACKAGE_EXECUTABLES "dji-link" "DJI Link")
set(CPACK_CREATE_DESKTOP_LINKS "dji-link")
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)

# Show the Apache-2.0 license in the installers (WiX license page / DMG agreement).
# WiX/DragNDrop want a file with a real extension, so stage the extension-less LICENSE
# as LICENSE.txt in the build tree and point CPack at that.
configure_file("${CMAKE_CURRENT_SOURCE_DIR}/LICENSE"
               "${CMAKE_CURRENT_BINARY_DIR}/LICENSE.txt" COPYONLY)
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_BINARY_DIR}/LICENSE.txt")

# WiX MSI: a STABLE upgrade GUID (generated once, never changed) is what lets a
# new .msi upgrade an installed older version in place. Do not regenerate it.
set(CPACK_WIX_UPGRADE_GUID "7E3C1B2A-4D5F-4A6B-9C8D-1E2F3A4B5C6D")
set(CPACK_WIX_PRODUCT_ICON "${DJI_LINK_RESOURCE_DIR}/dji-link.ico")
set(CPACK_WIX_UI_BANNER "${DJI_LINK_RESOURCE_DIR}/wix-banner.bmp")
set(CPACK_WIX_UI_DIALOG "${DJI_LINK_RESOURCE_DIR}/wix-dialog.bmp")
set(CPACK_DMG_VOLUME_NAME "DJI Link")
include(CPack)
