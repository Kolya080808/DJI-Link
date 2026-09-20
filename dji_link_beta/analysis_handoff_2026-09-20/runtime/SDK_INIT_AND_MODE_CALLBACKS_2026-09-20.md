# SDK initialization and mode callback findings

Date: 2026-09-20, resumed session. No aircraft was attached or commanded. No application source changes or commits were made.

## Result

Explicit initialization of the real SDK succeeds without an accessory. It does not create a connected product. Three direct JNI `SoftSwitchMode` requests, using values 0, 1, and 2, then produced three asynchronous callbacks with code `-1` and message `SoftSwitchMode`.

This advances the previous `sdk_initialized=false` checkpoint, but does not confirm a mode packet. The meaning of `-1` has not been independently decoded; do not describe it as an aircraft rejection or assign a specific SDK error name. There was no connected aircraft and no wire capture in this test.

## Runtime and instrumentation

- Root-capable `DJI_Research_35`, API 35, emulator-5554, arm64 application on x86_64 translation, Frida 17.16.4.
- The installed research APK initially failed before `DJIApplication` creation with `NoClassDefFoundError: kotlin.jvm.internal.Lambda`, including an uninstrumented launch. Do not reuse that APK as a working runtime baseline.
- Updating it with the original APK failed with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`. Research application data was archived on the emulator as `/data/local/tmp/dji-research-data-before-restore.tgz`, then the research package was uninstalled and the original `/data/local/tmp/DJI-Fly.apk` installed with `--abi arm64-v8a`. Installation succeeded.
- The original application's Java mode route was reproduced after restoration. ProductType was mocked only for that separate UI-to-JNI test.
- The initialization/callback test directly called JNI without a ProductType mock. It used the original application class loader and the Java bridge from `frida_tools`.
- The existing Frida runner was adapted in memory to replace AppGuard-x86 worker entry points with a no-op at `pthread_create`, and to omit the auxiliary emulated-realm probe. This is an instrumented run, not stock application behavior. The inherited trace label says `observe AppGuard worker`, but workers were actually suppressed.

## Reproduction of the successful initialization

Run inside `Java.performNow` after `currentApplication()` is non-null, using its class loader. In the successful run this was scheduled 2 seconds after spawn/resume.

```javascript
const app = Java.use('android.app.ActivityThread').currentApplication();
Java.classFactory.loader = app.getClassLoader();
const sdk = Java.use('uav.sdk.UAVSDKManager').d();
const init = Java.use('uav.jni.value.InitializeInfo').$new();
const dir = String(app.getFilesDir().getAbsolutePath());
init.setCommonStorageDirPath(dir);
init.setSystemPublicDir(dir);
init.setmAppVersion('1.21.4');
send({before: sdk.h(), products: String(sdk.g())});
send({initialize_return: sdk.i(app, init, null)});
send({after: sdk.h(), products: String(sdk.g())});

const Callback = Java.registerClass({
  name: 'org.research.ModeResult',
  implements: [Java.use('uav.jni.callback.key.JNISetCallback')],
  methods: {
    onResult: function(code, message) {
      send({callback_code: code, message: String(message)});
    }
  }
});
for (const mode of [0, 1, 2]) {
  send({mode: mode});
  Java.use('uav.jni.JNIKeyValue').native_set(
    0, 3, 0, 65534, 65534, 'SoftSwitchMode',
    Java.array('byte', [mode, 0, 0, 0]), Callback.$new());
}
```

Observed in PID 3517 (`scratch/android_runtime/resume-sdk-init.runtime.log`):

```text
before=false products=[]
initialize_return=true
after=true products=[]
mode=0
mode=1
callback_code=-1 message=SoftSwitchMode
mode=2
callback_code=-1 message=SoftSwitchMode
callback_code=-1 message=SoftSwitchMode
```

Callbacks were not individually tagged with their originating mode, so ordering should not be inferred beyond three requests and three identical errors. The initialization object retained defaults for other fields; this is a minimal successful initialization, not the normal application's complete configuration.

## USB socket-pair experiment

In a fresh process (PID 3600), SDK initialization again returned true. `ParcelFileDescriptor.createSocketPair()` produced descriptors 214 and 215. Passing descriptor 214 and model `WM160` to `JNIUsbAccessory.native_OnUsbConnected` returned normally.

The peer was opened with `FileInputStream`. Reading was scheduled every 2 seconds; mode requests were scheduled 6 seconds later. The process exited before these callbacks produced output. Android logged `Process 3600 exited cleanly (0)` at 15:16:15.949 device time. Therefore this run supplies no raw bytes, no post-notification product list, and no mode-request result. The cause of the early exit is unresolved; neither descriptor acceptance nor product discovery can be inferred from a void JNI method returning.

Evidence: `scratch/android_runtime/resume-usb-socket.runtime.log` and Android logcat for PID 3600. A future probe should record transport writes immediately and establish process lifetime before relying on delayed polling.

## Capture target correction

DEX `classes_0451d00c.dex` declares:

```text
uav.sdk.datalink.bridge.jni.JNIDataLinkBridgeServer
native_bridge_send_raw_data(String, byte[], int) -> boolean
```

In the saved `runtime/sdk_init.txt`, `DataLinkBridgeServerManager.i(BridgeDataLinkInfo, byte[])` obtains the link ID, executes `array-length` on the byte array, and passes that length as the third argument. It is not a `type` argument. Whether ordinary native AOA writes pass through this Java bridge method remains unproven; capture on the actual USB descriptor avoids relying on that assumption.

## Current project behavior and remaining gap

The current beta's `Drone.set_flight_mode()` sends centered MobileRC gear bursts through `set_sticks_mobilerc`; `set_flight_mode_with_authority()` adds control acquisition and release. The UI mode choice invokes that path. The HUD uses `st.flight_mode_name`, decoded from OSD payload byte `0x1e & 0x7f`, rather than the selected UI value or a speed parameter. A local selection therefore cannot establish the displayed aircraft mode.

The archived SoftSwitch fix report describes reverted implementation work. Its isolated ARM64 serializer evidence still supports the candidate `06/59`, receiver FLYC, one-byte values `00/01/02`, but this session did not promote that candidate to a runtime-confirmed packet.

Next unresolved boundary: keep the process alive, observe native product discovery over a functioning mock link, then correlate each UI mode request with outgoing bytes. Aircraft ACK and fresh OSD evidence remain a separate validation step.
