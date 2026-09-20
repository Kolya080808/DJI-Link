# WM160 flight-mode switching: `SoftSwitchMode`

The beta now follows the DJI Fly path for Mini/WM160 (UAV59). The mode request is a regular
DUML packet:

```
sender   = APP 0x02
receiver = FLYC 0x03
cmd_set  = RC  0x06
cmd_id   = 0x59 (`uav_rc_set_set_pts_channel_req`)
cmd_type = 0x40 (request with ACK)
payload  = [0x00 Sport | 0x01 Position/Normal | 0x02 Tripod/Cine]
```

The key detail is the receiver. `RC` is the command set, but the native
`OPR59RCAbstraction::SetSoftSwitchMode` constructor addresses the packet to FLYC. This is visible
in DJI Fly's ARM64 library (`libsdk_jni.so`): the setter is at `0x27a986c` and the request
constructor at `0x27aa72c`. The `verify_softswitch_native.py` harness executes those original
instructions with limited stubs and checks the addresses, command set/id, attribute, and all
three payloads.

The DEX enum independently confirms `SPORT=0`, `POSITION=1`, and `TRIPOD=2`. For UAV59/UAV96,
the Kotlin setter calls `KeySoftSwitchMode`, making this the actual mode-selection path rather
than the old virtual-RC fallback.

`fmodeauth` remains as a compatibility command for beta testing. It no longer requests virtual-stick authority or sends centered sticks: it sends the same SoftSwitchMode request as `fmode`, then waits for an ACK and a new OSD `FLYC_STATE`. An ACK alone is not considered a successful change. Sport is confirmed by state 31; Normal by GPS/hover states 1, 4, and 6; Cine by states 19 or 38. Joystick, takeoff, and RTH cannot falsely confirm the selection.

The raw OSD field `getModeChannel()` (dword `0x20`, bits 13–14) is now logged only as `OSD mode channel (raw)`. It is an FC selector that depends on firmware and configuration and must not be labelled with SoftSwitchMode values. The HUD continues to show the live `FLYC_STATE`, which is what the pilot sees.

## Offline checks

```
python dji_link_beta/test_flight_mode_gear.py
python dji_link_beta/reverse_docs/verify_softswitch_native.py
```

The second command runs the saved native serializer in an emulator with limited stubs. It does not use USB, the network, aircraft commands, or firmware changes; only a Pi run with `fmodeauth <mode>` can confirm that the aircraft accepts the packet.
