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
)

FetchContent_MakeAvailable(SDL2)

add_library(djilink_gui STATIC
    gui.cpp
)
target_include_directories(djilink_gui PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_definitions(djilink_gui PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)
target_link_libraries(djilink_gui PUBLIC djilink_core)

# Link the static SDL targets (names differ slightly across SDL versions).
if(TARGET SDL2::SDL2-static)
    target_link_libraries(djilink_gui PUBLIC SDL2::SDL2-static)
else()
    target_link_libraries(djilink_gui PUBLIC SDL2::SDL2)
endif()
if(TARGET SDL2::SDL2main)
    target_link_libraries(djilink_gui PUBLIC SDL2::SDL2main)
endif()
