---
title: DJI Link v0.9.4 — Pi services migrated to C++
version: 0.9.4
prerelease: true
---

## Changed

- **The Pi jump-host services are now C++ binaries, not Python scripts.**
  `bridge.py`, `aoa_device.py`, `raw_gadget.py` and `netctl.py` were ported
  one-to-one (same mechanics, same retry timings, same wire behavior) to
  `src/pi/` (`dji-bridge`: the raw_gadget AOA↔TCP bridge on `:9910`;
  `dji-netctl`: the Wi-Fi/AP HTTP API on `:9911` plus the CLI). The release
  workflow cross-compiles both for aarch64 with a static link
  (`cmake/pi-aarch64.toolchain.cmake`) and ships them in the Pi bundle under
  `pi/bin/`; the Pi no longer needs Python for the services themselves.
  - `dji-netctl`'s `/status` replies stayed byte-compatible with the Python
    service — the PC client's discovery screen (`tests/netctl_parse_test.cpp`
    pins the JSON shape) does not tell the difference;
  - the bridge keeps its restart-on-suspend / 2 s AOA-retry /
    stale-frame-drop semantics, including the deliberate "restart the whole
    process on a dirty USB disconnect" move (via `execv("/proc/self/exe")`),
    exactly as the Python version did it;
  - existing Pi's upgrade themselves: `install-pi.sh` detects an old
    `python3 netctl.py` systemd unit and swaps it for a tiny
    `dji-netctl-wrapper` shim pointing at the new binary, which also keeps a
    rolled-back old bundle working, so the unattended health gate cannot
    strand the Pi without its Wi-Fi API;
  - `setup_pi.sh` now generates services that exec `bin/dji-bridge` /
    `bin/dji-netctl` directly.

> Everything below from **v0.9.3** is kept verbatim as stack context; this release only
> supersedes how the Pi-side services are built and packaged.

---

---
title: DJI Link v0.9.3 — Media protocol truth from DJI Fly DEX (beta client)
version: 0.9.3
prerelease: true
---

## Changed

- **Beta media protocol now matches what the DEX actually proves.** The beta client's
  media stack (`dji_link_beta/media.py`, `drone.py`, `pc_client.py`) previously entered
  camera mode `PLAYBACK(2)` and described itself from older, mutually contradictory
  reverse notes. A fresh static decompile of DJI Fly v1.21.4 (`jadx` over the 16 dex
  files under `dji_link_beta/reverse_docs/unpacked_app_dex/`) shows the real state of
  play:

  - entry into the media work mode is `cmdset=0x02 CAMERA, cmd=0x10 SetMode,
    payload=0x03` (`CameraWorkMode.MEDIA_DOWNLOAD=3`), not `PLAYBACK(2)` — the
    FileChannel LIST/DOWNLOAD answers only after that, which is why plain playback
    looked like "the camera ignores us";
  - the legacy 10-byte FileChannel header and the LIST / DOWNLOAD inner layouts in
    `media.py` match `FileSendPack` / `DataRequestList` / `DataRequestFile` exactly —
    those parts were already correct;
  - `DELETE` on `CmdIdCommon 0x28` is implemented only inside `libcrossplayback.so` —
    no Java packer exists in the dex — so the best-known `count u16 + indices u32`
    layout remains **capture-pending** until a wire trace on real hardware confirms it;
  - the camera answers FileChannel on `0x00/0x27` with a receive-side length split
    (`total = u16 >> 12`, `len = u16 & 0xFFF`) that is now documented for the next
    debugging session;
  - no standalone COUNT command exists in v1.21.4 — paging terminates on the
    `isPageLastFile` flag inside each record.

- **New reference note.** `dji_link_beta/reverse_docs/MEDIA_PROTOCOL_DEX_TRUTH.md`
  collects the byte-accurate findings with their dex/class provenance, and clearly
  separates what is statically proven from what still needs a wire capture.

- **`pc_client.py` gate display now tracks MEDIA_DOWNLOAD(3).** The beta UI treats
  mode 3 (not 2) as the media-ready state, matching the new entry command.

> Everything below from **v0.9.2** is kept verbatim as stack context; this release only
> supersedes the beta media-facing behaviour.

---

### v0.9.2 — Mouse stick pads, GPS/SATS HUD, slower AP rejoin

#### Changed

