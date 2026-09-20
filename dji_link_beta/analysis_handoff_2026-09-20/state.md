# DJI Fly mode-analysis handoff

Date: 2026-09-20
Branch: `feature/flight-mode-virtual-gear`
Remote: `origin/feature/flight-mode-virtual-gear`

## Current stopping point

The DJI Fly UI path is confirmed through JNI `SoftSwitchMode`. The actual WM160 DUML packet is not confirmed yet. In the resumed API 35 runtime, explicit SDK initialization succeeded without USB, but the product list remained empty and three direct mode requests produced three callback errors (`-1`, `SoftSwitchMode`). See [runtime initialization findings](runtime/SDK_INIT_AND_MODE_CALLBACKS_2026-09-20.md).

Research is ongoing. The current runtime work extracts commands from DJI Fly by following `SoftSwitchMode` through JNI and the SDK transport until the outgoing DUML bytes are captured. `0x06/0x59` remains a hypothesis for WM160 until raw runtime capture exists.

## Confirmed

- DJI Fly 1.21.4 runs on the API 37 Google Play AVD with `PAGE_SIZE=4096` and ABI `x86_64,arm64-v8a`.
- `V1RCModeChannel...b()` calls `RxCSDK.u1` with the `SoftSwitchMode` key.
- `JNIKeyValue.native_set` receives Sport `00000000`, Normal/Position `01000000`, and Tripod `02000000`, with tuple `[0,3,0,65534,65534]` and name `SoftSwitchMode`.
- The Java product gate allows `UAV59` (Mavic Mini 1 / WM160) and `UAV96`; other products return `NOT_SUPPORTED`.
- In the original headless setup `UAVSDKManager.h()` was `false` and the product list was empty. The resumed run changed this to `true` through `UAVSDKManager.i(context, InitializeInfo, null)`, with the product list still empty. Neither observation proves an outgoing DUML frame.
- The USB AOA path expects manufacturer `DJI` and model `WM160`, `com.dji.link`, or `com.dji.logiclink`, then calls `JNIUsbAccessory.native_OnUsbConnected(fd, model)`.

## Next runtime steps

1. Keep the original APK runtime alive, reproduce the successful explicit SDK initialization, and establish native product discovery for `UAV59`. A socket-pair USB notification returned, but the process exited before capture; it is not a working accessory mock yet.
2. Prefer capture at the native USB descriptor/SDK transport. The actual Java bridge class is `uav.sdk.datalink.bridge.jni.JNIDataLinkBridgeServer`; its `native_bridge_send_raw_data(String, byte[], int)` receives the array length as its third argument. Its participation in ordinary USB mode sends is not established.
3. Trigger Sport, Normal, and Tripod and save the raw composite/DUML bytes.
4. Only then change the beta command and test it on the Pi.

## Artifacts

- This folder contains `repo_docs/`, `runtime/`, `scripts/`, and the pre-revert patch.
- Original APK LFS object: `.git/lfs/objects/26/64/26649c10483a090ca184fefe70b3694e5cdb72b538718d502dc1419e706e0a03`.
- Extracted DEX: `dji_link_beta/reverse_docs/unpacked_app_dex/`.
- Android Studio and AVD details: `android_studio/README.md`.
- Full runtime logs and Frida traces: `runtime/`.
- Resumed session findings and reproduction snippet: `runtime/SDK_INIT_AND_MODE_CALLBACKS_2026-09-20.md`; full new traces remain under ignored `scratch/android_runtime/`.
- `repo_docs/` and `repo_changes_before_revert.patch` include historical implementation claims. In particular, `FLIGHT_MODE_SOFTSWITCH_FIX_2026.md` describes reverted changes, not the current beta or a successful live packet capture. The current beta still sends MobileRC gear bursts; its HUD reads `FLYC_STATE` from telemetry.

## Android configuration

- SDK: `C:\Users\Nikolay\AppData\Local\Android\Sdk`
- Emulator 37.1.11, Platform Tools 37.0.1, Build Tools 36.0.0.
- Pixel 9: API 37 Google Play, `x86_64`, translated ABI `arm64-v8a`, verified page size 4096.
- Research AVD: API 35 Google APIs, `x86_64`, root-capable, verified page size 4096.
- Headless launch: `emulator -avd Pixel_9 -no-window -no-audio -no-boot-anim -no-snapshot -port 5554 -gpu swiftshader -feature -Vulkan`.

## Git state

No commits, pushes, or PR updates were made. The pre-revert diff is stored as `repo_changes_before_revert.patch`; verify the working tree separately before resuming.
