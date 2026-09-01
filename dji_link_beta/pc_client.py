#!/usr/bin/env python3
"""
pc_client.py — UNIFIED PC client for controlling DJI Mavic Mini 1 (WM160).

PC = brain. ALL functions for the drone: flight (WASD), gimbal, camera + settings,
telemetry (human-readable), video, and a CONSOLE for any DUML command (the whole
surface from reverse_docs) — i.e. literally every function of the app.

Startup: with no flags, a graphical start menu (gui.py) appears — connect via Pi
(with on-screen Pi discovery, AP-join, Wi-Fi pick), via serial, or run the simulator.
The console is reserved for logs; logs also go to logs/latest.log (see applog.py).

Transport (flags bypass the menu, for testing):
  --pi HOST[:PORT]  via Pi bridge (raw AOA -> composite demux: DUML + video)
  --serial PORT     directly into remote controller/drone (serial, DUML only, no video)
  --sim             no hardware (loopback): test UI/control/console

Flight (motors) — only with --live AND after ARM (Enter). Gimbal/camera — always.

Control (hold): W/S pitch · A/D roll · Space/Shift throttle up/down · Q/E yaw · Mouse-X yaw
Flight = virtual stick via 0x03/0x8E (DataFlycJoystick); control auto-enables after takeoff settles.
Hotkeys: Enter ARM/DISARM · T takeoff (auto-C) · C control on/off · L landing · H RTH(emergency)
        V ground-station(authority) · J stick-flag preset (velocity/BODY…) · N recenter · P photo
        R record TOGGLE · [ ]/Up/Down gimbal · Tab console · Esc settings
        F1 help · F3 hide/show HUD (clean video) · F11 fullscreen
        (media list/download auto-enter playback; no manual B/zoom/stick-flag keys)
Console (Tab): takeoff/land/rth · home here|<lat> <lon> · setalt <m>/setdist <m>/rthalt <m>
        fmode disabled until verified · hspeed <m/s> · rp height|radius|tilt · iso <n>/shutter <N>|auto/ev <n>
        rec start|stop · zoom <x> · gimbal <deg>|speed <dps> · raw <set> <id> <hex> [recv]
"""

from __future__ import annotations
import argparse
import struct
import subprocess
import sys
import threading
import time

from duml import DumlPacket, DumlStream
from drone import Drone, DEV_FC, DEV_CAMERA, DEV_GIMBAL
from telemetry import Telemetry
from control import keys_to_sticks
import applog
import composite


# ---------------------------------------------------------------- video sink
VERBOSE = False
_LOG = applog.get_logger()   # replaced by applog.setup() in main(); safe no-op before then


def log(*a):
    """Console log + file (logs/latest.log). Always on for milestones."""
    line = " ".join(str(x) for x in a)
    print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
    _LOG.info(line)


def vlog(*a):
    """Verbose detail: printed only in --verbose, but ALWAYS written to the log file."""
    line = " ".join(str(x) for x in a)
    if VERBOSE:
        print(f"[{time.strftime('%H:%M:%S')}] {line}", flush=True)
    _LOG.debug(line)


