# Cross toolchain for the Pi services (dji-bridge / dji-netctl), used by the
# pi-installer job in .github/workflows/release.yml. Targets a 64-bit Raspberry
# Pi OS on a Zero 2 W (Cortex-A53).
#
#   cmake -S . -B build-pi -DCMAKE_TOOLCHAIN_FILE=cmake/pi-aarch64.toolchain.cmake
#   cmake --build build-pi --target dji-bridge dji-netctl
#
# Fully static link: the runner's glibc is newer than Raspberry Pi OS's, so a
# dynamic binary would refuse to start on the Pi. Neither daemon does NSS/DNS
# lookups (the bridge binds a fixed address, netctl shells out to nmcli), so a
# static glibc is safe here.
set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

set(CMAKE_C_COMPILER aarch64-linux-gnu-gcc)
set(CMAKE_CXX_COMPILER aarch64-linux-gnu-g++)

set(CMAKE_EXE_LINKER_FLAGS "-static")
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
