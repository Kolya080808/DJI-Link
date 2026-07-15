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
import liveview


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
        self.reasm = liveview.LiveviewReassembler(self.video.on_frame) if self.video else None
        self.demux = composite.CompositeDemux(
            on_duml=self._on_duml_payload,
            on_video=self._on_video_payload,
            on_unit=self._on_unit)
        self.n_video = 0            # composite units of video type
        self.video_bytes = 0
        self.n_duml = 0
        self.n_video_bad = 0        # video payloads the liveview parser rejected
        self.dump_f = None          # raw video payload dump, for offline analysis
        self.responders = {}
        self.control = False        # whether control has been taken
        self.gs = False             # ground-station mode
        self.armed = False          # whether stick stream/takeoff is allowed
        self.axes = {"throttle": 0.0, "yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        self.lock = threading.Lock()
        self.running = True
        self.last_msg = ""
        self.fullscreen = True
        # WM160 sends plain HEVC Annex-B on composite type 0x574A: no liveview header to
        # strip, the payloads concatenate straight into a decodable stream.
        self.raw_video = True
        self.param_sets = {}
        self.params_logged = False
        self._tail = b""

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

    def _on_video_payload(self, pl: bytes):
        """A composite unit of video type. Counted separately from decoded frames so the
        log distinguishes 'nothing arrives' from 'arrives but does not reassemble'."""
        self.n_video += 1
        self.video_bytes += len(pl)
        if self.n_video <= 3 or self.n_video % 200 == 0:
            log(f"[video] payload #{self.n_video} {len(pl)}B "
                f"total={self.video_bytes / 1024:.0f}KB hdr={pl[:16].hex()}")
        if self.dump_f:
            self.dump_f.write(pl)
        if self.reasm and not composite.feed_video_payload(self.reasm, pl):
            self.n_video_bad += 1
            if self.raw_video and self.video:
                # Expected on WM160: no liveview header, the payload IS the HEVC stream.
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
                    log(f"[video] got {self._PARAM_NALS[nal]} ({len(self.param_sets)}/3)")
            elif nal >> 1 in (16, 17, 18, 19, 20, 21) and len(self.param_sets) == 3:
                # IRAP (keyframe): prepend the cached parameter sets so it can decode.
                self.video.on_frame(b"".join(self.param_sets[k]
                                             for k in sorted(self.param_sets)), True)
                if not self.params_logged:
                    self.params_logged = True
                    log("[video] IRAP + cached VPS/SPS/PPS injected — picture should start")
            i += 3

    def _on_duml_payload(self, payload: bytes):
        for p in self.duml.feed(payload):
            self.n_duml += 1
            if p.sender != 0x0A:
                self.responders[p.sender] = time.time()
            vlog(f"[duml] rx sender=0x{p.sender:02x} recv=0x{p.receiver:02x} "
                 f"set=0x{p.cmd_set:02x} id=0x{p.cmd_id:02x} len={len(p.payload)} "
                 f"{p.payload[:24].hex()}")
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
                f"({self.video_bytes / 1024:.0f}KB) unparsed={self.n_video_bad} "
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
        if c in ("takeoff", "to") and cli._flight_ok(): d.takeoff(); cli.msg("takeoff")
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
    gimbal_prev = 0

    def line(surf, y, text, f=font, col=(200, 220, 200)):
        surf.blit(f.render(text, True, col), (10, y))

    while cli.running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                cli.running = False
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
                if ev.key == pygame.K_ESCAPE: cli.running = False
                elif ev.key == pygame.K_F11:
                    cli.fullscreen = not cli.fullscreen
                    screen = make_screen(cli.fullscreen)
                    font = pygame.font.SysFont("consolas", fsize)
                    big = pygame.font.SysFont("consolas", fsize + 1, bold=True)
                elif ev.key == pygame.K_k:
                    cli.d.request_i_frame(); cli.msg("keyframe requested")
                elif ev.key == pygame.K_TAB: console = True; cbuf = ""
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
                elif ev.key == pygame.K_n: cli.d.gimbal_recenter(); cli.msg("recenter")
                elif ev.key == pygame.K_p: cli.d.take_photo(); cli.msg("photo")
                elif ev.key == pygame.K_r: cli.d.start_record(); cli.msg("record start (Shift+R stop)")
                elif ev.key == pygame.K_z: cli.d.set_zoom(2.0); cli.msg("zoom 2x")
                elif ev.key == pygame.K_x: cli.d.set_zoom(1.0); cli.msg("zoom 1x")

        # held keys -> sticks
        if not console:
            held = pygame.key.get_pressed()
            pressed = {name for k, name in KEYMAP.items() if held[k]}
            # Gimbal speed is a rate, not a step: it must be streamed while the key is
            # held and zeroed on release, otherwise one keypress either does nothing
            # visible or keeps the gimbal rotating.
            # Arrow keys too: pygame reports the typed symbol, so [ and ] do not fire
            # on a non-Latin keyboard layout.
            g = (30 if (held[pygame.K_RIGHTBRACKET] or held[pygame.K_UP]) else 0) \
                - (30 if (held[pygame.K_LEFTBRACKET] or held[pygame.K_DOWN]) else 0)
            if g or gimbal_prev:
                try:
                    cli.d.gimbal_speed(g)
                except Exception:
                    pass
            gimbal_prev = g
            s = keys_to_sticks(pressed)
            with cli.lock:
                cli.axes = {"throttle": s.throttle, "yaw": s.yaw, "pitch": s.pitch, "roll": s.roll}

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
        line(screen, 214, "Enter=ARM T=takeoff L=land H=RTH C=control V=gs [ ]=gimbal P=photo R=record Z/X=zoom",
             font, (150, 150, 160))
        line(screen, 232, "Tab=console  Esc=exit", font, (150, 150, 160))
        if console:
            pygame.draw.rect(screen, (30, 30, 40), (0, 300, 560, 60))
            line(screen, 308, "> " + cbuf + "_", big, (255, 255, 180))
        elif cli.last_msg:
            line(screen, 308, cli.last_msg, font, (200, 200, 120))
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


def main() -> int:
    ap = argparse.ArgumentParser(description="DJI Mavic Mini 1 PC client")
    ap.add_argument("--pi", metavar="HOST[:PORT]", help="via Pi bridge (AOA)")
    ap.add_argument("--serial", metavar="PORT", help="via remote controller/drone serial")
    ap.add_argument("--sim", action="store_true", help="no hardware (loopback)")
    ap.add_argument("--live", action="store_true", help="allow flight commands")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="log every DUML packet and raw chunk")
    ap.add_argument("--no-video", action="store_true",
                    help="do not send start_liveview on connect")
    ap.add_argument("--windowed", action="store_true",
                    help="windowed UI instead of fullscreen (F11 toggles)")
    ap.add_argument("--raw-video", action="store_true",
                    help="pipe video payloads to ffplay unparsed (no liveview header)")
    ap.add_argument("--dump-video", metavar="FILE",
                    help="write raw video payloads to FILE for offline analysis")
    args = ap.parse_args()

    global VERBOSE
    VERBOSE = args.verbose

    if args.pi:
        from transport import NetTransport, CompositeTransport
        host, _, p = args.pi.partition(":")
        # Pi = dumb jump-host: wrap outgoing in composite, demux incoming ourselves
        t = CompositeTransport(NetTransport(host, int(p) if p else 9910)); mode = "pi"
    elif args.serial:
        from transport import SerialTransport
        t = SerialTransport(args.serial); mode = "serial"
    else:
        from transport import LogTransport
        t = LogTransport(verbose=True); mode = "sim"
        print("[sim] loopback — commands are printed, no hardware")

    cli = Client(t, mode, args.live)
    cli.fullscreen = not args.windowed
    cli.raw_video = True if not args.no_video else args.raw_video
    if args.dump_video:
        cli.dump_f = open(args.dump_video, "wb")
        log(f"[video] dumping raw payloads to {args.dump_video}")
    cli.start_rx(); cli.start_sender()
    if not args.no_video:
        cli.start_video()
    cli.start_stats()
    try:
        run_ui(cli)
    except RuntimeError as e:
        print(e); return 2
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
