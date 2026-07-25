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
    target_link_libraries(djilink_core PUBLIC ws2_32)
endif()

# ---------------------------------------------------------------- executable
add_executable(dji-link src/main.cpp)
target_link_libraries(dji-link PRIVATE djilink_core)

# On Windows, a GUI app should not pop a console window when double-clicked.
if(DJI_LINK_GUI AND WIN32)
    set_target_properties(dji-link PROPERTIES WIN32_EXECUTABLE ON)
endif()
# macOS: build an .app bundle so double-click works and DragNDrop packaging is a drag-in.
if(DJI_LINK_GUI AND APPLE)
    set_target_properties(dji-link PROPERTIES MACOSX_BUNDLE ON)
endif()

if(DJI_LINK_GUI)
    add_subdirectory(src/gui)
    target_link_libraries(dji-link PRIVATE djilink_gui)
    target_compile_definitions(dji-link PRIVATE DJI_LINK_HAVE_GUI=1)
endif()

install(TARGETS dji-link RUNTIME DESTINATION bin BUNDLE DESTINATION .)

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
set(CPACK_PACKAGE_NAME "dji-link")
set(CPACK_PACKAGE_VENDOR "DJI Link")
set(CPACK_PACKAGE_CONTACT "dji-link@users.noreply.github.com")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Control a DJI Mavic Mini 1 (WM160) from your computer")
set(CPACK_PACKAGE_VERSION "${DJI_LINK_VERSION}")
set(CPACK_PACKAGE_INSTALL_DIRECTORY "DJI Link")
set(CPACK_PACKAGE_EXECUTABLES "dji-link" "DJI Link")
set(CPACK_CREATE_DESKTOP_LINKS "dji-link")
set(CPACK_DEBIAN_PACKAGE_SHLIBDEPS ON)

# WiX MSI: a STABLE upgrade GUID (generated once, never changed) is what lets a
# new .msi upgrade an installed older version in place. Do not regenerate it.
set(CPACK_WIX_UPGRADE_GUID "7E3C1B2A-4D5F-4A6B-9C8D-1E2F3A4B5C6D")
include(CPack)
