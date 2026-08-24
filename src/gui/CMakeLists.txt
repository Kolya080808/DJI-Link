# SDL2 GUI layer. SDL2 is fetched and built by CMake so no system
# packages are required on any CI runner (see docs/CI_CD.md / memory: build-on-ci).
include(FetchContent)

set(FETCHCONTENT_QUIET OFF)

# --- SDL2 ---
# Build only the static library we link; skip the SDL test/shared bloat.
set(SDL_SHARED OFF CACHE BOOL "" FORCE)
set(SDL_STATIC ON CACHE BOOL "" FORCE)
set(SDL_TEST OFF CACHE BOOL "" FORCE)
set(SDL2_DISABLE_INSTALL ON CACHE BOOL "" FORCE)
FetchContent_Declare(
    SDL2
    URL https://github.com/libsdl-org/SDL/releases/download/release-2.30.9/SDL2-2.30.9.tar.gz
    URL_HASH SHA256=24b574f71c87a763f50704bbb630cbe38298d544a1f890f099a4696b1d6beba4
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)

FetchContent_MakeAvailable(SDL2)

add_library(djilink_gui STATIC
    gui.cpp
)
# SDL_MAIN_HANDLED: we provide our own entry point (main / WinMain in src/main.cpp)
# and do NOT link SDL2main, so tell <SDL.h> not to redefine main to SDL_main. run_app()
# calls SDL_SetMainReady() before SDL_Init(), which is the supported way to bypass
# SDL's own main shim on every platform (Windows/macOS included).
target_include_directories(djilink_gui PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
# Vendored single-header stb_truetype (real anti-aliased text). Header-only, no build-
# system dependency; kept under third_party/ so lint/clang-format leave it alone.
target_include_directories(djilink_gui PRIVATE ${CMAKE_SOURCE_DIR}/third_party)
target_compile_definitions(djilink_gui PUBLIC SDL_MAIN_HANDLED)
target_compile_definitions(djilink_gui PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(djilink_gui PUBLIC djilink_core)

# Link the static SDL targets (names differ slightly across SDL versions).
if(TARGET SDL2::SDL2-static)
    target_link_libraries(djilink_gui PUBLIC SDL2::SDL2-static)
else()
    target_link_libraries(djilink_gui PUBLIC SDL2::SDL2)
endif()
