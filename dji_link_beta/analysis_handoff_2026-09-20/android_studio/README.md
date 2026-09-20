# Android Studio / emulator assets

SDK root: `C:\Users\Nikolay\AppData\Local\Android\Sdk`
Android Studio SDK components used:
- Emulator 37.1.11
- Platform Tools 37.0.1
- Build Tools 36.0.0
- Android Platform 37.0
- Android System Image API 37.0, Google Play, x86_64
- Android System Image API 35, Google APIs, x86_64 (research image; root-capable)

AVD configurations copied here:
- `avd_configs/Pixel_9.ini` and `avd_configs/Pixel_9.config.ini` — normal Google Play API 37, 4 KB pages.
- `avd_configs/DJI_Research_35.config.ini` — research Google APIs API 35 configuration generated for root/Frida.

Installed image paths:
- `C:\Users\Nikolay\AppData\Local\Android\Sdk\system-images\android-37.0\google_apis_playstore\x86_64\`
- Research image extracted under `scratch/android_runtime/google-apis-35\` and configured into `scratch/android_runtime/avd\DJI_Research_35.avd\`.

Verification:
- Pixel 9: `adb shell getconf PAGE_SIZE` returned `4096`; ABI `x86_64,arm64-v8a`.
- Research API 35: `adb root` succeeded; `PAGE_SIZE=4096`; ABI `x86_64,arm64-v8a`.

Headless launch used:
`emulator -avd Pixel_9 -no-window -no-audio -no-boot-anim -no-snapshot -port 5554 -gpu swiftshader -feature -Vulkan`

Research image catalog and setup script are in the parent `runtime/` and `scripts/` folders.
The full Google APIs API 35 ZIP is intentionally not duplicated here; it is 1.7 GB and remains at `scratch/android_runtime/google-apis-35.zip`.
