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
        V ground-station · [ ] gimbal down/up · N recenter · P photo · R record
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
class VideoSink:
    """H.264 Annex-B frames -> ffplay window (robust, no heavy dependencies)."""
    def __init__(self):
        self.proc = None
        self.ok = False
        try:
            self.proc = subprocess.Popen(
                ["ffplay", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer",
                 "-flags", "low_delay", "-framedrop", "-f", "h264", "-i", "-"],
                stdin=subprocess.PIPE)
            self.ok = True
        except FileNotFoundError:
            print("[video] ffplay not found — won't show video (install ffmpeg). Data still flows anyway.")

    def on_frame(self, frame: bytes, is_key: bool):
        if self.ok and self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(frame)
            except Exception:
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
            on_video=lambda pl: liveview.feed_video_payload(self.reasm, pl) if self.reasm else None)
        self.responders = {}
        self.control = False        # whether control has been taken
        self.gs = False             # ground-station mode
        self.armed = False          # whether stick stream/takeoff is allowed
        self.axes = {"throttle": 0.0, "yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        self.lock = threading.Lock()
        self.running = True
        self.last_msg = ""

    # receive
    def _on_duml_payload(self, payload: bytes):
        for p in self.duml.feed(payload):
            if p.sender != 0x0A:
                self.responders[p.sender] = time.time()
            self.tele.feed_packet(p)

    def start_rx(self):
        threading.Thread(target=self._rx_loop, daemon=True).start()

    def _rx_loop(self):
        while self.running:
            try:
                data = self.t.recv(timeout_ms=300)
            except Exception:
                break
            if not data:
                continue
            if self.mode == "pi":
                self.demux.feed(data)           # AOA: composite -> DUML/video
            else:
                self._on_duml_payload(data)      # serial: DUML directly

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
    screen = pygame.display.set_mode((560, 360))
    pygame.display.set_caption("DJI Mavic Mini 1 — PC control")
    font = pygame.font.SysFont("consolas", 15)
    big = pygame.font.SysFont("consolas", 17, bold=True)
    clock = pygame.time.Clock()
    console = False
    cbuf = ""

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
                elif ev.key == pygame.K_LEFTBRACKET: cli.d.gimbal_speed(-30); cli.msg("gimbal down")
                elif ev.key == pygame.K_RIGHTBRACKET: cli.d.gimbal_speed(30); cli.msg("gimbal up")
                elif ev.key == pygame.K_n: cli.d.gimbal_recenter(); cli.msg("recenter")
                elif ev.key == pygame.K_p: cli.d.take_photo(); cli.msg("photo")
                elif ev.key == pygame.K_r: cli.d.start_record(); cli.msg("record start (Shift+R stop)")
                elif ev.key == pygame.K_z: cli.d.set_zoom(2.0); cli.msg("zoom 2x")
                elif ev.key == pygame.K_x: cli.d.set_zoom(1.0); cli.msg("zoom 1x")

        # held keys -> sticks
        if not console:
            held = pygame.key.get_pressed()
            pressed = {name for k, name in KEYMAP.items() if held[k]}
            s = keys_to_sticks(pressed)
            with cli.lock:
                cli.axes = {"throttle": s.throttle, "yaw": s.yaw, "pitch": s.pitch, "roll": s.roll}

        # render
        screen.fill((16, 18, 22))
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
    args = ap.parse_args()

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
    cli.start_rx(); cli.start_sender()
    try:
        run_ui(cli)
    except RuntimeError as e:
        print(e); return 2
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