- **The yaw stick pad now reacts to mouse movement.** The bottom-right stick pads
  already visualized keyboard input (WASDEQ); the yaw pad now also tracks mouse
  movement with the same sensitivity used for actual yaw control, without consuming
  the accumulated mouse delta that flight control relies on.

- **PC-to-AP rejoin after a Pi uplink change is now deliberately slow.** The C++
  client disconnects first, waits several seconds, and only then reconnects, on all
  platforms: Windows (`netsh wlan disconnect`, then join), macOS (airport power
  off/on), and Linux (`nmcli dev disconnect`, then `wifi connect`). The Pi-side
  `netctl.py` pause between AP teardown and bring-up grew from 0.7 s to 2.5 s, so a
  too-fast disconnect/reconnect can no longer leave the client on a stale
  association.

- **The flight HUD now shows SATS · GPS like the beta.** Satellite count (u8 at
  OSD offset 0x24) and GPS level (bits 18..21 of the u32 at 0x20, 0..5) are decoded
  from the OSD push and displayed next to flight mode in the top-left card, matching
  the beta client's layout. GPS coordinates (lat/lon) remain out of scope.

- **A successful Pi uplink join now ends with one predictable AP refresh.** `netctl.py`
  schedules the refresh after its HTTP response can leave the Pi; failed uplink joins
  and ordinary uplink disconnects still do not interrupt a healthy field AP.

- **Flight limits in the C++ settings panel now use draggable sliders.** Maximum
  altitude, maximum distance, and RTH altitude can be moved directly across their full
  ranges, and each command is sent once when the pointer is released.

- **Beta compatibility was rechecked against the current client.** Its GPS/SATS flight
  check still runs every 10 seconds in the top-left HUD, Home remains available for
  manual verification, and the existing media test controls remain present.

- **Pi discovery no longer depends on internet.** `pc_client.py` now identifies the Pi
  through a command-free `/healthz` response at `10.42.0.1`; detailed uplink status is
  optional and cannot make an offline Pi look unreachable.

- **Internet checks no longer block `/status`.** They run in the background and the
  local API returns the cached result immediately when no uplink exists.

- **A healthy AP is not restarted after its last client disconnects.** AP restarts now
  require a real health failure or a confirmed uplink channel change.

- **The AP watchdog now verifies the uplink before changing channels.** It requires
  NetworkManager to report `wlan0` connected, `iw link` to report a real SSID/frequency,
  and the same channel to be observed twice. A transient scan/reassociation is no longer
  mistaken for a switch to the channel-6 fallback.

- **No uplink now explicitly means no AP retune.** In the field, with no external Wi-Fi
  or internet, `PI_DJI_LINK-*` remains serving DHCP and the Pi remains reachable at
  `10.42.0.1`; the watchdog does not restart a healthy local AP just because `wlan0` is
  disconnected.

- **Pi-side Wi-Fi/AP hardening plus beta test-client compatibility updates.** The
  release is still centered on the Raspberry Pi networking layer: `ap.sh`, `netctl.py`
  AP watchdog behavior, and the `dji-ap.service` setup generated by `setup_pi.sh`.

- **The Pi access point is treated as the lifeline, not as a side effect of internet.**
  `PI_DJI_LINK-*` must start even with no saved Wi-Fi, no uplink, and no internet. In
  that state `ap.sh` falls back to a plain local 2.4 GHz AP on channel 6, with the Pi
  reachable at `10.42.0.1`.

- **AP recovery now survives reboot better.** Repeated AP start failures are tracked under
  `/var/lib/dji-ap` instead of `/run`, so a reboot no longer erases the evidence that the
  previous AP start path was broken. Once the AP is confirmed healthy, the counter is
  cleared again.

- **Boot-time AP interface creation is more defensive.** `ap.sh` now unblocks Wi-Fi,
  reloads `brcmfmac` if available, disables Wi-Fi power save, waits longer for `wlan0` /
  phy registration, verifies that `uap0` is really an AP interface, and recreates it if it
  was left in the wrong state.

- **The AP boot path no longer disconnects `wlan0`.** A previous recovery path could
  cut the normal LAN/SSH uplink while trying to recover the Pi AP. The AP service owns
  `uap0` creation itself and never drops the station interface from `ap.sh` during
  ordinary service restarts.

- **`uap0` is created directly from the radio's udev event.** BCM43430 rejects hostapd
  when NetworkManager's P2P device takes the first virtual-interface slot. A normal
  systemd oneshot was still late enough to lose that race; the udev rule now creates
  `uap0` before NetworkManager can create P2P, and hostapd starts afterwards.

