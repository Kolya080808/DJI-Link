# Record / Photo — WM160 (Mavic Mini 1) DUML truth

Source: baksmali of the DJI Fly app DEX (`unpacked_app_dex/`), namespace
`uav.midware.data.model.P3.*` (== `dji.midware` in older jars). All values below
are read directly from smali, not guessed.

## 1. Frame / addressing (confirmed from DataCameraSetRecord.start / DataConfig)

- sender  = DeviceType.APP    = **0x02**
- receiver = DeviceType.CAMERA = **0x01**
- cmd_set  = CmdSet.CAMERA     = **0x02**
- cmd_type = REQUEST (0x00) with NEEDACK = YES(data 2)  → wire ack byte **0x40**
  (drone.py `_cmd(..., ack=True)` → cmd_type 0x40 — correct)
- encrypt  = EncryptType.NO (0) → plaintext, no crypto on camera cmds

## 2. Command bytes (CmdIdCamera enum, confirmed)

| Action        | cmd_set | cmd_id | payload (1 byte) |
|---------------|---------|--------|------------------|
| Set record    | 0x02    | 0x02   | type             |
| Take photo    | 0x02    | 0x01   | type             |
| Set work mode | 0x02    | 0x10   | mode             |

### SetRecord.TYPE (DataCameraSetRecord$TYPE, confirmed)
- STOP   = 0
- START  = 1
- PAUSE  = 2
- RESUME = 3
- OTHER  = 7

### SetPhoto.TYPE (DataCameraSetPhoto$TYPE, confirmed)
- STOP=0, **SINGLE=1**, HDR=2, FULLVIEW=3, BURST=4, AEB=5, TIME=6,
  APP_FULLVIEW=7, TRACKING=8, RAWBURST=9, HDR_PLUS=10, HYPER_NIGHT=11,
  HYPER_LAPSE=12, PANORAMA_TRUE=13, HIGH_RESOLUTION=14, SMART_CAPTURE=15,
  BOKEH=0x62, PANORAMA=0x63, OTHER=0x12

### Work mode (DataCameraSetMode uses DataCameraGetMode$MODE.value())
MODE enum not present in this partial DEX; SetRecord path only needs RECORD.
From getMode push + app usage: **0 = photo/capture, 1 = record/video, 2 = playback**
(drone.py already sends 0x10 payload 1 for record, 2 for playback — consistent).

## 3. IMPORTANT: SetRecord.start() is a REPEATING timer

`DataCameraSetRecord.start(long period)` schedules the SendPack on a
`java.util.Timer` with `schedule(task, delay=0, period=period)` — i.e. the app
**re-sends the START frame on a fixed interval until stop()**. It is not a
one-shot. The single frame still triggers recording, but the app keeps
re-arming it. For our client a single START is normally enough; the repeat is
belt-and-suspenders. stop() cancels the timer (does NOT itself send STOP —
the caller sends setType(STOP).start() once).

## 4. Confirming record actually started — push 0x02/0x80 (GetPushStateInfo)

Payload is a status struct; getters (offset = byte offset into payload):
- **getRecordState()**: read u32 LE @0x00, `(v >> 6) & 0x3`
  → RecordType: 0=NO, 1=START, 2=STARTING, 3=STOP
- **getMode()**: u8 @0x04 (NEW_PLAYBACK folds to PLAYBACK)
- **getSDCardInsertState()**: u32 LE @0x00, `(v >> 9) & 1`  (1 = SD inserted)
- **getVideoRecordTime()**: u16 LE @0x1D (seconds; climbs while recording)
- **getSDCardFreeSize()**: u32 LE @0x09
- **getPhotoState()**: u32 LE @0x00 (bitfield)

So to verify: after START, poll the 0x02/0x80 push and check
`recordState == START(1)` and `videoRecordTime` (@0x1D) incrementing.

## 5. Preconditions (root-cause: why record may not start)

1. **Wrong work mode.** Record only works if camera is in RECORD/video mode
   (0x02/0x10 payload 1). If camera is in PLAYBACK (2) or PHOTO (0) the FC/cam
   silently drops the START. drone.py DOES set mode first — good.
2. **No settle time between set-mode and record.** drone.py fires 0x10 then
   0x02 back-to-back with no wait. The mode switch is async; the camera can
   still be transitioning when START arrives and drop it. The app gates the
   record button on the pushed mode actually being RECORD. Fix: after 0x10,
   wait for 0x02/0x80 push to report getMode()==RECORD (or a short ~300–500 ms
   sleep) before sending START.
3. **No SD card.** getSDCardInsertState bit @ (u32@0)>>9. Without SD the cam
   refuses to record. Should check before START and surface an error.
4. **take_photo sends HDR, not SINGLE** (see fix list) — separate bug.

## 6. Fix list for drone.py (do NOT edit code — user commits himself)

- `take_photo()` (line 399): payload `b"\x02"` is **HDR**, not single.
  Change to `b"\x01"` (SINGLE) for a normal photo. Optionally expose the type.
- `start_record()` (lines 403–404): insert a wait between set-mode(0x10,1)
  and START(0x02,1): either poll the 0x02/0x80 push until getMode()==RECORD,
  or a ~400 ms sleep. Otherwise START races the mode switch and is dropped.
- Add an SD-card guard: read SDCardInsertState from the 0x02/0x80 push before
  recording; abort with a clear message if absent.
- Add a post-START verification: confirm recordState==START(1) and/or
  videoRecordTime climbing from the 0x02/0x80 push; retry START once if not.
- (Optional) mirror the app's repeat: if a single START proves unreliable on
  HW, re-send START every ~1 s until the push confirms recording, then stop
  re-sending (matches DataCameraSetRecord's Timer behaviour).
- stop_record(): payload `b"\x00"` (STOP) is correct.

## 7. Confidence
- Command bytes/enums/framing: HIGH (direct from smali).
- Push offsets for verification: HIGH (direct from getters).
- MODE numeric values: MEDIUM (enum class absent from this partial DEX;
  photo=0/record=1/playback=2 inferred from push getter + existing drone.py,
  consistent with prior CAMERA docs).

<!-- PROGRESS: 100% — complete: bytes, preconditions, root-cause, drone.py fix list -->
