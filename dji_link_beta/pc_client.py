#!/usr/bin/env python3
"""
pc_client.py — UNIFIED PC client for controlling DJI Mavic Mini 1 (WM160).

PC = brain. ALL functions for the drone: flight (WASD), gimbal, camera + settings,
telemetry (human-readable), video, and a CONSOLE for any DUML command (the whole
surface from reverse_docs) — i.e. literally every function of the app.

Transport:
  --pi HOST[:PORT]  via Pi bridge (raw AOA -> composite demux: DUML + video)
  --serial PORT     directly into remote controller/drone (serial, DUML only, no video)
  --sim             no hardware (loopback): test UI/control/console

Flight (motors) — only with --live AND after ARM (Enter). Gimbal/camera — always.

Control (hold): W/S pitch · A/D roll · Space/Shift throttle up/down · Q/E yaw
Hotkeys: Enter ARM/DISARM · T takeoff · L landing · H RTH(emergency) · C control on/off
        V ground-station · [ ] or Up/Down gimbal (HOLD) · N recenter · P photo · R record
        Z/X zoom +/- · Tab console · Esc exit
Console (Tab): takeoff/land/rth/gimbal <deg>/photo/rec start|stop/zoom <x>/iso <n>/
        mode photo|video/videofmt <res> <fps>/speed .../raw <set> <id> <hex> [recv]
"""

from __future__ import annotations
import argparse
import subprocess
import sys
import threading
import time

from duml import DumlPacket, DumlStream
from drone import Drone, DEV_FC, DEV_CAMERA, DEV_GIMBAL
from telemetry import Telemetry
from control import keys_to_sticks
import composite


# ---------------------------------------------------------------- video sink
VERBOSE = False


def log(*a):
    """Console log. Always on for milestones; VERBOSE adds per-packet detail."""
    print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