- **A temporary hostapd failure can no longer permanently disable the AP.** On the
  tested BCM43430, cfg80211 rejected a correct shared channel during early boot but
  accepted the identical configuration later. systemd now retries every 15 seconds
  until the lifeline AP is serving. The retry path reuses `uap0` and never disconnects
  `wlan0`, while netctl's separate watchdog still backs off after repeated failures.

- **The AP now follows the active kernel regulatory domain.** If a connected uplink's
  country IE changes the live domain, hostapd uses that value instead of forcing a
  conflicting country from the kernel command line. NetworkManager's connected state
  and the uplink channel must also remain stable before the first hostapd attempt.

- **BCM43430 now starts the AP in stable non-HT mode.** Hardware testing isolated the
  boot failure to `ieee80211n=1`: cfg80211 rejected the otherwise correct shared channel
  with `(extension) channel is disabled`, while `ieee80211n=0` reached `AP-ENABLED`
  immediately. 802.11g capacity remains well above control and telemetry requirements.

- **dnsmasq now belongs to the normal service lifecycle.** It starts from `ap.sh run`
  immediately before that process execs hostapd, instead of surviving `ExecStartPre`
  and triggering systemd's `service implementation deficiencies` warning.

- **Manual hotspot-off now remains off until reboot or an explicit hotspot-on.** Its
  marker moved outside systemd's `RuntimeDirectory`; stopping `dji-ap.service` no longer
  deletes the marker and causes the netctl watchdog to immediately undo the request.
  The early-created `uap0` is kept down and addressless rather than deleted, so a later
  hotspot-on reuses the boot-safe interface order.

- **Installing a new early-interface rule requires a real reboot.** The installer does
  not test hostapd against the already-initialized, wrong-order PHY or claim the AP is
  healthy. It defers AP startup, leaves the current LAN intact, and reports the required
  reboot explicitly.

- **Uplink changes no longer have to come from the PC client.** If NetworkManager
  reconnects `wlan0` after boot by itself, the `netctl.py` watchdog notices when the AP
  needs to retune to the uplink channel and restarts only the AP service.

- **A live uplink channel now always overrides the AP failure fallback.** On the Pi's
  single radio, pinning `uap0` to channel 6 after failed starts while `wlan0` is live on
  channel 7 guarantees a `channel is disabled` hostapd loop. The safe channel fallback
  is now used only while there is no usable uplink channel.

- **The beta flight HUD now performs an automatic GPS/SATS check every 10 seconds.** The
  result is shown in the top-left flight HUD card and logged as `[gps-check]`, so GPS
  level, satellite count, and aircraft position availability are visible during live
  testing without typing console commands.

- **Beta hardware utilities now use the app sender address `0x02`.** Older beta scripts
  that still built DUML packets as `0x0A` were updated so diagnostics and bench tests do
  not accidentally look like DJI Assistant traffic to the flight controller.

## Expected behavior

- With no internet and no external Wi-Fi, the laptop should still see and join
  `PI_DJI_LINK-*`, get DHCP from the Pi, and reach the Pi at `10.42.0.1`.
- When the Pi later joins an uplink Wi-Fi, clients on `PI_DJI_LINK-*` should keep access
  to the Pi after the explicit reassociation and gain internet through NAT when the
  uplink has internet.
- Reboot, repeated reconnects, uplink loss, uplink return, and power loss during a
  reconnect should not permanently break the Pi AP.

## Validation focus

- Repeated connect/disconnect cycles between the laptop and `PI_DJI_LINK-*`.
- Boot with no internet/uplink available at all.
- Join an uplink after the Pi AP is already serving clients.
- Drop the uplink, confirm the Pi AP remains reachable.
- Restore the uplink and confirm AP clients regain internet.
- Pull Pi power during an uplink reconnect, then boot again and confirm the AP returns.

### Not changed

- Media protocol behavior in v0.9.2 was not touched; v0.9.3 above superseded it.

<!--
Release checklist:
  1. Keep "version" above equal to the tag you push (tag v0.9.2 => version: 0.9.2).
  2. Commit UPDATE.md.
  3. git tag v0.9.2 && git push origin v0.9.2
Everything below the second "---" (except this comment) becomes the GitHub Release body.
These binaries are unsigned, so first launch shows a Gatekeeper (macOS) / SmartScreen
(Windows) warning — expected for a pre-release.
-->