class VideoSink:
    """HEVC Annex-B stream -> ffplay window (robust, no heavy dependencies).

    WM160 liveview is H.265, not H.264: payloads on composite type 0x574A are already
    Annex-B (3-byte start code + 2-byte HEVC NAL header) and just concatenate.
    """
    # Decode at half size: 720p RGB is 2.7MB per frame (~80MB/s at 30fps)
    # through the pipe, which throttles the decoder for no visible gain.
    W, H = 640, 360

    def __init__(self):
        self.proc = None
        self.ok = False
        self.frames = 0
        self.bytes = 0
        self.latest = None    # newest decoded RGB frame, for the UI to blit
        try:
            self.proc = subprocess.Popen(
                # The drone never sends an IDR/CRA — the stream is all TRAIL_R with
                # periodic VPS/SPS/PPS + SEI, i.e. intra-refresh/GDR. Without showall the
                # decoder discards every frame while waiting for a keyframe that never
                # comes; with it, the picture converges over a few frames instead.
                # Decode to raw RGB on a pipe rather than using ffplay: it keeps the video
                # inside our own window, and lets us drop stale frames for low latency.
                ["ffmpeg", "-hide_banner", "-loglevel", "error",
                 "-fflags", "nobuffer", "-flags", "low_delay", "-flags2", "+showall",
                 "-err_detect", "ignore_err", "-avioflags", "direct",
                 "-fpsprobesize", "0", "-f", "hevc", "-i", "-",
                 "-flush_packets", "1", "-fps_mode", "passthrough",
                 "-vf", f"scale={self.W}:{self.H}", "-pix_fmt", "rgb24",
                 "-f", "rawvideo", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self.ok = True
            threading.Thread(target=self._reader, daemon=True).start()
            log("[video] decoder started")
        except FileNotFoundError:
            log("[video] ffmpeg NOT FOUND — no picture (install ffmpeg). Frames still counted.")

    def _reader(self):
        """Keep only the newest frame: an unread queue is exactly what makes video lag."""
        n = self.W * self.H * 3
        while self.proc and self.proc.stdout:
            buf = self.proc.stdout.read(n)
            if len(buf) < n:
                break
            self.frames += 1
            self.latest = buf
            if self.frames in (1, 30) or self.frames % 300 == 0:
                log(f"[video] decoded frame #{self.frames} ({self.W}x{self.H})")

    def on_frame(self, frame: bytes, is_key: bool):
        """Feed one HEVC payload to the decoder. 'frames' counts DECODED pictures now,
        so this only tracks bytes pushed in."""
        self.bytes += len(frame)
        if self.ok and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(frame)
            except Exception as e:
                log(f"[video] decoder write failed ({e}) — picture stopped")
                self.ok = False

    def close(self):
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------- client
class Client:
    def __init__(self, transport, mode: str, live: bool):
        self.t = transport
        self.mode = mode            # 'pi' | 'serial' | 'sim'
        self.live = live
        self.d = Drone(transport)
        self.tele = Telemetry()
        self.duml = DumlStream()
        self.video = VideoSink() if mode == "pi" else None
        self.demux = composite.CompositeDemux(
            on_duml=self._on_duml_payload,
            on_video=self._on_video_payload,
            on_unit=self._on_unit)
        self.n_video = 0            # composite units of video type
        self.video_bytes = 0
        self.n_duml = 0
        self.dump_f = None          # raw video payload dump, for offline analysis
        self.responders = {}
        self.packet_samples = {}    # (sender,set,id) -> (len, hex) for offline calibration
        self.capture_f = None       # append raw OSD/battery packets over time (--capture)
        self.raw_dump_f = None      # append every non-video composite unit (--dump-raw)
        self._ptable_f = None       # param-table dump (0x03/0xF0 responses) -> params_table.txt
        self.capture_t0 = time.time()
        self.control = False        # whether control has been taken
        self.gs = False             # ground-station mode
        self.armed = False          # whether stick stream/takeoff is allowed
        self.auto_c = True          # auto-enable control once the takeoff settles
        self._pending_auto_c = False
        self._takeoff_t = 0.0
        self.recording = False      # R toggles video recording (start/stop)
        # 0x03/0x8E flag byte. J cycles the useful presets to find what feels right.
        # 0x4A = velocity + BODY (heading-relative, pilot-natural); 0x48 = velocity + GROUND
        # (world-relative, feels diagonal); 0x0A = angle + BODY (tilt); 0x08 = angle + GROUND.
        self.stick_flags = [0x4A, 0x48, 0x0A, 0x08]
        self.stick_flag = 0x4A
        self.stick_mobilerc = False # M toggles 0x01/0x0A (primary) vs 0x01/0x02 (mobile-RC fallback)
        self.axes = {"throttle": 0.0, "yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        self.lock = threading.Lock()
        self.running = True
        self.return_to_menu = False  # set by "Exit to main menu": run_ui ends, main() re-shows menu
        self.show_hud = True         # F3 toggles the telemetry overlay (clean video when off)
        self.last_msg = ""
        self.gps_check_text = "GPS/SATS: waiting"
        self.gps_check_level = "warn"  # ok | warn | bad
        self.gps_check_at = 0.0
        self.fullscreen = True
        self.param_sets = {}
        self.params_logged = False
        self._tail = b""
        import media
        self.media = media.MediaClient(self.d, receiver=0x01)
        self.media_sel = 0              # index into self.media.files (selection for GUI ops)
        self._raw_dump = False          # True while raw DUML dump is running (logs every frame from drone)
        # Limit params we read back via 0xF8 and show on the HUD (the OSD low-freq push that
        # used to carry them isn't emitted by this drone). Map: param hash -> OsdState field.
        # All three are u16 metres, RW+EE, from the verified WM160 param table.
        self._limit_params = [
            ("g_config.flying_limit.max_height_0", "max_height_m"),
            ("g_config.flying_limit.max_radius_0", "max_distance_m"),
            ("g_config.go_home.fixed_go_home_altitude_0", "rth_altitude_m"),
        ]
        self._limit_hash_to_field = {}
        try:
            from param_hash import param_hash as _ph
            for _name, _field in self._limit_params:
                self._limit_hash_to_field[_ph(_name)] = _field
        except Exception:
            pass
        # Spectator mouse-look: X drives yaw rate, Y drives an absolute gimbal pitch.
        self.playback = False           # liveview (flight) vs playback (media) mode
        self.mouse_look = True          # off while the settings panel is open
        self.gimbal_pitch = 0.0         # accumulated target, degrees (down negative)
        self.mouse_yaw = 0.0            # yaw axis from the latest mouse motion
        self._frame_dx = 0.0           # horizontal mouse travel accumulated within the current frame

    def start_stats(self):
        """Heartbeat so a silent link is visibly silent rather than ambiguous."""
        def loop():
            while self.running:
                time.sleep(5)
                log(f"[stats] {self.stats()}")
        threading.Thread(target=loop, daemon=True).start()

    def start_gps_checks(self):
        """Every 10 seconds summarize GPS/SAT state for the flight HUD and log."""
        def loop():
            while self.running:
                st = self.tele.state
                sats = st.satellites
                gps = st.gps_level
                has_pos = st.drone_lat is not None and st.drone_lon is not None

                if sats is None and gps is None and not has_pos:
                    level = "bad"
                    text = "GPS/SATS: no OSD yet"
                else:
                    gps_ok = gps is not None and gps >= 4
                    sats_ok = sats is not None and sats >= 8
                    if gps_ok and sats_ok and has_pos:
                        level = "ok"
                    elif gps_ok or sats_ok or has_pos:
                        level = "warn"
                    else:
                        level = "bad"
                    pos = "pos" if has_pos else "no-pos"
                    text = (f"GPS/SATS: {sats if sats is not None else '-'} sats · "
                            f"lvl {gps if gps is not None else '-'} · {pos}")

                self.gps_check_text = text
                self.gps_check_level = level
                self.gps_check_at = time.time()
                log(f"[gps-check] {level.upper()} {text}")
                time.sleep(10)

        threading.Thread(target=loop, daemon=True).start()

    # receive
    def _on_unit(self, typ: int, payload: bytes):
        """Every composite unit, including channels we do not route."""
        if typ == 0x574B and VERBOSE:
            # The RC's own ASCII debug log — free insight into what it is doing.
            log(f"[rc-log] {payload.decode('utf-8', 'replace').strip()}")
        # Capture every non-video channel (DUML replies, 0x574C, etc.) so we can see
        # whether the drone answers a media request and what the unknown channels carry.
        if self.raw_dump_f is not None and typ != 0x574A:
            dt = time.time() - self.capture_t0
            self.raw_dump_f.write(f"{dt:7.2f} type=0x{typ:04x} len={len(payload)} "
                                  f"{payload[:120].hex()}\n")
            self.raw_dump_f.flush()

    def _on_video_payload(self, pl: bytes):
        """A composite unit of video type. Counted separately from decoded frames so the
        log distinguishes 'nothing arrives' from 'arrives but does not reassemble'."""
        self.n_video += 1
        self.video_bytes += len(pl)
        if self.n_video <= 3 or self.n_video % 200 == 0:
            vlog(f"[video] payload #{self.n_video} {len(pl)}B "
                f"total={self.video_bytes / 1024:.0f}KB hdr={pl[:16].hex()}")
        if self.dump_f:
            self.dump_f.write(pl)
        if self.video:
            # WM160 has no liveview header: the payload IS the HEVC stream.
            self._cache_param_sets(pl)
            self.video.on_frame(pl, False)

    # HEVC parameter sets: 3-byte start code + 2-byte NAL header.
    # nal_unit_type = byte>>1  ->  32 VPS (0x40), 33 SPS (0x42), 34 PPS (0x44).
    _PARAM_NALS = {0x40: "VPS", 0x42: "SPS", 0x44: "PPS"}

    def _cache_param_sets(self, pl: bytes):
        """Remember VPS/SPS/PPS and re-inject them before each IRAP.

        The drone sends them only rarely, so a client that joins mid-stream never gets
        them and the decoder sits on 'PPS id out of range' forever.
        """
        # NALs are split across payloads, so scan the join: a start code (and the whole
        # parameter set after it) can straddle the boundary. Scanning each payload on its
        # own missed every VPS/SPS/PPS that happened to land on a split.
        buf = self._tail + pl
        self._tail = buf[-16:]   # just enough to span a split start code + NAL header
        pl = buf
        i = 0
        while (i := pl.find(b"\x00\x00\x01", i)) >= 0:
            nal = pl[i + 3] if i + 4 < len(pl) else 0
            if nal in self._PARAM_NALS:
                j = pl.find(b"\x00\x00\x01", i + 3)
                self.param_sets[nal] = pl[i:j if j > 0 else len(pl)]
                if not self.params_logged:
                    vlog(f"[video] got {self._PARAM_NALS[nal]} ({len(self.param_sets)}/3)")
            elif nal >> 1 in (16, 17, 18, 19, 20, 21) and len(self.param_sets) == 3:
                # IRAP (keyframe): prepend the cached parameter sets so it can decode.
                self.video.on_frame(b"".join(self.param_sets[k]
                                             for k in sorted(self.param_sets)), True)
                if not self.params_logged:
                    self.params_logged = True
                    log("[video] IRAP + cached VPS/SPS/PPS injected — picture should start")
            i += 3

    def dump_packets(self, path="telemetry_dump.txt"):
        """Write one hex sample of every distinct (sender,set,id) packet seen, so the
        real telemetry layout can be calibrated from actual bytes."""
        try:
            with open(path, "w") as f:
                for (snd, cs, ci), (ln, hx) in sorted(self.packet_samples.items()):
                    f.write(f"sender=0x{snd:02x} set=0x{cs:02x} id=0x{ci:02x} len={ln}  {hx}\n")
            self.msg(f"telemetry dump -> {path} ({len(self.packet_samples)} packet types)")
        except OSError as e:
            self.msg(f"dump failed: {e}")

    def _on_duml_payload(self, payload: bytes):
        for p in self.duml.feed(payload):
            self.n_duml += 1
            if p.sender != 0x02:
                self.responders[p.sender] = time.time()
                self.packet_samples[(p.sender, p.cmd_set, p.cmd_id)] = (len(p.payload), p.payload.hex())
                # Time-series capture of the telemetry-bearing packets, so lifting the
                # drone shows which byte tracks height vs vertical speed.
                if self.capture_f and ((p.cmd_set == 0x03 and p.cmd_id == 0x43)
                                       or (p.cmd_set == 0x0D and p.cmd_id == 0x02)):
                    dt = time.time() - self.capture_t0
                    self.capture_f.write(f"{dt:7.2f} set=0x{p.cmd_set:02x} id=0x{p.cmd_id:02x} "
                                         f"{p.payload.hex()}\n")
                    self.capture_f.flush()
            vlog(f"[duml] rx sender=0x{p.sender:02x} recv=0x{p.receiver:02x} "
                 f"set=0x{p.cmd_set:02x} id=0x{p.cmd_id:02x} len={len(p.payload)} "
                 f"{p.payload[:24].hex()}")
            # Raw dump: log EVERY frame from the drone (skip our own echoes and the
            # noisy video/OSD floods) so we can see exactly what a media op triggers.
            if self._raw_dump and p.sender != 0x02 and not (p.cmd_set == 0x02 and p.cmd_id in (0x80, 0x81, 0x87)):
                log(f"[raw] src=0x{p.sender:02x} set=0x{p.cmd_set:02x} id=0x{p.cmd_id:02x} "
                    f"len={len(p.payload)} {p.payload[:32].hex()}")
            # Camera work-mode push (0x02/0x80) → playback gate.
            if self.media and p.cmd_set == 0x02 and p.cmd_id == 0x80 and p.sender != 0x02:
                prev = self.media._cam_mode
                self.media.note_camera_state(p.payload)
                if self.media._cam_mode != prev:
                    log(f"[media] cam mode {prev}→{self.media._cam_mode} "
                        f"({'PLAYBACK' if self.media._cam_mode==2 else 'other'})")
            # DEEP MEDIA LOG: every cmd_set 0x00 frame FROM the drone (not our echoes).
            # LIST reply should be 0x27, but log all so we catch it on any cmd_id.
            if self.media and p.cmd_set == 0x00 and p.sender != 0x02:
                log(f"[media] rx 0x00/0x{p.cmd_id:02x} src=0x{p.sender:02x} "
                    f"len={len(p.payload)} {p.payload[:32].hex()}")
            # FileChannel replies (0x00/0x27 GetPushFile) — LIST records & FILE data.
            if self.media and p.cmd_set == 0x00 and p.cmd_id == 0x27 and p.sender != 0x02:
                files = self.media.on_push(p.payload)
                if files is not None:
                    self.media_sel = 0
                    if not files:
                        self.msg(f"media: 0 files — {self.media.last_note}")
                    else:
                        self.msg(f"media: {len(files)} file(s) — {self.media.last_note}")
                        for i, f in enumerate(files):
                            kind = "🎬" if f.is_video else "📷"
                            marker = " ◀" if i == 0 else ""
                            self.msg(f"  [{i}] {kind} {f.file_name}{marker}")
            # Decode the 1-byte ACK to our 0x26/0x28 requests (0x00 = OK).
            if self.media and p.cmd_set == 0x00 and p.cmd_id in (0x26, 0x28) \
                    and p.sender != 0x02 and len(p.payload) == 1:
                cc = p.payload[0]
                _n = {0x00:"OK",0xD6:"PARAM_ERR",0xE0:"INVALID_CMD",0xE3:"INVALID_PARAM",
                      0xE4:"WRONG_STATE",0xE8:"NO_SDCARD"}
                log(f"[media] 0x{p.cmd_id:02x} ack=0x{cc:02x} {_n.get(cc,'?')}")
            # FC param-info response (0x03/0xF0): pull the ASCII name so we learn the real
            # param names straight from the drone.
            if p.cmd_set == 0x03 and p.cmd_id in (0xF0, 0xF7, 0xF8, 0xF9) and p.sender != 0x02:
                # Log any FC param reply raw so we can see the format (name-parse below).
                log(f"[param] id=0x{p.cmd_id:02x} len={len(p.payload)} {p.payload.hex()}")
                if p.cmd_id in (0xF8, 0xF9) and self._ptable_f is not None:
                    try:
                        self._ptable_f.write(f"id=0x{p.cmd_id:02x} {p.payload.hex()}\n")
                        self._ptable_f.flush()
                    except Exception:
                        pass
                # A read-param reply (0xF8) = [retcode u8][hash u32 LE][value]. When it's one of
                # our limit params (max height / max distance / RTH altitude) surface it on the
                # HUD — the OSD low-freq push that used to carry these isn't emitted by this
                # drone, so we read them via the param channel (on connect / after a write).
                if p.cmd_id == 0xF8 and len(p.payload) >= 7:
                    import struct as _st
                    rhash = _st.unpack_from("<I", p.payload, 1)[0]
                    field = self._limit_hash_to_field.get(rhash)
                    if field:                       # value = u16 metres (typeId 1, size 2) @0x05
                        setattr(self.tele.state, field,
                                float(_st.unpack_from("<H", p.payload, 5)[0]))
                if p.cmd_id in (0xF0, 0xF7) and len(p.payload) > 20:
                    name = p.payload[19:].split(b"\x00", 1)[0].decode("ascii", "replace")
                    if name.strip():
                        log(f"[param]   name={name}")
                    # Dump the full param-info struct to a file so we can rebuild the drone's
                    # REAL param table offline (index/hash/type/min/max/value + name).
                    try:
                        if self._ptable_f is None:
                            self._ptable_f = open("params_table.txt", "a")
                        self._ptable_f.write(f"id=0x{p.cmd_id:02x} {p.payload.hex()} name={name}\n")
                        self._ptable_f.flush()
                    except Exception:
                        pass
            self.tele.feed_packet(p)
            # SAT debug: green LED means GPS-locked, but HUD shows SAT=0 → the sat-count
            # offset (0x24, reverse-guessed) is likely wrong. Log the raw OSD-common push
            # + the parsed values once per second so the true offset can be found offline.
            if (p.cmd_set == 0x03 and p.cmd_id == 0x43 and p.sender != 0x02
                    and time.time() - getattr(self, "_sat_dbg_t", 0) > 1.0):
                self._sat_dbg_t = time.time()
                st = self.tele.state
                log(f"[sat] OSD 0x43 len={len(p.payload)} sats={st.satellites} "
                    f"gps_lvl={st.gps_level} raw@0x20..0x2c={p.payload[0x20:0x2c].hex()}")

    def start_rx(self):
        threading.Thread(target=self._rx_loop, daemon=True).start()
        # Read the flight limits (max height / distance / RTH altitude) once shortly after RX
        # is up, so the HUD shows the real enforced values (no OSD push carries them here).
        def _read_limits():
            import time as _t
            _t.sleep(1.5)
            for _name, _field in self._limit_params:
                try:
                    self.d.read_param(_name); _t.sleep(0.1)
                except Exception:
                    pass
        threading.Thread(target=_read_limits, daemon=True).start()

    def _rx_loop(self):
        n = 0
        total = 0
        while self.running:
            try:
                data = self.t.recv(timeout_ms=300)
            except Exception as e:
                log(f"[rx] link closed: {e}")
                break
            if not data:
                continue
            n += 1
            total += len(data)
            if n <= 3:
                log(f"[rx] raw #{n} {len(data)}B: {data[:32].hex()}")
            vlog(f"[rx] raw {len(data)}B (total {total / 1024:.0f}KB)")
            if self.mode == "pi":
                self.demux.feed(data)           # AOA: composite -> DUML/video
            else:
                self._on_duml_payload(data)      # serial: DUML directly

    def stats(self) -> str:
        v = self.video
        types = " ".join(f"0x{t:04x}={n}" for t, n in sorted(self.demux.type_counts.items()))
        return (f"rx: duml={self.n_duml} video_pl={self.n_video} "
                f"({self.video_bytes / 1024:.0f}KB) "
                f"frames={v.frames if v else 0} "
                f"params={'/'.join(self._PARAM_NALS[k] for k in sorted(self.param_sets)) or 'NONE'} "
                f"| units: {types}")

    def start_video(self):
        """Ask the drone to start streaming. Without this nothing is pushed to us."""
        if self.mode != "pi":
            return
        try:
            self.d.start_liveview()
            log("[video] start_liveview sent")
        except Exception as e:
            log(f"[video] start_liveview failed: {e}")
            return

        def keyframe_nag():
            # We join mid-GOP and the decoder stays blank until an IRAP arrives, so ask
            # for one a few times (the first requests can land before the camera is
            # streaming). Whether it worked is visible in ffplay, not from here.
            for _ in range(5):
                time.sleep(1)
                if not self.running:
                    return
                try:
                    self.d.request_i_frame()
                except Exception:
                    return
            log("[video] keyframe requested (K to ask again)")
        threading.Thread(target=keyframe_nag, daemon=True).start()

    # send sticks ~20 Hz
    def start_sender(self):
        threading.Thread(target=self._send_loop, daemon=True).start()

    def _send_loop(self):
        from telemetry import SDK_CTRL_DEVICE
        last_diag = 0.0
        while self.running:
            # Auto-enable control (C) once the auto-takeoff has SETTLED. We must not enable
            # mid-takeoff (FC not yet in joystick state -> the uncommanded-climb trap), so we
            # wait for: airborne + a settle delay + out of Assisted/Auto-Takeoff state
            # (10/11), with an 8 s hard fallback in case the state isn't reported.
            if (self.live and self.armed and self.auto_c and self._pending_auto_c
                    and not self.control and self._airborne()):
                elapsed = time.time() - self._takeoff_t
                in_takeoff = self.tele.state.flight_mode in (10, 11)
                if elapsed > 3.0 and (not in_takeoff or elapsed > 8.0):
                    self._pending_auto_c = False
                    self.d.enable_virtual_stick(True)
                    self.control = True
                    self.gs = True
                    self.msg("control auto-ON (takeoff settled)")
            if self.live and self.armed and self.control:
                # Decisive stick diagnostic straight into stdout (the log you share): is the
                # FC actually accepting us? mode should read Joystick(17); owner should read
                # APP(1). If they don't flip, the FC is ignoring the sticks, full stop.
                now = time.time()
                if now - last_diag >= 1.0:
                    last_diag = now
                    st = self.tele.state
                    owner = SDK_CTRL_DEVICE.get(st.ctrl_device, st.ctrl_device)
                    a0 = self.axes
                    log(f"[stick] mode={st.flight_mode_name} FC-owner={owner} "
                        f"flag=0x{self.stick_flag:02x} alt={st.altitude_m}m  tx roll/pitch/yaw/thr="
                        f"{a0['roll']:+.2f}/{a0['pitch']:+.2f}/{a0['yaw']:+.2f}/{a0['throttle']:+.2f}")
                with self.lock:
                    a = dict(self.axes)
                try:
                    # PRIMARY = DataFlycJoystick 0x03/0x8E (MSDK v4.18, the Mini-supporting
                    # SDK): flag 0x48 + 4 floats in physical units. See VIRTUAL_STICK_RESEARCH_2026.md.
                    # M toggles fallbacks: mobile-RC 0x01/0x02, then legacy TLV 0x01/0x0A.
                    if self.stick_mobilerc:
                        self.d.set_sticks_mobilerc(a["roll"], a["pitch"], a["yaw"], a["throttle"])
                    else:
                        self.d.set_sticks_velocity(a["roll"], a["pitch"], a["yaw"], a["throttle"],
                                                   flag=self.stick_flag)
                except Exception:
                    pass
            # 20 Hz (0.05s) — MSDK virtual-stick rate. DUML = DataFlycJoystick 0x03/0x8E
            # (flag 0x48 + 4 floats), authority via 0x03/0x80 NavigationSwitch (open=1/close=2).
            time.sleep(0.05)

    # whether it's safe to send a takeoff / motor-start command
    def _flight_ok(self) -> bool:
        if not self.live:
            self.last_msg = "flight commands are blocked (run with --live)"
            return False
        if not self.armed:
            self.last_msg = "not ARMED — press Enter to arm before takeoff"
            return False
        return True

    # the aircraft must be airborne before virtual stick is taken (MSDK: enable
    # virtual stick only after motor start / while flying — VIRTUAL_STICK_NATIVE.md).
    # The is_flying/motors flags stay 0 on Mini in ATTI/no-GPS (TELEMETRY_TRUTH.md),
    # so ALTITUDE is the reliable signal: baro height rises off the ground.
    AIRBORNE_ALT_M = 0.5
    def _airborne(self) -> bool:
        st = self.tele.state
        if st.altitude_m is not None and st.altitude_m > self.AIRBORNE_ALT_M:
            return True
        return bool(st.is_flying) or bool(st.motors_on)

    def msg(self, s):
        self.last_msg = s
        print("  " + s)
        _LOG.info(s)

    def close(self):
        self.running = False
        try:
            if self.live and self.control:
                # Proper hand-back: CLOSE_GROUND_STATION(2) + release, so control returns to
                # the RC. Plain release_control() (0x49/0x80=0) does NOT release on WM160.
                self.d.enable_virtual_stick(False)
                self.control = False
        except Exception:
            pass
        if self.dump_f:
            self.dump_f.close()
            log("[video] dump closed")
        if self.capture_f:
            self.capture_f.close()
            log("[capture] closed")
        if self.raw_dump_f:
            self.raw_dump_f.close()
            log("[dump-raw] closed")
        if self.video:
            self.video.close()
        self.d.stop()
        self.t.close()


# ---------------------------------------------------------------- console commands
def run_console_cmd(cli: Client, line: str):
    d = cli.d
    parts = line.strip().split()
    if not parts:
        return
    c = parts[0].lower()
    args = parts[1:]
    try:
        if c in ("params", "param") and not (args and not args[0].lstrip("-").isdigit()):
            # Enumerate FC param names from the drone: params [start] [count]
            # WM160 does NOT answer get-param-info-by-index (0xF0). Instead iterate the json
            # param NAMES and READ each by hash (0xF8): valid params reply with a multi-byte
            # struct, missing ones reply 1 byte. Replies are dumped (with hash) to params_table.txt;
            # map hash->name offline via param_hash. args: params [start] [count]
            start = int(args[0]) if args else 0
            count = int(args[1]) if len(args) > 1 else 100000
            import threading as _t, time as _tm, json as _j
            try:
                _raw = _j.load(open("flyc_param_infos.json"))
                _names = list(_raw.keys()) if isinstance(_raw, dict) else \
                    [x.get("name") for x in _raw if isinstance(x, dict) and x.get("name")]
            except Exception as e:
                _names = []
                cli.msg(f"could not load flyc_param_infos.json: {e}")
            _sel = _names[start:start + count]
            if cli._ptable_f is None:
                try: cli._ptable_f = open("params_table.txt", "a")
                except Exception: pass
            def _dump():
                from param_hash import param_hash as _ph
                for nm in _sel:
                    if cli._ptable_f:
                        cli._ptable_f.write(f"NAME 0x{_ph(nm):08x} {nm}\n"); cli._ptable_f.flush()
                    d.read_param(nm); _tm.sleep(0.06)
                cli.msg(f"param read sweep done ({len(_sel)} names) -> params_table.txt")
            _t.Thread(target=_dump, daemon=True).start()
            cli.msg(f"reading {len(_sel)} params by hash (start={start}); valid ones reply multi-byte")
        elif c in ("readparam", "rp", "param") and args:
            aliases = {"height": "g_config.flying_limit.max_height_0",
                       "radius": "g_config.flying_limit.max_radius_0",
                       "tilt": "g_config.mode_normal_cfg.tilt_atti_range_0",
                       "speed": "g_config.mode_normal_cfg.tilt_atti_range_0",
                       "gpsenable": "g_config.gps_cfg.gps_enable_0",
                       "novice": "g_config.novice_cfg.max_height_0"}
            name = aliases.get(args[0], args[0])
            d.read_param(name); cli.msg(f"read {name}")
        elif c in ("takeoff", "to") and cli._flight_ok():
            d.takeoff(); cli._pending_auto_c = True; cli._takeoff_t = time.time(); cli.msg("takeoff")
        elif c == "land": d.land(); cli.msg("land")
        elif c in ("rth", "gohome"): d.return_to_home(); cli.msg("RTH")
        elif c == "control":
            want = bool(args and args[0] == "on")
            if want and not cli._airborne():
                cli.msg("control on blocked: take off first (virtual stick only after motors start)")
            else:
                cli.control = want
                cli.gs = want
                d.enable_virtual_stick(want)
                cli.msg(f"virtual-stick={cli.control} (control+ground_station)")
        elif c in ("gs", "groundstation"): cli.gs = args and args[0] == "on"; \
            d.set_ground_station_mode(cli.gs); cli.msg(f"ground_station={cli.gs}")
        elif c == "gimbal":
            if args and args[0] == "speed": d.gimbal_speed(float(args[1])); cli.msg("gimbal speed")
            else: d.gimbal_angle(float(args[0])); cli.msg(f"gimbal angle {args[0]}")
        elif c == "recenter": d.gimbal_recenter(); cli.msg("gimbal recenter")
        elif c == "home":
            if args and args[0] in ("status", "st"):
                st = cli.tele.state
                cli.msg(f"home recorded={st.home_recorded}  gps={st.gps_level} sats={st.satellites} "
                        f"pos={st.drone_lat},{st.drone_lon}")
            elif args and args[0] in ("here", "current", "aircraft"):
                d.set_home_to_current_location(); cli.msg("home -> current location (needs GPS>=4); watch home= on HUD")
            elif len(args) >= 2:
                d.set_home_point(float(args[0]), float(args[1]))
                cli.msg(f"home -> {args[0]},{args[1]} (must be within ~30m of current home); watch home= on HUD")
            else:
                cli.msg("usage: home status | home here/current/aircraft | home <lat> <lon>")
        elif c in ("gps", "gpsstatus", "sat", "sats", "satellites"):
            st = cli.tele.state
            if c in ("sat", "sats", "satellites"):
                cli.msg(f"satellites={st.satellites} gps_level={st.gps_level}")
            else:
                cli.msg(f"gps_level={st.gps_level} satellites={st.satellites} "
                        f"lat={st.drone_lat} lon={st.drone_lon} home_recorded={st.home_recorded}")
        elif c in ("setalt", "maxalt"):
            d.set_max_altitude(int(args[0])); cli.msg(f"max alt {args[0]} m (verify: rp height)")
        elif c in ("setdist", "maxdist"):
            d.set_max_distance(int(args[0])); cli.msg(f"max dist {args[0]} m (verify: rp radius)")
        elif c == "rthalt":
            d.set_rth_altitude(int(args[0])); cli.msg(f"RTH alt {args[0]} m")
        elif c in ("fmode", "flightmode"):
            d.set_flight_mode(args[0])
        elif c in ("hspeed", "speed"):
            d.set_horizontal_speed(float(args[0])); cli.msg(f"horiz speed ~{args[0]} m/s (via tilt angle)")
        elif c == "photo": d.take_photo(); cli.msg("photo")
        elif c == "rec": (d.start_record() if args and args[0] == "start" else d.stop_record()); cli.msg("rec " + (args[0] if args else ""))
        elif c == "zoom": d.set_zoom(float(args[0])); cli.msg(f"zoom {args[0]}x")
        elif c == "mode": d.set_camera_mode(0 if args[0] == "photo" else 1); cli.msg(f"camera mode {args[0]}")
        elif c == "iso": d.set_iso(int(args[0])); cli.msg(f"iso {args[0]}")
        elif c == "shutter":
            if args and args[0] == "auto": d.set_shutter_auto(); cli.msg("shutter AUTO")
            else: d.set_shutter(int(args[0])); cli.msg(f"shutter 1/{args[0]} (smaller = brighter)")
        elif c == "ev": d.set_ev(int(args[0])); cli.msg(f"ev {args[0]}")
        elif c == "videofmt": d.set_video_format(int(args[0]), int(args[1])); cli.msg("video format")
        elif c == "codec": d.set_video_codec(args and args[0] == "h265"); cli.msg("codec")
        elif c == "raw":
            cs = int(args[0], 0); cid = int(args[1], 0)
            pl = bytes.fromhex(args[2]) if len(args) > 2 and args[2] != "-" else b""
            recv = int(args[3], 0) if len(args) > 3 else DEV_FC
            d.send_raw(cs, cid, pl, receiver=recv); cli.msg(f"raw {cs:#x}/{cid:#x} -> {recv:#x}")
        elif c in ("fetchmedia", "fm"):
            # Brute-fetch files by RAW index over the FileChannel (0x00/0x26 File), bypassing
            # the broken LIST. Sweep indices [from..to] with a delay, dump whatever the camera
            # sends to media_downloads/, then return to liveview.
            #   fetchmedia <from> <to> [delay_s]
            import threading as _t, os as _os
            lo = int(args[0]) if args else 0
            hi = int(args[1]) if len(args) > 1 else lo
            delay = float(args[2]) if len(args) > 2 else 1.0
            if not cli.media:
                cli.msg("fetchmedia: no media client"); return
            ddir = _os.path.join(_os.getcwd(), "media_downloads"); _os.makedirs(ddir, exist_ok=True)
            def _sweep():
                cli.media.enter_playback()
                cli.media.wait_playback_ready()
                cli.msg(f"fetchmedia: sweeping index {lo}..{hi} (delay {delay}s) → media_downloads/")
                for idx in range(lo, hi + 1):
                    dest = _os.path.join(ddir, f"DJI_{idx:04d}.bin")
                    cli.media.fetch_index(idx, dest)
                    cli.msg(f"fetchmedia: requested index {idx}")
                    time.sleep(delay)
                time.sleep(2.0)                       # let last chunks / reaper settle
                cli.media.close_file()                # flush any open transfers
                # Return to liveview: exit playback + restart stream.
                try:
                    d.exit_playback(); d.start_liveview(); d.request_i_frame()
                except Exception:
                    pass
                cli.playback = False
                cli.msg("fetchmedia: done — liveview restored, check media_downloads/ + [media] log")
            _t.Thread(target=_sweep, daemon=True).start()
        elif c == "help":
            cli.msg("takeoff land rth control on|off gs on|off gps sats home status|here|<lat> <lon> setalt <m> setdist <m> rthalt <m> rp height|radius hspeed <m/s> gimbal <deg>|speed <dps> recenter photo rec start|stop zoom <x> mode photo|video iso ev videofmt <r> <f> fetchmedia <from> <to> [delay] raw <set> <id> <hex> [recv]")
        else:
            cli.msg(f"unknown command: {c} (help)")
    except Exception as e:
        cli.msg(f"error: {e}")


MOUSE_YAW_SENS = 0.030          # yaw rate per pixel of mouse SPEED this frame (direct: mouse speed = yaw speed)
MOUSE_GIMBAL_SENS = 0.15        # gimbal degrees per pixel of vertical mouse motion
# FC virtual-stick (0x03/0x8E) physical scaling — conservative for first tests.
STICK_ROLLPITCH_MS = 3.0        # roll/pitch full-deflection m/s (MSDK velocity mode)
STICK_YAW_DPS = 45.0            # yaw full-deflection deg/s
STICK_VERT_MS = 1.5            # throttle full-deflection m/s (vertical velocity)


class _W:
    """One settings widget. kind: slider | choice | toggle | button."""
    def __init__(self, kind, label, action, **kw):
        self.kind = kind
        self.label = label
        self.action = action
        self.lo = kw.get("lo", 0)
        self.hi = kw.get("hi", 1)
        self.step = kw.get("step", 1)
        self.val = kw.get("val", self.lo)
        self.opts = kw.get("opts", [])
        self.oi = 0
        self.on = kw.get("on", False)
        self.note = kw.get("note")        # small hint shown under the label
        self.rect = None                  # main hit-box (set at draw)
        self.track = None                 # slider track (x0, x1, y) for hit/drag


class SettingsPanel:
    """ESC overlay, mouse-driven: click sliders/toggles/choice arrows/buttons, Exit button.

    Keyboard (arrows/Enter) still works as a fallback, but the primary interaction is the
    mouse — click a widget or drag a slider.
    """
    def __init__(self, cli: "Client"):
        self.cli = cli
        self.open = False
        self.sel = 0
        self.dragging = None              # a slider widget while the mouse button is held
        d = cli.d
        self.w = [
            _W("slider", "Max altitude (m)", lambda v: self._try(lambda: (d.set_max_altitude(v), d.read_param("g_config.flying_limit.max_height_0")), f"max alt {v} m"),
               lo=15, hi=500, step=5, val=120),
            _W("slider", "Max distance (m)", lambda v: self._try(lambda: (d.set_max_distance(v), d.read_param("g_config.flying_limit.max_radius_0")), f"max dist {v} m"),
               lo=15, hi=5000, step=50, val=500),
            _W("slider", "RTH altitude (m)", lambda v: self._try(lambda: (d.set_rth_altitude(v), d.read_param("g_config.go_home.fixed_go_home_altitude_0")), f"RTH alt {v} m"),
               lo=20, hi=500, step=5, val=30,
               note="height the drone climbs to before returning home (shown in HUD after readback)"),
            _W("slider", "Brightness (EV)", lambda v: self._try(lambda: d.set_ev(v), f"EV {v:+d}"),
               lo=-3, hi=3, step=1, val=0, note="main brighten/darken lever (auto exposure)"),
            _W("choice", "ISO", lambda v: self._try(
                lambda: (d.set_iso_auto() if v == "auto" else d.set_iso(v)),
                f"ISO {v}"),
               opts=["auto", 100, 200, 400, 800, 1600, 3200],
               note="auto by default; manual caps at 3200 (sensor ceiling)"),
            _W("choice", "Shutter (manual)", lambda v: self._try(
                lambda: (d.set_shutter_auto() if v == "auto" else d.set_shutter(v)),
                f"shutter {'auto' if v=='auto' else '1/'+str(v)}"),
               opts=["auto", 1000, 500, 250, 125, 60, 30, 15, 8, 4],
               note="SLOWER (smaller) = brighter. video floor ~1/30; photo can go lower"),
            _W("choice", "Camera mode", lambda v: self._try(
                lambda: d.set_camera_mode(0 if v == "photo" else 1), v), opts=["photo", "video"]),
            _W("button", "Recenter gimbal", lambda v: self._try(lambda: d.gimbal_recenter(), "recenter")),
            _W("button", "Set home to here", lambda v: self._try(lambda: d.set_home_to_current_location(), "home set")),
            _W("button", "Media: list SD card",    lambda v: self._list_media()),
            _W("button", "Media: download selected", lambda v: self._download_selected()),
            _W("button", "Media: delete selected",   lambda v: self._delete_selected()),
            _W("button", "Media: diagnose",          lambda v: self._diagnose_media()),
            _W("button", "Return to liveview",       lambda v: self._restore_liveview()),
            _W("button", "Exit to main menu",        lambda v: self._exit_to_menu()),
        ]

    def _close(self):
        self._restore_liveview()   # always exit playback when the panel closes
        self.open = False

    def _exit_to_menu(self):
        # End this flight session and go back to the start menu (so the user can reconnect,
        # switch to serial/sim, or just poke around). Resuming the current flight is Esc-again.
        self._restore_liveview()
        self.open = False
        self.cli.return_to_menu = True
        self.cli.running = False

    # ---- media helpers ----
    def _ensure_playback(self):
        if not self.cli.playback:
            try:
                self.cli.d.enter_playback(); self.cli.playback = True
            except Exception as e:
                self.cli.msg(f"media: couldn't enter playback: {e}")

    def _restore_liveview(self):
        """Exit playback (RECORD_VIDEO) and restart the video stream + keyframe.
        Skips restore while a download is still streaming (would kill the 0x27 flow).
        """
        if self.cli.media and self.cli.media.has_active_download():
            self.cli.msg("media: download in progress — not exiting playback yet")
            return
        # Exit if the flag says playback OR the camera actually reports PLAYBACK mode.
        in_playback = self.cli.playback or (self.cli.media and self.cli.media._cam_mode == 3)
        if in_playback:
            try:
                log("[media] restore: exit_playback → start_liveview → i-frame")
                self.cli.d.exit_playback()      # RECORD_VIDEO (mode 1) — resumes video stream
                self.cli.d.start_liveview()
                self.cli.d.request_i_frame()    # kick a fresh keyframe so the picture restarts
            except Exception as e:
                log(f"[media] restore error: {e}")
            self.cli.playback = False
        self.cli.msg("liveview restored")

    def _list_media(self):
        # request_list(playback_first=True) enters playback itself. Mark cli.playback
        # so _restore_liveview knows to switch back — else the camera stays stuck in
        # PLAYBACK and video freezes.
        self.cli.playback = True
        try:
            self.cli.media.request_list()
            self.cli.msg("media: list requested — files appear when drone replies")
        except Exception as e:
            self.cli.msg(f"media list error: {e}")
            self._restore_liveview()

    def _nav_file(self, delta: int):
        """Move selection cursor through the known file list."""
        files = self.cli.media.files
        if not files:
            self.cli.msg("media: list first (no files known yet)"); return
        self.cli.media_sel = (self.cli.media_sel + delta) % len(files)
        idx = self.cli.media_sel
        f = files[idx]
        kind = "🎬" if f.is_video else "📷"
        sz = f"{f.file_size/1_000_000:.1f}MB" if f.file_size else "?"
        self.cli.msg(f"media: selected [{idx}/{len(files)-1}] {kind} {f.file_name}  {sz}")

    def _selected_file(self):
        """Return the currently selected MediaFile, or None with an error msg."""
        files = self.cli.media.files
        if not files:
            self.cli.msg("media: list first (no files known yet)"); return None
        idx = min(self.cli.media_sel, len(files) - 1)
        return files[idx]

    def _media_dest(self, name: str) -> str:
        """Absolute path under ./media_downloads/, created on demand."""
        import os
        d = os.path.join(os.getcwd(), "media_downloads")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, name)

    def _download_selected(self):
        """Download the full-resolution file for the selected entry."""
        self._ensure_playback()
        f = self._selected_file()
        if not f: return
        dest = self._media_dest(f.file_name or f"download_{f.file_index}.bin")
        try:
            self.cli.media.download(f, dest)
            self.cli.msg(f"media: downloading {f.file_name} → media_downloads/ (arrives on 0x27)")
        except Exception as e:
            self.cli.msg(f"media download error: {e}")

    def _delete_selected(self):
        """Delete the selected file from the SD card."""
        self._ensure_playback()
        f = self._selected_file()
        if not f: return
        try:
            self.cli.media.delete(f)
            self.cli.msg(f"media: delete [{self.cli.media_sel}] {f.file_name} sent (ACK confirms)")
        except Exception as e:
            self.cli.msg(f"media delete error: {e}")

    def _diagnose_media(self):
        """Param sweep: LIST format is smali-confirmed correct but returns count=0,
        so sweep storage×subType to find the combo the camera answers with count>0.
        Watch the log for 'LIST PUSH: count=N' — any NON-ZERO marks the right params."""
        import threading
        self.cli.playback = True   # sweep enters playback; mark so liveview restores
        self.cli.msg("diag: sweeping LIST params (storage×subType) — watch 'LIST PUSH: count='")

        def run():
            try:
                self.cli.media.enter_playback()
                self.cli.media.wait_playback_ready()
                self.cli.media.sweep_list_params()
                time.sleep(8.0)
                self.cli.msg("diag: sweep done — check 'LIST PUSH: count=' lines for NON-ZERO")
            except Exception as e:
                self.cli.msg(f"diag error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def _try(self, fn, msg):
        try:
            fn(); self.cli.msg(msg)
        except NotImplementedError as e:
            self.cli.msg(f"{msg}: {e}")
        except AttributeError:
            self.cli.msg(f"{msg}: not implemented yet")
        except Exception as e:
            self.cli.msg(f"{msg}: {e}")

    def value_text(self, w: "_W"):
        if w.kind == "slider":
            return str(w.val)
        if w.kind == "choice":
            return str(w.opts[w.oi])
        if w.kind == "toggle":
            return "on" if w.on else "off"
        return ""

    # ---- input ----
    def handle(self, ev):
        import pygame
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self._click(ev.pos)
        elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
            self.dragging = None
        elif ev.type == pygame.MOUSEMOTION and self.dragging:
            self._drag(self.dragging, ev.pos[0])
        elif ev.type == pygame.MOUSEWHEEL:
            w = self.w[self.sel]
            if w.kind == "slider":
                self._set_slider(w, w.val + ev.y * w.step)
        elif ev.type == pygame.KEYDOWN:
            self._key(ev.key)

    def _click(self, pos):
        for i, w in enumerate(self.w):
            if w.rect and w.rect.collidepoint(pos):
                self.sel = i
                if w.kind == "slider":
                    self.dragging = w
                    self._drag(w, pos[0])
                elif w.kind == "choice":
                    # Arrows sit at x0 (‹) and x1 (›); split at their midpoint so a click on
                    # ‹ decrements and › increments. (Old code split at the row's centre, which
                    # fell to the RIGHT of ‹, so clicking ‹ wrongly incremented.)
                    x0, x1, _ = (w.track or (w.rect.left, w.rect.right, 0))
                    self._cycle(w, -1 if pos[0] < (x0 + x1) // 2 else 1)
                elif w.kind == "toggle":
                    w.on = not w.on; w.action(w.on)
                elif w.kind == "button":
                    w.action(None)
                return

    def _drag(self, w, mx):
        x0, x1, _ = w.track
        frac = 0 if x1 == x0 else max(0.0, min(1.0, (mx - x0) / (x1 - x0)))
        self._set_slider(w, w.lo + round(frac * (w.hi - w.lo) / w.step) * w.step)

    def _set_slider(self, w, v):
        v = max(w.lo, min(w.hi, int(v)))
        if v != w.val:
            w.val = v
            w.action(v)

    def _cycle(self, w, d):
        w.oi = (w.oi + d) % len(w.opts)
        w.action(w.opts[w.oi])

    def _key(self, key):
        import pygame
        if key in (pygame.K_UP, pygame.K_w):
            self.sel = (self.sel - 1) % len(self.w)
        elif key in (pygame.K_DOWN, pygame.K_s):
            self.sel = (self.sel + 1) % len(self.w)
        else:
            w = self.w[self.sel]
            d = 1 if key in (pygame.K_RIGHT, pygame.K_d) else (-1 if key in (pygame.K_LEFT, pygame.K_a) else 0)
            if key == pygame.K_RETURN and w.kind == "button":
                w.action(None)
            elif w.kind == "slider" and d:
                self._set_slider(w, w.val + d * w.step)
            elif w.kind == "choice" and d:
                self._cycle(w, d)
            elif w.kind == "toggle" and (d or key == pygame.K_RETURN):
                w.on = not w.on; w.action(w.on)

    # ---- draw ----
    # Widgets are grouped visually: a small heading is drawn above the first row of each
    # group so the panel reads as Flight / Camera / Media / (exit), not one long list.
    _GROUPS = [(0, "FLIGHT"), (3, "CAMERA"), (7, "HOME & MEDIA")]

    # Layout constants — one place to retune the rhythm of the panel.
    _PAD = 26           # inner padding of the card
    _ROW_H = 44         # height of one setting row
    _ROW_GAP = 6        # vertical gap between rows
    _HEAD_H = 30        # section-header band height
    _ARROW_W = 30       # width of a ‹ / › hit box
    _VAL_W = 92         # right-hand value column width

    def _fonts(self):
        # Build once, reuse every frame (SysFont per frame is expensive). segoe/dejavu is a
        # proper proportional UI face — a big step up from the console monospace.
        if getattr(self, "_fcache", None) is None:
            import gui as _g
            self._fcache = {
                "title":   _g._font(23, bold=True),
                "section": _g._font(12, bold=True),
                "label":   _g._font(18),
                "val":     _g._font(18, bold=True),
                "note":    _g._font(12),
                "hint":    _g._font(13),
                "arrow":   _g._font(22, bold=True),
                "pill":    _g._font(15, bold=True),
            }
        return self._fcache

    @staticmethod
    def _blit_mid(surf, font, text, col, cx, cy):
        img = font.render(text, True, col)
        surf.blit(img, img.get_rect(center=(cx, cy)))

    @staticmethod
    def _blit_right(surf, font, text, col, rx, cy):
        img = font.render(text, True, col)
        surf.blit(img, img.get_rect(midright=(rx, cy)))

    @staticmethod
    def _blit_left(surf, font, text, col, lx, cy):
        img = font.render(text, True, col)
        surf.blit(img, img.get_rect(midleft=(lx, cy)))

    def draw(self, screen, font, big):
        import pygame
        import gui as _g
        F = self._fonts()
        PAD, ROW_H, GAP, HEAD_H = self._PAD, self._ROW_H, self._ROW_GAP, self._HEAD_H
        sw, sh = screen.get_size()
        pw = min(660, sw - 48)
        n_heads = len(self._GROUPS)
        body_h = len(self.w) * (ROW_H + GAP) + n_heads * HEAD_H
        ph = min(body_h + 96, sh - 40)
        px, py = (sw - pw) // 2, (sh - ph) // 2

        # ---- card: drop shadow + solid panel + hairline border + title bar ----
        shadow = pygame.Surface((pw + 24, ph + 24), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 110), shadow.get_rect(), border_radius=16)
        screen.blit(shadow, (px - 12, py - 8))
        card = pygame.Surface((pw, ph), pygame.SRCALPHA)
        pygame.draw.rect(card, (*_g.PANEL, 250), card.get_rect(), border_radius=14)
        screen.blit(card, (px, py))
        pygame.draw.rect(screen, _g.PANEL_HI, (px, py, pw, ph), width=1, border_radius=14)

        self._blit_left(screen, F["title"], "Flight settings", _g.ACCENT, px + PAD, py + 26)
        pygame.draw.line(screen, _g.PANEL_HI, (px + PAD, py + 48), (px + pw - PAD, py + 48), 1)

        headmap = {i: name for i, name in self._GROUPS}
        mouse = pygame.mouse.get_pos()
        left_x = px + PAD                       # label column
        right_x = px + pw - PAD                 # right edge of controls
        ctrl_x = px + int(pw * 0.50)            # where the control column starts
        y = py + 60

        for i, w in enumerate(self.w):
            if i in headmap:
                self._blit_left(screen, F["section"], headmap[i], _g.MUTED, left_x, y + HEAD_H // 2 + 2)
                ly = y + HEAD_H - 6
                pygame.draw.line(screen, (44, 48, 60), (left_x + 90, ly), (right_x, ly), 1)
                y += HEAD_H

            row = pygame.Rect(px + 12, y, pw - 24, ROW_H)
            w.rect = row
            cy = row.centery
            hover = row.collidepoint(mouse)
            active = hover or i == self.sel
            if active:
                pygame.draw.rect(screen, _g.PANEL_HI, row, border_radius=8)

            is_btn = w.kind == "button"
            is_exit = w.label.startswith("Exit")
            base_col = _g.TEXT if active else (196, 206, 218)

            # ---- label (+ note under it, which lifts the label up a touch) ----
            if w.note:
                self._blit_left(screen, F["label"], w.label, base_col, left_x, cy - 8)
                self._blit_left(screen, F["note"], w.note, (150, 152, 130), left_x, cy + 11)
            else:
                lab_col = (_g.GOOD if is_exit else base_col) if is_btn else base_col
                self._blit_left(screen, F["label"], w.label, lab_col, left_x, cy)

            # ---- control on the right ----
            if w.kind == "slider":
                tx0, tx1 = ctrl_x, right_x - self._VAL_W
                ty = cy
                w.track = (tx0, tx1, ty)
                pygame.draw.line(screen, (58, 63, 78), (tx0, ty), (tx1, ty), 4)
                frac = (w.val - w.lo) / (w.hi - w.lo) if w.hi > w.lo else 0
                hx = int(tx0 + frac * (tx1 - tx0))
                pygame.draw.line(screen, _g.ACCENT, (tx0, ty), (hx, ty), 4)
                pygame.draw.circle(screen, _g.ACCENT_HI, (hx, ty), 8)
                pygame.draw.circle(screen, _g.PANEL, (hx, ty), 4)
                self._blit_right(screen, F["val"], str(w.val), base_col, right_x, cy)

            elif w.kind == "choice":
                # ‹ [ value ] › — arrows are real hit boxes at fixed positions; the value is
                # centred between them. w.track stores the two arrow centres for _click.
                la = pygame.Rect(0, 0, self._ARROW_W, ROW_H - 12)
                ra = pygame.Rect(0, 0, self._ARROW_W, ROW_H - 12)
                la.midleft = (ctrl_x, cy)
                ra.midright = (right_x, cy)
                for rr, glyph in ((la, "‹"), (ra, "›")):
                    hot = rr.collidepoint(mouse)
                    pygame.draw.rect(screen, (52, 57, 72) if hot else (36, 40, 52), rr, border_radius=7)
                    self._blit_mid(screen, F["arrow"], glyph, _g.ACCENT_HI if hot else _g.ACCENT,
                                   rr.centerx, rr.centery - 1)
                self._blit_mid(screen, F["val"], str(w.opts[w.oi]), base_col,
                               (la.right + ra.left) // 2, cy)
                w.track = (la.centerx, ra.centerx, cy)

            elif w.kind == "toggle":
                pill = pygame.Rect(0, 0, 66, 26); pill.midright = (right_x, cy)
                on = w.on
                pygame.draw.rect(screen, (40, 90, 55) if on else (70, 44, 48), pill, border_radius=13)
                knob_x = pill.right - 14 if on else pill.left + 14
                pygame.draw.circle(screen, _g.GOOD if on else _g.BAD, (knob_x, pill.centery), 9)
                self._blit_mid(screen, F["pill"], "ON" if on else "OFF",
                               _g.TEXT, pill.centerx - (10 if on else -10), pill.centery)

            elif is_btn:
                # Action row: a right-aligned pill. Exit is emphasised (green), others neutral.
                label = "Go" if not is_exit else "Menu"
                pill = pygame.Rect(0, 0, 84, 28); pill.midright = (right_x, cy)
                if is_exit:
                    pygame.draw.rect(screen, _g.GOOD if active else (46, 92, 60), pill, border_radius=8)
                    self._blit_mid(screen, F["pill"], "Menu", (12, 20, 14), pill.centerx, pill.centery)
                else:
                    pygame.draw.rect(screen, _g.PANEL_HI, pill, border_radius=8)
                    pygame.draw.rect(screen, _g.ACCENT if active else (70, 76, 92), pill, width=1, border_radius=8)
                    self._blit_mid(screen, F["pill"], "Run", _g.ACCENT_HI if active else _g.MUTED,
                                   pill.centerx, pill.centery)

            y += ROW_H + GAP

        # ---- footer hint ----
        pygame.draw.line(screen, _g.PANEL_HI, (px + PAD, py + ph - 34),
                         (px + pw - PAD, py + ph - 34), 1)
        self._blit_left(screen, F["hint"],
                        "Esc — resume flight     click ‹ ›, drag sliders, tap Run/Menu",
                        _g.MUTED, left_x, py + ph - 18)


# ---------------------------------------------------------------- flight HUD
_HUD_FONTS = None


def _hud_fonts():
    """Build the flight-HUD fonts once (proportional UI face, not the console monospace)."""
    global _HUD_FONTS
    if _HUD_FONTS is None:
        import gui as _g
        _HUD_FONTS = {
            "title": _g._font(15, bold=True),
            "chip":  _g._font(12, bold=True),
            "lbl":   _g._font(12),
            "val":   _g._font(17, bold=True),
            "unit":  _g._font(11),
            "big":   _g._font(20, bold=True),
            "small": _g._font(12),
        }
    return _HUD_FONTS


def _hud_chip(surf, F, text, rect, fg, border, fill=None):
    import pygame
    if fill:
        pygame.draw.rect(surf, fill, rect, border_radius=7)
    pygame.draw.rect(surf, border, rect, width=1, border_radius=7)
    img = F["chip"].render(text, True, fg)
    surf.blit(img, img.get_rect(center=rect.center))


def _hud_pad(surf, F, cx, cy, half, x, y, label):
    """A small square stick-pad with a dot at (x,y) in [-1,1]. Shows one axis pair."""
    import pygame, gui as _g
    r = pygame.Rect(cx - half, cy - half, half * 2, half * 2)
    pygame.draw.rect(surf, (0, 0, 0, 0), r)
    s = pygame.Surface((r.w, r.h), pygame.SRCALPHA); s.fill((10, 12, 16, 150))
    surf.blit(s, r.topleft)
    pygame.draw.rect(surf, (70, 76, 92), r, width=1, border_radius=6)
    pygame.draw.line(surf, (46, 50, 62), (r.centerx, r.top + 4), (r.centerx, r.bottom - 4), 1)
    pygame.draw.line(surf, (46, 50, 62), (r.left + 4, r.centery), (r.right - 4, r.centery), 1)
    dx = max(-1.0, min(1.0, x)); dy = max(-1.0, min(1.0, y))
    px = int(r.centerx + dx * (half - 6))
    py = int(r.centery - dy * (half - 6))
    pygame.draw.circle(surf, _g.ACCENT_HI, (px, py), 5)
    img = F["small"].render(label, True, _g.MUTED)
    surf.blit(img, img.get_rect(midtop=(r.centerx, r.bottom + 3)))


def _draw_flight_hud(screen, cli):
    """A clean, designed telemetry overlay: status card (top-left), REC badge (top-right),
    and a twin stick indicator (bottom-right). Replaces the old stacked text lines."""
    import pygame
    import gui as _g
    from telemetry import SDK_CTRL_DEVICE
    F = _hud_fonts()
    st = cli.tele.state
    sw, sh = screen.get_size()

    def right(font, text, col, rx, cy):
        img = font.render(text, True, col); screen.blit(img, img.get_rect(midright=(rx, cy)))

    def left(font, text, col, lx, cy):
        img = font.render(text, True, col); screen.blit(img, img.get_rect(midleft=(lx, cy)))

    # ---------- top-left status card ----------
    X, Y, W = 16, 16, 300
    pad = 14
    card_h = 252
    card = pygame.Surface((W, card_h), pygame.SRCALPHA)
    pygame.draw.rect(card, (18, 20, 26, 205), card.get_rect(), border_radius=12)
    screen.blit(card, (X, Y))
    pygame.draw.rect(screen, (48, 53, 66), (X, Y, W, card_h), width=1, border_radius=12)
    lx, rx = X + pad, X + W - pad
    y = Y + 20

    # title + mode chip
    left(F["title"], "DJI Mavic Mini 1", _g.TEXT, lx, y)
    mode_txt = f"{cli.mode.upper()} · {'LIVE' if cli.live else 'DRY'}"
    mc = pygame.Rect(0, 0, F["chip"].size(mode_txt)[0] + 16, 20); mc.midright = (rx, y)
    _hud_chip(screen, F, mode_txt, mc, _g.ACCENT_HI, (60, 90, 130), fill=(30, 40, 56))
    y += 26

    # battery bar
    pct = st.battery_pct if st.battery_pct is not None else 0
    bcol = _g.GOOD if pct > 50 else (_g.WARN if pct > 20 else _g.BAD)
    bar = pygame.Rect(lx, y, W - 2 * pad, 20)
    pygame.draw.rect(screen, (36, 40, 52), bar, border_radius=6)
    fillw = int((W - 2 * pad) * max(0, min(100, pct)) / 100)
    if fillw > 4:
        pygame.draw.rect(screen, bcol, (bar.x, bar.y, fillw, bar.h), border_radius=6)
    volt = f"{st.battery_mv/1000:.1f}V" if st.battery_mv else ""
    left(F["chip"], f"{pct}%", (12, 16, 20) if pct > 20 else _g.TEXT, lx + 8, bar.centery)
    right(F["chip"], volt, _g.TEXT, rx - 8, bar.centery)
    y += 30

    # status chips: ARMED / CONTROL / FC-owner
    owner = SDK_CTRL_DEVICE.get(st.ctrl_device, str(st.ctrl_device))
    chips = [
        ("ARMED" if cli.armed else "DISARMED", cli.armed),
        ("CTRL ON" if cli.control else "CTRL OFF", cli.control),
        (f"FC:{owner}", st.ctrl_device == 1),
    ]
    cxp = lx
    for text, on in chips:
        w = F["chip"].size(text)[0] + 16
        rc = pygame.Rect(cxp, y, w, 22)
        fg = _g.GOOD if on else _g.MUTED
        _hud_chip(screen, F, text, rc, fg, (fg[0]//3, fg[1]//3, fg[2]//3),
                  fill=(24, 34, 26) if on else (30, 32, 40))
        cxp += w + 6
    y += 32

    # telemetry grid — two columns of label/value
    def cell(col_x, label, value, vcol=_g.TEXT):
        left(F["lbl"], label, _g.MUTED, col_x, y + 7)
        left(F["val"], value, vcol, col_x, y + 24)

    c0, c1 = lx, lx + (W - 2 * pad) // 2 + 6
    _ft = f"{st.flight_time_s//60}:{st.flight_time_s%60:02d}" if st.flight_time_s is not None else "—"
    alt = f"{st.altitude_m:.1f} m" if st.altitude_m is not None else "—"
    cell(c0, "ALTITUDE", alt)
    cell(c1, "FLY TIME", _ft)
    y += 40
    cell(c0, "SATS · GPS", f"{st.satellites if st.satellites is not None else '—'} · {st.gps_level if st.gps_level is not None else '—'}")
    cell(c1, "MODE", st.flight_mode_name or "—")
    y += 40
    # Aircraft GPS position (drone_lat/lon from OSD 0x43) + home-set flag (coordinate
    # readback for home was dropped — never worked on WM160; only "recorded yes/no" is shown).
    _gps = "no GPS" if st.drone_lat is None else f"{st.drone_lat:.5f},{st.drone_lon:.5f}"
    _home = "set" if st.home_recorded else "not set"
    _g_ = lambda v: f"{v:g}m" if v is not None else "—"       # limit values, read via param 0xF8
    left(F["small"], f"GPS {_gps}  ·  home {_home}", _g.MUTED, lx, y + 6)
    left(F["small"], f"alt≤{_g_(st.max_height_m)}  ·  dist≤{_g_(st.max_distance_m)}  ·  RTH {_g_(st.rth_altitude_m)}",
         _g.MUTED, lx, y + 24)
    check_col = {"ok": _g.GOOD, "warn": _g.WARN, "bad": _g.BAD}.get(cli.gps_check_level, _g.MUTED)
    left(F["small"], cli.gps_check_text, check_col, lx, y + 42)
    left(F["small"], "F1 help · Esc settings · F3 hide", (110, 116, 130), lx, y + 60)

    # motor-start failure (only when relevant) — a red banner under the card
    if st.motor_fail_code:
        fb = pygame.Rect(X, Y + card_h + 6, W, 26)
        s = pygame.Surface((fb.w, fb.h), pygame.SRCALPHA); s.fill((80, 20, 24, 210))
        screen.blit(s, fb.topleft)
        left(F["chip"], f"WON'T START: {st.motor_fail_reason}", _g.BAD, fb.x + 10, fb.centery)

    # ---------- top-right REC badge ----------
    if st.is_recording:
        rt = f"REC {st.record_time_s or 0}s"
        w = F["title"].size(rt)[0] + 40
        rb = pygame.Rect(sw - 16 - w, 16, w, 30)
        s = pygame.Surface((rb.w, rb.h), pygame.SRCALPHA); s.fill((20, 12, 14, 200))
        screen.blit(s, rb.topleft)
        pygame.draw.rect(screen, (120, 40, 44), rb, width=1, border_radius=8)
        pygame.draw.circle(screen, _g.BAD, (rb.x + 16, rb.centery), 6)
        right(F["title"], rt, _g.TEXT, rb.right - 12, rb.centery)

    # ---------- bottom-right twin stick pads ----------
    with cli.lock:
        a = dict(cli.axes)
    half = 42
    gap = 24
    base_y = sh - half - 34
    rpad_cx = sw - 16 - half
    lpad_cx = rpad_cx - half * 2 - gap
    _hud_pad(screen, F, lpad_cx, base_y, half, a["yaw"], a["throttle"], "yaw / thr")
    _hud_pad(screen, F, rpad_cx, base_y, half, a["roll"], a["pitch"], "roll / pitch")


# ---------------------------------------------------------------- help overlay
# (key, what it does / WHEN to use it). Grouped into sections drawn in two columns.
_HELP_SECTIONS = [
    ("FLIGHT — do these in order", [
        ("Enter", "ARM / disarm motors — always first"),
        ("T", "take off (control auto-enables once stable)"),
        ("C", "control on/off — only AFTER takeoff"),
        ("L", "land (auto-releases control back to RC)"),
        ("H", "Return-to-Home — emergency recall"),
    ]),
    ("MOVE — hold while flying", [
        ("W / S", "pitch forward / back"),
        ("A / D", "roll left / right"),
        ("Space / Shift", "throttle up / down"),
        ("Q / E", "yaw left / right"),
        ("Mouse", "yaw (left-right) + gimbal tilt (up-down)"),
    ]),
    ("CAMERA", [
        ("P", "take a photo"),
        ("R", "start / stop recording"),
        ("[ ] or ↑ ↓", "gimbal tilt"),
        ("N", "recenter gimbal"),
    ]),
    ("VIEW & SYSTEM", [
        ("Esc", "flight settings panel"),
        ("Tab", "console (type any command)"),
        ("F1", "this help (Esc/F1 to close)"),
        ("F3", "hide / show the HUD"),
        ("F11", "fullscreen toggle"),
        ("V", "ground-station authority toggle"),
        ("U", "no-GPS takeoff unlock"),
        ("K", "request a video keyframe"),
        ("G", "dump packet samples (debug)"),
    ]),
]


def _draw_help(screen):
    import pygame
    import gui as _g
    F = _hud_fonts()
    fh = _g._font(24, bold=True)
    fsec = _g._font(14, bold=True)
    fkey = _g._font(14, bold=True)
    fdesc = _g._font(14)
    sw, sh = screen.get_size()
    pw = min(940, sw - 40)
    ph = min(600, sh - 40)
    px, py = (sw - pw) // 2, (sh - ph) // 2

    # dim the world, then the card
    dim = pygame.Surface((sw, sh), pygame.SRCALPHA); dim.fill((0, 0, 0, 150)); screen.blit(dim, (0, 0))
    card = pygame.Surface((pw, ph), pygame.SRCALPHA)
    pygame.draw.rect(card, (*_g.PANEL, 250), card.get_rect(), border_radius=14)
    screen.blit(card, (px, py))
    pygame.draw.rect(screen, _g.PANEL_HI, (px, py, pw, ph), width=1, border_radius=14)

    screen.blit(fh.render("Controls & help", True, _g.ACCENT), (px + 26, py + 20))
    pygame.draw.line(screen, _g.PANEL_HI, (px + 26, py + 56), (px + pw - 26, py + 56), 1)

    col_w = (pw - 52) // 2
    col_x = [px + 26, px + 26 + col_w]
    col_y = [py + 72, py + 72]
    # distribute sections: first two in the left column, rest in the right
    layout = [0, 0, 1, 1]
    for si, (title, rows) in enumerate(_HELP_SECTIONS):
        c = layout[si] if si < len(layout) else (si % 2)
        x = col_x[c]; y = col_y[c]
        screen.blit(fsec.render(title, True, _g.ACCENT_HI), (x, y))
        y += 26
        for key, desc in rows:
            screen.blit(fkey.render(key, True, _g.TEXT), (x + 6, y))
            screen.blit(fdesc.render(desc, True, (188, 196, 208)), (x + 150, y))
            y += 22
        y += 12
        col_y[c] = y

    hint = fdesc.render("Esc or F1 — close", True, _g.MUTED)
    screen.blit(hint, hint.get_rect(midbottom=(px + pw // 2, py + ph - 12)))


# ---------------------------------------------------------------- UI (pygame)
def run_ui(cli: Client):
    import pygame
    KEYMAP = {pygame.K_w: "w", pygame.K_a: "a", pygame.K_s: "s", pygame.K_d: "d",
              pygame.K_q: "q", pygame.K_e: "e", pygame.K_SPACE: "space",
              pygame.K_LSHIFT: "shift", pygame.K_RSHIFT: "shift",
              pygame.K_LEFT: "left", pygame.K_RIGHT: "right"}
    if not pygame.get_init():
        pygame.init()
    WINDOWED_SIZE = (900, 600)

    def make_screen(full: bool):
        if full:
            return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        return pygame.display.set_mode(WINDOWED_SIZE, pygame.RESIZABLE)

    # A display may already exist (the pre-flight menu created it); reuse it unless the
    # requested fullscreen state differs, so we don't drop and recreate the window.
    screen = pygame.display.get_surface()
    if screen is None or bool(screen.get_flags() & pygame.FULLSCREEN) != bool(cli.fullscreen):
        screen = make_screen(cli.fullscreen)
    pygame.display.set_caption("DJI Mavic Mini 1 — PC control")
    # Scale text with the window so fullscreen is readable rather than a corner of ants.
    fsize = 13
    font = pygame.font.SysFont("consolas", fsize)
    big = pygame.font.SysFont("consolas", fsize + 1, bold=True)
    clock = pygame.time.Clock()
    console = False
    cbuf = ""
    help_open = False
    gimbal_last = 0.0
    settings = SettingsPanel(cli)

    # Mouse-look grabs the cursor (relative motion, hidden pointer) like a game's
    # spectator camera. The settings panel and console release it so the pointer is
    # usable again.
    def set_grab(on: bool):
        pygame.event.set_grab(on)
        pygame.mouse.set_visible(not on)
        if on:
            pygame.mouse.get_rel()          # discard the jump to re-centre

    grabbed = cli.mouse_look and cli.mode != "sim"
    set_grab(grabbed)

    def line(surf, y, text, f=font, col=(200, 220, 200)):
        # Outline in the opposite luminance so text stays readable on any video: a bright
        # colour gets a dark halo (visible on light backgrounds), a dark colour a light one.
        halo = (0, 0, 0) if sum(col) > 330 else (255, 255, 255)
        base = f.render(text, True, halo)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            surf.blit(base, (10 + dx, y + dy))
        surf.blit(f.render(text, True, col), (10, y))

    while cli.running:
        # Close any media downloads that finished (no chunk for ~2s) and report them.
        if cli.media:
            for path, nbytes in cli.media.reap_finished_downloads():
                cli.msg(f"media: saved {path} ({nbytes} bytes)")
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                cli.running = False
            elif help_open:
                # Help overlay swallows input; Esc or F1 closes it.
                if ev.type == pygame.KEYDOWN and ev.key in (pygame.K_ESCAPE, pygame.K_F1):
                    help_open = False
                    set_grab(grabbed)
            elif settings.open:
                # The panel owns all input while it is up.
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    settings.open = False
                    settings._restore_liveview()   # back to live video if a media op used playback
                    set_grab(grabbed)
                else:
                    settings.handle(ev)
            elif ev.type == pygame.MOUSEMOTION and grabbed and not console:
                dx, dy = ev.rel
                # X -> yaw rate (= mouse speed), Y -> gimbal pitch (look up/down).
                # Sum horizontal travel over the frame; yaw is set from it in the stick block.
                cli._frame_dx += dx
                cli.gimbal_pitch = max(-90.0, min(30.0, cli.gimbal_pitch - dy * MOUSE_GIMBAL_SENS))
            elif ev.type == pygame.KEYDOWN:
                if console:
                    if ev.key == pygame.K_RETURN:
                        run_console_cmd(cli, cbuf); cbuf = ""; console = False
                    elif ev.key == pygame.K_ESCAPE:
                        console = False; cbuf = ""
                    elif ev.key == pygame.K_BACKSPACE:
                        cbuf = cbuf[:-1]
                    elif ev.unicode and ev.unicode.isprintable():
                        cbuf += ev.unicode
                    continue
                if ev.key == pygame.K_ESCAPE:
                    settings.open = True; set_grab(False)      # ESC opens settings
                elif ev.key == pygame.K_F11:
                    cli.fullscreen = not cli.fullscreen
                    screen = make_screen(cli.fullscreen)
                    font = pygame.font.SysFont("consolas", fsize)
                    big = pygame.font.SysFont("consolas", fsize + 1, bold=True)
                elif ev.key == pygame.K_F3:
                    cli.show_hud = not cli.show_hud    # hide all overlay text: clean video only
                    cli.msg("HUD " + ("shown" if cli.show_hud else "hidden — F3 to show"))
                elif ev.key == pygame.K_F1:
                    help_open = True; set_grab(False)  # controls & help overlay (Esc/F1 closes)
                elif ev.key == pygame.K_k:
                    cli.d.request_i_frame(); cli.msg("keyframe requested")
                elif ev.key == pygame.K_m:
                    cli.stick_mobilerc = not cli.stick_mobilerc
                    cli.msg(f"stick frame = {'0x01/0x02 mobile-RC' if cli.stick_mobilerc else '0x01/0x0A'}")
                elif ev.key == pygame.K_g:
                    cli.dump_packets()      # capture packet samples for calibration
                elif ev.key == pygame.K_u:
                    cli.d.unlock_no_gps(True); cli.msg("no-GPS takeoff unlock sent (U)")
                elif ev.key == pygame.K_TAB: console = True; cbuf = ""; set_grab(False)
                elif ev.key == pygame.K_RETURN:
                    if cli.live: cli.armed = not cli.armed; cli.msg(f"ARMED={cli.armed}")
                    else: cli.msg("ARM unavailable without --live")
                elif ev.key == pygame.K_t and cli._flight_ok():
                    cli.d.takeoff(); cli._pending_auto_c = True; cli._takeoff_t = time.time()
                    cli.msg("takeoff" + (" (control will auto-enable when settled)" if cli.auto_c else ""))
                elif ev.key == pygame.K_l:
                    cli.d.land()
                    cli._pending_auto_c = False          # cancel any pending post-takeoff auto-C
                    if cli.control:                      # release virtual stick so the FC lands cleanly
                        cli.control = False; cli.gs = False
                        cli.d.enable_virtual_stick(False)
                        cli.msg("land (control auto-OFF, returned to RC)")
                    else:
                        cli.msg("land")
                elif ev.key == pygame.K_h: cli.d.return_to_home(); cli.msg("RTH (emergency)")
                elif ev.key == pygame.K_c:
                    if not cli.control and not cli._airborne():
                        cli.msg("C blocked: take off FIRST — virtual stick must be enabled AFTER motors start (Enter -> T -> C)")
                    else:
                        cli.control = not cli.control
                        cli.d.enable_virtual_stick(cli.control)   # request control + ground-station
                        cli.gs = cli.control
                        cli.msg(f"virtual-stick={cli.control} (control+ground_station)")
                elif ev.key == pygame.K_v:
                    cli.gs = not cli.gs; cli.d.set_ground_station_mode(cli.gs); cli.msg(f"ground_station={cli.gs}")
                elif ev.key == pygame.K_n:
                    cli.gimbal_pitch = 0.0; cli.d.gimbal_recenter(); cli.msg("recenter")
                elif ev.key == pygame.K_p: cli.d.take_photo(); cli.msg("photo")
                elif ev.key == pygame.K_r:
                    if cli.recording:
                        cli.d.stop_record(); cli.recording = False; cli.msg("record STOP")
                    else:
                        cli.d.start_record(); cli.recording = True; cli.msg("record START (R again = stop)")

        if console or settings.open:
            grabbed = False
        elif cli.mouse_look and cli.mode != "sim" and not pygame.event.get_grab():
            grabbed = True; set_grab(True)

        # held keys -> sticks
        if not console and not settings.open:
            held = pygame.key.get_pressed()
            pressed = {name for k, name in KEYMAP.items() if held[k]}
            s = keys_to_sticks(pressed)
            # Mouse X -> yaw rate, directly proportional to how fast the mouse moved this
            # frame (mouse speed = yaw speed). Mouse still this frame -> 0 -> yaw stops.
            cli.mouse_yaw = max(-1.0, min(1.0, cli._frame_dx * MOUSE_YAW_SENS))
            cli._frame_dx = 0.0
            yaw = s.yaw + (cli.mouse_yaw if grabbed else 0.0)
            with cli.lock:
                cli.axes = {"throttle": s.throttle, "yaw": max(-1.0, min(1.0, yaw)),
                            "pitch": s.pitch, "roll": s.roll}
            # Gimbal: mouse Y sets an absolute pitch target; stream it a few times a
            # second. Bracket/arrow keys still nudge it for keyboard-only use.
            if held[pygame.K_RIGHTBRACKET] or held[pygame.K_UP]:
                cli.gimbal_pitch = min(30.0, cli.gimbal_pitch + 2)
            if held[pygame.K_LEFTBRACKET] or held[pygame.K_DOWN]:
                cli.gimbal_pitch = max(-90.0, cli.gimbal_pitch - 2)
            now = time.time()
            if now - gimbal_last > 0.1:
                gimbal_last = now
                try:
                    cli.d.gimbal_angle(cli.gimbal_pitch, duration_s=0.12)
                except Exception:
                    pass

        # render
        screen.fill((16, 18, 22))
        # Live video as the backdrop, HUD drawn over it — one window, not two.
        v = cli.video
        if v and v.latest:
            try:
                img = pygame.image.frombuffer(v.latest, (v.W, v.H), "RGB")
                sw, sh = screen.get_size()
                k = min(sw / v.W, sh / v.H)          # fit, keep aspect
                img = pygame.transform.smoothscale(img, (int(v.W * k), int(v.H * k)))
                screen.blit(img, ((sw - img.get_width()) // 2,
                                  (sh - img.get_height()) // 2))
            except Exception:
                pass
        if cli.show_hud:
            _draw_flight_hud(screen, cli)
        if console:
            pygame.draw.rect(screen, (30, 30, 40), (0, 300, screen.get_width(), 60))
            line(screen, 308, "> " + cbuf + "_", big, (255, 255, 180))
        elif cli.last_msg:
            line(screen, 308, cli.last_msg, font, (200, 200, 120))
        if settings.open:
            settings.draw(screen, font, big)
        if help_open:
            _draw_help(screen)
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def discover_pi() -> tuple[str | None, int]:
    """Console Pi discovery — the pre-GUI flow. The normal path now uses the graphical
    gui.preflight() menu + gui.DiscoveryScreen; this remains as a headless/no-display
    fallback and is not called by main() when a display is available.

    Find the Pi and walk the user through getting online + powering the link on.

    Follows the agreed flow: reach the Pi over the current network if possible; else
    join its access point; only ask about internet when we actually had to join the AP
    and the Pi has no uplink of its own.
    """
    try:
        import netfind
    except ImportError:
        log("[pi] netfind not available; pass --pi <ip>")
        return None, 9910

    log("[pi] looking for the Pi (LAN, then access point)...")
    r = netfind.discover()
    host = r["host"]
    if not host:
        return None, netfind.BRIDGE_PORT

    if r["via"] == "ap":
        log(f"[pi] joined the Pi's network '{r['joined_ap']}'")
    else:
        log(f"[pi] found the Pi at {host} on the current network")

    if r["needs_internet_prompt"]:
        ans = input("[pi] The Pi has no internet. Connect it to a Wi-Fi now? [y/N] ").strip().lower()
        if ans.startswith("y"):
            nets = netfind.pi_scan_wifi(host)
            for i, n in enumerate(nets[:15]):
                print(f"  {i:2d})  {n['signal']:3d}%  {n['security']:10s} {n['ssid']}")
            sel = input("  number (or blank to skip): ").strip()
            if sel.isdigit() and int(sel) < len(nets):
                ssid = nets[int(sel)]["ssid"]
                psk = input(f"  password for {ssid}: ").strip()
                res = netfind.pi_connect_wifi(host, ssid, psk)
                log(f"[pi] {'connected' if res.get('ok') else 'failed'}: {res.get('output','')[:120]}")

    print("\n=== Get the link ready ===")
    print("  1. Turn on the remote controller.")
    print("  2. Plug the RC into the Pi's data port (the phone cable into the Pi).")
    print("  3. Turn on the drone and wait for it to link to the RC.")
    input("Press Enter when done (or just continue if it is already up)... ")
    return host, netfind.BRIDGE_PORT


def run_wifi_setup(args) -> int:
    """Headless Pi Wi-Fi setup, same flow as the GUI setup button."""
    import netfind

    log("[wifi] finding the Pi for Wi-Fi setup...")
    r = netfind.discover(allow_ap_join=True)
    host = r.get("host")
    if not host:
        log("[wifi] no Pi found")
        return 2
    log(f"[wifi] Pi at {host} via {r.get('via')}")

    ssid = args.wifi_ssid
    psk = args.wifi_psk or ""
    if not ssid:
        nets = netfind.pi_scan_wifi(host)
        if not nets:
            log("[wifi] the Pi reported no visible Wi-Fi networks")
            return 2
        for i, n in enumerate(nets[:20]):
            print(f"  {i:2d}) {n.get('signal', 0):3d}%  {n.get('security',''):10s} {n.get('ssid','')}")
        sel = input("network number: ").strip()
        if not sel.isdigit() or int(sel) >= len(nets):
            log("[wifi] cancelled")
            return 1
        ssid = nets[int(sel)]["ssid"]
    if psk == "" and args.wifi_psk is None:
        psk = input(f"password for {ssid} (blank for open/saved): ").strip()

    res = netfind.pi_connect_wifi(host, ssid, psk)
    ok = bool(res.get("ok"))
    log(f"[wifi] {'connected' if ok else 'failed'}: {res.get('output','')[:180]}")
    st = netfind.wait_for_pi(host, timeout_s=30) or netfind.wait_for_pi(netfind.AP_GATEWAY, timeout_s=15)
    if st:
        log(f"[wifi] Pi reachable; internet={st.get('internet')} "
            f"uplink={st.get('uplink_ssid') or st.get('uplink')}")
    else:
        log("[wifi] Pi did not answer after Wi-Fi setup; reconnect to PI_DJI_LINK-* if needed")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="DJI Mavic Mini 1 PC client")
    # No arguments = the normal case: find the Pi, connect, fly. Everything below is an
    # optional override for testing/debugging — the app works with none of them.
    ap.add_argument("--pi", metavar="HOST[:PORT]",
                    help="skip discovery, use this Pi address")
    ap.add_argument("--serial", metavar="PORT", help="use a serial port instead of the Pi")
    ap.add_argument("--sim", action="store_true", help="no hardware (loopback), for UI testing")
    ap.add_argument("--dry", action="store_true", help="connect but block flight commands")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="log every DUML packet and raw chunk")
    ap.add_argument("--no-video", action="store_true", help="do not start the video stream")
    ap.add_argument("--windowed", action="store_true", help="windowed UI (F11 toggles)")
    ap.add_argument("--dump-video", metavar="FILE", help="record raw video payloads to FILE")
    ap.add_argument("--capture", metavar="FILE",
                    help="append raw OSD/battery packets over time (for offset calibration)")
    ap.add_argument("--dump-raw", metavar="FILE",
                    help="append every non-video composite unit (to see media/DUML replies)")
    ap.add_argument("--wifi", action="store_true",
                    help="only run Pi Wi-Fi setup, then exit")
    ap.add_argument("--wifi-ssid", metavar="SSID",
                    help="SSID to connect the Pi uplink to in --wifi mode")
    ap.add_argument("--wifi-psk", metavar="PSK",
                    help="password for --wifi-ssid (blank means open/saved)")
    args = ap.parse_args()

    global VERBOSE, _LOG
    VERBOSE = args.verbose
    _LOG = applog.setup(verbose=args.verbose)   # latest.log + dated archive + weekly cleanup
    log(f"[log] logging to {applog.LATEST}")
    live = not args.dry and not args.sim      # flight enabled by default; ARM still gates motors

    if args.wifi:
        return run_wifi_setup(args)

    from transport import NetTransport, CompositeTransport, LogTransport, SerialTransport
    import pygame, gui, netfind

    base_live = not args.dry     # --dry blocks flight; --sim forces it off below
    first = True                 # CLI flags pin the connection on the FIRST run only;
                                 # "Exit to main menu" then drops to the graphical menu.
    last_pi_host = None          # remember the Pi we connected to → fast re-connect (no rescan)
    while True:
      # ---- choose a connection (flags first run, else the graphical menu) ----
      if first and args.sim:
        spec = {"mode": "sim"}
      elif first and args.serial:
        spec = {"mode": "serial", "port": args.serial}
      elif first and args.pi:
        host, _, p = args.pi.partition(":")
        spec = {"mode": "pi", "host": host, "port": int(p) if p else 9910}
      else:
        if not pygame.get_init():
            pygame.init()
        surf = pygame.display.get_surface() or pygame.display.set_mode((900, 600), pygame.RESIZABLE)
        pygame.display.set_caption("DJI Mavic Mini 1 — PC control")
        spec = gui.preflight(surf, pygame.time.Clock(), netfind, applog.tail,
                             default_serial="", saved_host=last_pi_host)
      first = False
      if spec.get("mode") == "pi":
          last_pi_host = spec.get("host")   # reuse on the next menu re-entry

      if spec["mode"] == "quit":
        log("[menu] quit")
        return 0
      if spec["mode"] == "sim":
        t = LogTransport(verbose=True); mode = "sim"; live = False
        log("[sim] loopback — commands are printed, no hardware")
      elif spec["mode"] == "serial":
        t = SerialTransport(spec["port"]); mode = "serial"; live = base_live
      else:  # pi — dumb jump-host: wrap outgoing in composite, demux incoming ourselves
        t = CompositeTransport(NetTransport(spec["host"], spec["port"])); mode = "pi"; live = base_live

      cli = Client(t, mode, live)
      # Config-param writes: SIMPLE encryption is CONDITIONAL on the link being encrypted
      # (KEYVALUE_DUML_TRANSPORT.md). Our RC/AOA link is plaintext (plain 0x03/0x80 works),
      # so the FC expects plaintext 0xF9 too — forcing encryption made it silently drop the
      # unlock write. Keep plaintext; flip to True only if a future link negotiates encryption.
      cli.d.encrypt_config = False
      cli.fullscreen = not args.windowed
      if args.dump_video:
          cli.dump_f = open(args.dump_video, "wb")
          log(f"[video] dumping raw payloads to {args.dump_video}")
      if args.capture:
          cli.capture_f = open(args.capture, "w")
          log(f"[capture] logging OSD/battery packets to {args.capture}")
      if args.dump_raw:
          cli.raw_dump_f = open(args.dump_raw, "w")
          log(f"[dump-raw] logging non-video composite units to {args.dump_raw}")
      cli.start_rx(); cli.start_sender(); cli.start_gps_checks()
      if not args.no_video:
          cli.start_video()
      cli.start_stats()
      want_menu = False
      try:
          run_ui(cli)
          want_menu = cli.return_to_menu
      except KeyboardInterrupt:
          log("interrupted — shutting down")
      except RuntimeError as e:
          print(e); return 2
      finally:
          cli.close()
      if not want_menu:
          return 0
      log("[menu] returning to main menu")


if __name__ == "__main__":
    raise SystemExit(main())