def vlog(*a):
    if VERBOSE:
        log(*a)


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
        self.capture_t0 = time.time()
        self.control = False        # whether control has been taken
        self.gs = False             # ground-station mode
        self.armed = False          # whether stick stream/takeoff is allowed
        self.axes = {"throttle": 0.0, "yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        self.lock = threading.Lock()
        self.running = True
        self.last_msg = ""
        self.fullscreen = True
        self.param_sets = {}
        self.params_logged = False
        self._tail = b""
        import media
        self.media = media.MediaClient(self.d, receiver=0x01)
        # Spectator mouse-look: X drives yaw rate, Y drives an absolute gimbal pitch.
        self.playback = False           # liveview (flight) vs playback (media) mode
        self.mouse_look = True          # off while the settings panel is open
        self.gimbal_pitch = 0.0         # accumulated target, degrees (down negative)
        self.mouse_yaw = 0.0            # yaw axis from the latest mouse motion

    def start_stats(self):
        """Heartbeat so a silent link is visibly silent rather than ambiguous."""
        def loop():
            while self.running:
                time.sleep(5)
                log(f"[stats] {self.stats()}")
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
            # Media responses (general cmd_set 0x00): file list / file data.
            if self.media and p.cmd_set == 0x00 and p.sender != 0x02:
                if p.cmd_id == 0x20:
                    n = len(self.media.on_list_response(p.payload))
                    self.msg(f"media: {n} file(s) — {self.media.last_note}")
                elif p.cmd_id == 0x1F:
                    self._media_raw = getattr(self, "_media_raw", 0) + len(p.payload)
                    log(f"[media] data chunk {len(p.payload)}B (total {self._media_raw}B) — "
                        "framing captured for offline finalisation")
            # FC param-info response (0x03/0xF0): pull the ASCII name so we learn the real
            # param names straight from the drone.
            if p.cmd_set == 0x03 and p.cmd_id in (0xF0, 0xF7, 0xF8, 0xF9) and p.sender != 0x02:
                # Log any FC param reply raw so we can see the format (name-parse below).
                log(f"[param] id=0x{p.cmd_id:02x} len={len(p.payload)} {p.payload.hex()}")
                if p.cmd_id in (0xF0, 0xF7) and len(p.payload) > 20:
                    name = p.payload[19:].split(b"\x00", 1)[0].decode("ascii", "replace")
                    if name.strip():
                        log(f"[param]   name={name}")
            self.tele.feed_packet(p)

    def start_rx(self):
        threading.Thread(target=self._rx_loop, daemon=True).start()

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
        while self.running:
            if self.live and self.armed and self.control:
                with self.lock:
                    a = dict(self.axes)
                try:
                    self.d.set_sticks(a["roll"], a["pitch"], a["yaw"], a["throttle"])
                except Exception:
                    pass
            time.sleep(0.05)

    # whether it's safe to send a flight command
    def _flight_ok(self) -> bool:
        if not self.live:
            self.last_msg = "flight commands are blocked (run with --live)"
            return False
        return True

    def msg(self, s):
        self.last_msg = s
        print("  " + s)

    def close(self):
        self.running = False
        try:
            if self.live and self.control:
                self.d.release_control()
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
            start = int(args[0]) if args else 0
            count = int(args[1]) if len(args) > 1 else 50
            import threading as _t, time as _tm
            def _dump():
                for i in range(start, start + count):
                    d.get_param_info(i); _tm.sleep(0.05)
            _t.Thread(target=_dump, daemon=True).start()
            cli.msg(f"requesting param names {start}..{start+count} (see [param] lines)")
        elif c in ("readparam", "rp", "param") and args:
            aliases = {"height": "g_config.flying_limit.max_height_0",
                       "radius": "g_config.flying_limit.max_radius_0",
                       "speed": "g_config.control.horiz_vel_atti_range_0",
                       "gpsenable": "g_config.gps_cfg.gps_enable_0",
                       "novice": "g_config.novice_cfg.max_height_0"}
            name = aliases.get(args[0], args[0])
            d.read_param(name); cli.msg(f"read {name}")
        elif c in ("takeoff", "to") and cli._flight_ok(): d.takeoff(); cli.msg("takeoff")
        elif c == "land": d.land(); cli.msg("land")
        elif c in ("rth", "gohome"): d.return_to_home(); cli.msg("RTH")
        elif c == "control": cli.control = args and args[0] == "on"; \
            (d.request_control() if cli.control else d.release_control()); cli.msg(f"control={cli.control}")
        elif c in ("gs", "groundstation"): cli.gs = args and args[0] == "on"; \
            d.set_ground_station_mode(cli.gs); cli.msg(f"ground_station={cli.gs}")
        elif c == "gimbal":
            if args and args[0] == "speed": d.gimbal_speed(float(args[1])); cli.msg("gimbal speed")
            else: d.gimbal_angle(float(args[0])); cli.msg(f"gimbal angle {args[0]}")
        elif c == "recenter": d.gimbal_recenter(); cli.msg("gimbal recenter")
        elif c == "photo": d.take_photo(); cli.msg("photo")
        elif c == "rec": (d.start_record() if args and args[0] == "start" else d.stop_record()); cli.msg("rec " + (args[0] if args else ""))
        elif c == "zoom": d.set_zoom(float(args[0])); cli.msg(f"zoom {args[0]}x")
        elif c == "mode": d.set_camera_mode(0 if args[0] == "photo" else 1); cli.msg(f"camera mode {args[0]}")
        elif c == "iso": d.set_iso(int(args[0])); cli.msg(f"iso {args[0]}")
        elif c == "ev": d.set_ev(int(args[0])); cli.msg(f"ev {args[0]}")
        elif c == "videofmt": d.set_video_format(int(args[0]), int(args[1])); cli.msg("video format")
        elif c == "codec": d.set_video_codec(args and args[0] == "h265"); cli.msg("codec")
        elif c == "raw":
            cs = int(args[0], 0); cid = int(args[1], 0)
            pl = bytes.fromhex(args[2]) if len(args) > 2 and args[2] != "-" else b""
            recv = int(args[3], 0) if len(args) > 3 else DEV_FC
            d.send_raw(cs, cid, pl, receiver=recv); cli.msg(f"raw {cs:#x}/{cid:#x} -> {recv:#x}")
        elif c == "help":
            cli.msg("takeoff land rth control on|off gs on|off gimbal <deg>|speed <dps> recenter photo rec start|stop zoom <x> mode photo|video iso ev videofmt <r> <f> raw <set> <id> <hex> [recv]")
        else:
            cli.msg(f"unknown command: {c} (help)")
    except Exception as e:
        cli.msg(f"error: {e}")


MOUSE_YAW_SENS = 0.010          # yaw axis per pixel of horizontal mouse motion
MOUSE_GIMBAL_SENS = 0.15        # gimbal degrees per pixel of vertical mouse motion


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
            _W("choice", "Flight mode", lambda v: self._try(lambda: d.set_flight_mode(v), f"mode {v}"),
               opts=["normal", "cinema", "sport"]),
            _W("slider", "Max altitude (m)", lambda v: self._try(lambda: d.set_max_altitude(v), f"max alt {v} m"),
               lo=15, hi=500, step=5, val=120),
            _W("slider", "Max distance (m)", lambda v: self._try(lambda: d.set_max_distance(v), f"max dist {v} m"),
               lo=15, hi=5000, step=50, val=500),
            _W("slider", "Max speed (m/s)", lambda v: self._try(lambda: d.set_horizontal_speed(v), f"max speed {v}"),
               lo=1, hi=15, step=1, val=6, note="needs a runtime param-hash (see Limitations)"),
            _W("slider", "Exposure (EV)", lambda v: self._try(lambda: d.set_ev(v), f"EV {v:+d}"),
               lo=-3, hi=3, step=1, val=0),
            _W("choice", "ISO", lambda v: self._try(lambda: d.set_iso(v), f"ISO {v}"),
               opts=[100, 200, 400, 800, 1600, 3200]),
            _W("choice", "Camera mode", lambda v: self._try(
                lambda: d.set_camera_mode(0 if v == "photo" else 1), v), opts=["photo", "video"]),
            _W("button", "Recenter gimbal", lambda v: self._try(lambda: d.gimbal_recenter(), "recenter")),
            _W("button", "Set home to here", lambda v: self._try(lambda: d.set_home_to_aircraft(), "home set")),
            _W("button", "Media: list SD card", lambda v: self._list_media()),
            _W("button", "Media: download first", lambda v: self._download_first()),
            _W("button", "Media: delete first", lambda v: self._delete_first()),
            _W("button", "Exit (resume flight)", lambda v: self._close()),
        ]

    def _close(self):
        self.open = False

    # ---- media (SD card) ----
    # The request encoders are proven; the list-response record stride and download chunk
    # framing are native, so the first real capture from the drone finalises them. Every
    # response is dumped for that. Download-by-index works once we have a file from a list.
    def _list_media(self):
        self._try(lambda: self.cli.media.request_list(0, 50),
                  "media: list requested (result appears when the drone replies)")

    def _download_first(self):
        files = self.cli.media.files
        if not files:
            self.cli.msg("media: list first (no files known yet)")
            return
        f = files[0]
        self._try(lambda: self.cli.media.download(f, f.file_name or "download.bin"),
                  f"media: downloading {f.file_name}")

    def _delete_first(self):
        files = self.cli.media.files
        if not files:
            self.cli.msg("media: list first (no files known yet)")
            return
        self._try(lambda: self.cli.media.delete(files[0]),
                  f"media: delete {files[0].file_name} requested")

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
                    # left third = previous, right third = next, middle = next
                    x0, _, x1 = (w.track or (w.rect.left, 0, w.rect.right))
                    self._cycle(w, -1 if pos[0] < (w.rect.left + w.rect.width // 2) - 30 else 1)
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
    def draw(self, screen, font, big):
        import pygame
        sw, sh = screen.get_size()
        pw = min(560, sw - 40)
        ph = min(len(self.w) * 46 + 70, sh - 40)
        px, py = (sw - pw) // 2, (sh - ph) // 2
        overlay = pygame.Surface((pw, ph)); overlay.set_alpha(240); overlay.fill((20, 22, 28))
        screen.blit(overlay, (px, py))
        screen.blit(big.render("Settings", True, (150, 200, 255)), (px + 20, py + 14))
        mouse = pygame.mouse.get_pos()
        for i, w in enumerate(self.w):
            y = py + 52 + i * 46
            row = pygame.Rect(px + 12, y, pw - 24, 40)
            w.rect = row
            hover = row.collidepoint(mouse)
            if hover or i == self.sel:
                pygame.draw.rect(screen, (36, 40, 50), row, border_radius=6)
            col = (235, 240, 245) if (hover or i == self.sel) else (200, 210, 220)
            screen.blit(font.render(w.label, True, col), (row.x + 10, y + 4))
            if w.note:
                screen.blit(font.render(w.note, True, (150, 150, 120)), (row.x + 10, y + 21))
            cx0 = row.x + int(row.width * 0.46)
            cx1 = row.right - 16
            if w.kind == "slider":
                ty = y + 20
                w.track = (cx0, cx1, ty)
                pygame.draw.line(screen, (70, 76, 88), (cx0, ty), (cx1, ty), 3)
                frac = (w.val - w.lo) / (w.hi - w.lo) if w.hi > w.lo else 0
                hx = int(cx0 + frac * (cx1 - cx0))
                pygame.draw.circle(screen, (120, 200, 255), (hx, ty), 7)
                screen.blit(font.render(str(w.val), True, col), (cx1 - 46, y + 2))
            elif w.kind == "choice":
                screen.blit(font.render("‹", True, col), (cx0, y + 4))
                screen.blit(font.render(str(w.opts[w.oi]), True, col), (cx0 + 24, y + 4))
                screen.blit(font.render("›", True, col), (cx1 - 12, y + 4))
            elif w.kind == "toggle":
                txt = "ON" if w.on else "OFF"
                screen.blit(font.render(txt, True, (120, 255, 160) if w.on else (200, 120, 120)),
                            (cx1 - 40, y + 4))
            elif w.kind == "button":
                screen.blit(font.render("▶", True, col), (cx1 - 16, y + 4))


# ---------------------------------------------------------------- UI (pygame)
def run_ui(cli: Client):
    import pygame
    KEYMAP = {pygame.K_w: "w", pygame.K_a: "a", pygame.K_s: "s", pygame.K_d: "d",
              pygame.K_q: "q", pygame.K_e: "e", pygame.K_SPACE: "space",
              pygame.K_LSHIFT: "shift", pygame.K_RSHIFT: "shift",
              pygame.K_LEFT: "left", pygame.K_RIGHT: "right"}
    pygame.init()
    WINDOWED_SIZE = (900, 600)

    def make_screen(full: bool):
        if full:
            return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        return pygame.display.set_mode(WINDOWED_SIZE, pygame.RESIZABLE)

    screen = make_screen(cli.fullscreen)
    pygame.display.set_caption("DJI Mavic Mini 1 — PC control")
    # Scale text with the window so fullscreen is readable rather than a corner of ants.
    fsize = 13
    font = pygame.font.SysFont("consolas", fsize)
    big = pygame.font.SysFont("consolas", fsize + 1, bold=True)
    clock = pygame.time.Clock()
    console = False
    cbuf = ""
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
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                cli.running = False
            elif settings.open:
                # The panel owns all input while it is up.
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    settings.open = False
                    set_grab(grabbed)
                else:
                    settings.handle(ev)
            elif ev.type == pygame.MOUSEMOTION and grabbed and not console:
                dx, dy = ev.rel
                # X -> yaw rate (spin in place), Y -> gimbal pitch (look up/down).
                cli.mouse_yaw = max(-1.0, min(1.0, dx * MOUSE_YAW_SENS))
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
                elif ev.key == pygame.K_k:
                    cli.d.request_i_frame(); cli.msg("keyframe requested")
                elif ev.key == pygame.K_g:
                    cli.dump_packets()      # capture packet samples for calibration
                elif ev.key == pygame.K_u:
                    cli.d.unlock_no_gps(True); cli.msg("no-GPS takeoff unlock sent (U)")
                elif ev.key == pygame.K_b:
                    cli.playback = not cli.playback
                    if cli.playback:
                        cli.d.enter_playback(); cli.msg("PLAYBACK mode (media) — B to return to liveview")
                    else:
                        cli.d.exit_playback(); cli.d.start_liveview(); cli.msg("LIVEVIEW mode")
                elif ev.key == pygame.K_TAB: console = True; cbuf = ""; set_grab(False)
                elif ev.key == pygame.K_RETURN:
                    if cli.live: cli.armed = not cli.armed; cli.msg(f"ARMED={cli.armed}")
                    else: cli.msg("ARM unavailable without --live")
                elif ev.key == pygame.K_t and cli._flight_ok(): cli.d.takeoff(); cli.msg("takeoff")
                elif ev.key == pygame.K_l: cli.d.land(); cli.msg("land")
                elif ev.key == pygame.K_h: cli.d.return_to_home(); cli.msg("RTH (emergency)")
                elif ev.key == pygame.K_c:
                    cli.control = not cli.control
                    (cli.d.request_control() if cli.control else cli.d.release_control())
                    cli.msg(f"control={cli.control}")
                elif ev.key == pygame.K_v:
                    cli.gs = not cli.gs; cli.d.set_ground_station_mode(cli.gs); cli.msg(f"ground_station={cli.gs}")
                elif ev.key == pygame.K_n:
                    cli.gimbal_pitch = 0.0; cli.d.gimbal_recenter(); cli.msg("recenter")
                elif ev.key == pygame.K_p: cli.d.take_photo(); cli.msg("photo")
                elif ev.key == pygame.K_r: cli.d.start_record(); cli.msg("record start (Shift+R stop)")
                elif ev.key == pygame.K_z: cli.d.set_zoom(2.0); cli.msg("zoom 2x")
                elif ev.key == pygame.K_x: cli.d.set_zoom(1.0); cli.msg("zoom 1x")

        if console or settings.open:
            grabbed = False
        elif cli.mouse_look and cli.mode != "sim" and not pygame.event.get_grab():
            grabbed = True; set_grab(True)

        # held keys -> sticks
        if not console and not settings.open:
            held = pygame.key.get_pressed()
            pressed = {name for k, name in KEYMAP.items() if held[k]}
            s = keys_to_sticks(pressed)
            # Mouse X adds to yaw (spectator spin). Decay to zero when the mouse stops,
            # since MOUSEMOTION only fires on movement.
            yaw = s.yaw + (cli.mouse_yaw if grabbed else 0.0)
            cli.mouse_yaw *= 0.6
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
        # Dark translucent backdrop behind the HUD so text is readable over bright video.
        hud_bg = pygame.Surface((min(screen.get_width(), 560), 250))
        hud_bg.set_alpha(150); hud_bg.fill((0, 0, 0))
        screen.blit(hud_bg, (0, 0))
        st = cli.tele.state
        line(screen, 8, f"DJI Mavic Mini 1 — {cli.mode}  {'LIVE' if cli.live else 'DRY'}"
                        f"  {'ARMED' if cli.armed else 'disarmed'}", big,
             (120, 255, 120) if cli.armed else (255, 180, 120))
        line(screen, 34, f"control={cli.control}  ground_station={cli.gs}"
                         f"  responding: {sorted(hex(a) for a in cli.responders)}")
        line(screen, 58, "── TELEMETRY ──", font, (150, 180, 255))
        line(screen, 78, f"mode={st.flight_mode_name}  satellites={st.satellites}  gps={st.gps_level}")
        line(screen, 96, f"battery={st.battery_pct}%  altitude={st.altitude_m}m  flying={st.is_flying}  motors={st.motors_on}")
        line(screen, 114, f"roll/pitch/yaw={st.roll}/{st.pitch}/{st.yaw}")
        if st.motor_fail_code:
            line(screen, 134, f"WON'T START: {st.motor_fail_reason}", font, (255, 120, 120))
        line(screen, 162, "── STICKS (WASD/Space/Shift/QE) ──", font, (150, 180, 255))
        with cli.lock:
            a = cli.axes
        line(screen, 182, f"thr={a['throttle']:+.2f} yaw={a['yaw']:+.2f} pitch={a['pitch']:+.2f} roll={a['roll']:+.2f}")
        line(screen, 214, "Mouse=yaw+gimbal  WASD=move  Space/Shift=throttle  Enter=ARM T=takeoff L=land H=RTH",
             font, (150, 150, 160))
        line(screen, 232, "C=control V=gs N=recenter P=photo R=record Z/X=zoom K=keyframe  Tab=console  Esc=settings",
             font, (150, 150, 160))
        if console:
            pygame.draw.rect(screen, (30, 30, 40), (0, 300, screen.get_width(), 60))
            line(screen, 308, "> " + cbuf + "_", big, (255, 255, 180))
        elif cli.last_msg:
            line(screen, 308, cli.last_msg, font, (200, 200, 120))
        if settings.open:
            settings.draw(screen, font, big)
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def discover_pi() -> tuple[str | None, int]:
    """Find the Pi and walk the user through getting online + powering the link on.

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
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose
    live = not args.dry and not args.sim      # flight enabled by default; ARM still gates motors

    from transport import NetTransport, CompositeTransport
    if args.sim:
        from transport import LogTransport
        t = LogTransport(verbose=True); mode = "sim"
        print("[sim] loopback — commands are printed, no hardware")
    elif args.serial:
        from transport import SerialTransport
        t = SerialTransport(args.serial); mode = "serial"
    else:
        # Default path: explicit --pi host, or auto-discover.
        if args.pi:
            host, _, p = args.pi.partition(":")
            port = int(p) if p else 9910
        else:
            host, port = discover_pi()
        if not host:
            print("\n[pi] Could not reach the Pi. Make sure it is powered and either on")
            print("     your Wi-Fi or broadcasting its 'PI_DJI_LINK-*' network, then retry.")
            print("     You can also test the interface with no hardware:  py -3 pc_client.py --sim")
            return 2
        # Pi = dumb jump-host: wrap outgoing in composite, demux incoming ourselves
        t = CompositeTransport(NetTransport(host, port)); mode = "pi"

    cli = Client(t, mode, live)
    cli.d.encrypt_config = (mode == "pi")   # radio path encrypts config; direct USB is plaintext
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
    cli.start_rx(); cli.start_sender()
    if not args.no_video:
        cli.start_video()
    cli.start_stats()
    try:
        run_ui(cli)
    except KeyboardInterrupt:
        log("interrupted — shutting down")
    except RuntimeError as e:
        print(e); return 2
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
