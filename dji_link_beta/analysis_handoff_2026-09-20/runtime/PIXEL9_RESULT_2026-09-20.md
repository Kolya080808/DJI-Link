# DJI Fly runtime checkpoint — 2026-09-20

- AVD: Pixel_9, android-37.0/google_apis_playstore/x86_64.
- Started headlessly, no Windows UI opened. Successful boot used `-no-window -no-audio -no-boot-anim -no-snapshot -port 5554 -show-kernel -gpu swiftshader -feature -Vulkan`.
- First launch with default graphics stayed offline during observation; restarting with the above options booted. This does not isolate the cause to Vulkan.
- `getconf PAGE_SIZE`: 4096; `sys.boot_completed`: 1; ABI list: x86_64,arm64-v8a.
- Installed original local DJI Fly 1.21.4 APK as arm64-v8a; package installation returned Success.
- `libc++_shared.so` and `libsdk_jni.so` load successfully. The previous 16 KB alignment failure is absent.
- Completed initial onboarding, skipped account login and optional analytics/location authorization. Main screen shows Connection Guide, Album, DJI Simulator, etc. Main process remained running (observed PID 4991).
- Nonfatal MdidSdkHelper initialization error appears in the startup log. No fatal crash was observed in this run.
- USB service reports DISCONNECTED; no physical accessory was attached or simulated. DJI USB protocol was not exercised.
- `adb root`: adbd cannot run as root in production builds. `run-as dji.go.v5`: package not debuggable. Build type user, ro.debuggable=0. Stock frida-server attachment is therefore not ready on this image. A debuggable Google APIs image or instrumented APK remains necessary.
- Beta tests: 16 passed. Native SoftSwitchMode harness passed sport=00, normal=01, cine=02, command 06/59 to FLYC.
- No aircraft acceptance or flight-mode change has been proven by this emulator run. Hardware test with fresh ACK/OSD logs remains necessary.
- No commits or push performed. Runtime files are under ignored scratch/android_runtime.

Evidence: pixel9-dji.log, pixel9-startup.log, pixel9-capabilities.log, pixel9-kernel.out.log, dji-next.txt.
