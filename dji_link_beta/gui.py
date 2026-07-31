#!/usr/bin/env python3
"""
gui.py — a small pygame widget toolkit + the pre-flight screens (start menu and the
graphical Pi discovery). Everything the console used to prompt for (find the Pi, join
its access point, pick a Wi-Fi, type a password, "press Enter when ready") now happens
on screen. Console output is reserved for logs only.

The in-flight HUD/controls still live in pc_client.run_ui — this module is the shell
around it: menu -> connect -> (hand off to run_ui) -> back to menu on disconnect.

Toolkit is deliberately tiny: Button, TextInput, ListBox, Label, plus a Screen base
with an event/draw loop. Mouse-first, keyboard where it helps (Tab/Enter/Esc, typing).
"""

from __future__ import annotations
import pygame

# ---- palette (dark, calm; matches the HUD's readable-on-video look) ----
BG        = (18, 20, 26)
PANEL     = (28, 31, 40)
PANEL_HI  = (38, 42, 54)
ACCENT    = (90, 160, 255)
ACCENT_HI = (120, 190, 255)
TEXT      = (222, 228, 236)
MUTED     = (140, 150, 165)
GOOD      = (120, 220, 140)
WARN      = (255, 180, 110)
BAD       = (255, 120, 120)


def _font(size: int, bold: bool = False):
    return pygame.font.SysFont("segoeui,dejavusans,arial", size, bold=bold)


class Widget:
    def __init__(self, rect):
        self.rect = pygame.Rect(rect)
        self.enabled = True
        self.visible = True

    def handle(self, ev):     # returns True if the event was consumed
        return False

    def draw(self, surf):
        pass


class Label(Widget):
    def __init__(self, rect, text, size=20, col=TEXT, center=False, bold=False):
        super().__init__(rect)
        self.text = text
        self.col = col
        self.center = center
        self.font = _font(size, bold)

    def draw(self, surf):
        if not self.visible:
            return
        img = self.font.render(self.text, True, self.col)
        if self.center:
            surf.blit(img, img.get_rect(center=self.rect.center))
        else:
            surf.blit(img, (self.rect.x, self.rect.y + (self.rect.h - img.get_height()) // 2))


class Button(Widget):
    def __init__(self, rect, text, on_click, size=20, primary=False):
        super().__init__(rect)
        self.text = text
        self.on_click = on_click
        self.primary = primary
        self.font = _font(size, bold=primary)
        self.hover = False

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEMOTION:
            self.hover = self.rect.collidepoint(ev.pos)
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            self.on_click()
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        base = ACCENT if self.primary else PANEL_HI
        if not self.enabled:
            base = PANEL
        elif self.hover:
            base = ACCENT_HI if self.primary else (52, 57, 72)
        pygame.draw.rect(surf, base, self.rect, border_radius=8)
        col = (12, 16, 22) if self.primary and self.enabled else (TEXT if self.enabled else MUTED)
        img = self.font.render(self.text, True, col)
        surf.blit(img, img.get_rect(center=self.rect.center))


class TextInput(Widget):
    def __init__(self, rect, placeholder="", password=False, size=20):
        super().__init__(rect)
        self.text = ""
        self.placeholder = placeholder
        self.password = password
        self.font = _font(size)
        self.focused = False
        self._blink = 0

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self.focused = self.rect.collidepoint(ev.pos)
            return self.focused
        if not self.focused:
            return False
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            elif ev.key in (pygame.K_RETURN, pygame.K_TAB, pygame.K_ESCAPE):
                return False   # let the screen handle submit/navigation
            elif ev.unicode and ev.unicode.isprintable():
                self.text += ev.unicode
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, (14, 16, 21), self.rect, border_radius=6)
        pygame.draw.rect(surf, ACCENT if self.focused else PANEL_HI, self.rect, width=2, border_radius=6)
        shown = ("•" * len(self.text)) if self.password else self.text
        col = TEXT if self.text else MUTED
        s = shown if self.text else self.placeholder
        self._blink = (self._blink + 1) % 60
        if self.focused and self._blink < 30:
            s = shown + "|"
        img = self.font.render(s, True, col)
        surf.blit(img, (self.rect.x + 10, self.rect.y + (self.rect.h - img.get_height()) // 2))


class ListBox(Widget):
    """Scrollable single-select list. items = list of (label, value)."""
    def __init__(self, rect, items=None, size=18, row_h=30):
        super().__init__(rect)
        self.items = items or []
        self.font = _font(size)
        self.row_h = row_h
        self.sel = -1
        self.scroll = 0

    def set_items(self, items):
        self.items = items
        self.sel = -1
        self.scroll = 0

    @property
    def value(self):
        return self.items[self.sel][1] if 0 <= self.sel < len(self.items) else None

    def handle(self, ev):
        if not (self.visible and self.enabled):
            return False
        if ev.type == pygame.MOUSEWHEEL and self.rect.collidepoint(pygame.mouse.get_pos()):
            self.scroll = max(0, self.scroll - ev.y)
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 and self.rect.collidepoint(ev.pos):
            idx = self.scroll + (ev.pos[1] - self.rect.y) // self.row_h
            if 0 <= idx < len(self.items):
                self.sel = idx
            return True
        return False

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, (14, 16, 21), self.rect, border_radius=6)
        prev = surf.get_clip()
        surf.set_clip(self.rect)
        rows = self.rect.h // self.row_h
        for i in range(self.scroll, min(len(self.items), self.scroll + rows)):
            y = self.rect.y + (i - self.scroll) * self.row_h
            if i == self.sel:
                pygame.draw.rect(surf, (40, 60, 96),
                                 (self.rect.x, y, self.rect.w, self.row_h))
            img = self.font.render(self.items[i][0], True, TEXT if i == self.sel else MUTED)
            surf.blit(img, (self.rect.x + 10, y + (self.row_h - img.get_height()) // 2))
        surf.set_clip(prev)
        pygame.draw.rect(surf, PANEL_HI, self.rect, width=2, border_radius=6)


class LogPane(Widget):
    """A bottom pane that shows the last N log lines (tail of the shared log list)."""
    def __init__(self, rect, lines_ref, size=15):
        super().__init__(rect)
        self.lines_ref = lines_ref     # callable returning a list[str]
        self.font = _font(size)

    def draw(self, surf):
        if not self.visible:
            return
        pygame.draw.rect(surf, (12, 13, 17), self.rect, border_radius=6)
        lh = self.font.get_height() + 2
        rows = self.rect.h // lh
        lines = self.lines_ref()[-rows:]
        y = self.rect.y + 4
        for ln in lines:
            surf.blit(self.font.render(ln[:120], True, MUTED), (self.rect.x + 8, y))
            y += lh


class Screen:
    """Base screen: owns widgets, runs its own modal loop until done() is set.

    Subclasses set self.result and call self.finish(). run() returns self.result.
    """
    def __init__(self, surf, clock, title=""):
        self.surf = surf
        self.clock = clock
        self.title = title
        self.widgets = []
        self.result = None
        self._done = False
        self._title_font = _font(30, bold=True)

    def finish(self, result=None):
        self.result = result
        self._done = True

    def on_event(self, ev):
        pass

    def update(self):
        pass

    def draw_bg(self):
        self.surf.fill(BG)
        if self.title:
            img = self._title_font.render(self.title, True, TEXT)
            self.surf.blit(img, (40, 28))

    def run(self):
        while not self._done:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    self.result = "quit"
                    self._done = True
                    break
                consumed = False
                for w in self.widgets:
                    if w.handle(ev):
                        consumed = True
                        break
                if not consumed:
                    self.on_event(ev)
            self.update()
            self.draw_bg()
            for w in self.widgets:
                w.draw(self.surf)
            pygame.display.flip()
            self.clock.tick(60)
        return self.result


# ============================================================ screens

class MenuScreen(Screen):
    """Start menu. result is one of: 'connect', 'wifi', 'serial', 'sim', 'quit'."""
    def __init__(self, surf, clock):
        super().__init__(surf, clock, "DJI Mavic Mini 1 — PC control")
        w, h = surf.get_size()
        cx = w // 2
        bw, bh, gap = 340, 56, 16
        y = h // 2 - 124
        self.sub = Label((40, 78, w - 80, 26),
                         "Control the drone from your PC. Pick how to connect.",
                         size=18, col=MUTED)
        self.widgets = [
            self.sub,
            Button((cx - bw // 2, y,             bw, bh), "Connect via Raspberry Pi",
                   lambda: self.finish("connect"), primary=True),
            Button((cx - bw // 2, y + (bh+gap),  bw, bh), "Connect via serial (RC cable)",
                   lambda: self.finish("serial")),
            Button((cx - bw // 2, y + 2*(bh+gap),bw, bh), "Pi Wi-Fi setup",
                   lambda: self.finish("wifi")),
            Button((cx - bw // 2, y + 3*(bh+gap),bw, bh), "Simulator (no hardware)",
                   lambda: self.finish("sim")),
            Button((cx - bw // 2, y + 4*(bh+gap),bw, bh), "Quit",
                   lambda: self.finish("quit")),
        ]

    def on_event(self, ev):
        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            self.finish("quit")


class DiscoveryScreen(Screen):
    """Graphical replacement for the console discover_pi() flow.

    Runs netfind.discover() in a worker thread; shows progress; if the Pi has no
    internet, offers a Wi-Fi list + password to connect it; then a "ready" step.

    result: (host, port) on success, None on back, 'quit' on window close.
    """
    def __init__(self, surf, clock, netfind, log_lines, saved_host=None, force_wifi=False):
        super().__init__(surf, clock, "Pi Wi-Fi setup" if force_wifi else "Finding the Raspberry Pi")
        self.nf = netfind
        self.log_lines = log_lines          # callable -> list[str] (shared app log tail)
        self.saved_host = saved_host        # Pi address from earlier this session (fast re-connect)
        self.force_wifi = force_wifi
        self.state = "scanning"             # scanning | wifi | ready | done | failed
        self.host = None
        self.port = netfind.BRIDGE_PORT
        self.disc = None                    # discover() result dict
        self._worker = None
        self._err = None
        w, h = surf.get_size()
        self.status = Label((40, 82, w - 80, 28), "Looking for the Pi (LAN, then its access point)…",
                            size=19, col=ACCENT)
        self.wifi_list = ListBox((40, 130, w - 80, 220)); self.wifi_list.visible = False
        self.pw = TextInput((40, 360, 320, 40), "Wi-Fi password", password=True); self.pw.visible = False
        self.logpane = LogPane((40, h - 150, w - 80, 110), log_lines)
        self.btn_primary = Button((w - 260, h - 260, 220, 48), "", lambda: None, primary=True)
        self.btn_primary.visible = False
        self.btn_back = Button((40, h - 260, 160, 44), "Back", lambda: self.finish(None))
        self.widgets = [self.status, self.wifi_list, self.pw,
                        self.btn_primary, self.btn_back, self.logpane]
        self._start_scan()

    # ---- worker helpers (network calls must not block the 60fps loop) ----
    def _start_scan(self):
        import threading
        self.state = "scanning"
        def work():
            try:
                # saved_host makes find_on_lan() try the known Pi first (one quick port
                # check) before any LAN sweep / AP-join — instant on a re-connect.
                self.disc = self.nf.discover(saved_host=self.saved_host)
            except Exception as e:      # noqa: BLE001 — surface any netfind failure on screen
                self._err = str(e)
        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

    def _scan_wifi(self):
        import threading
        self.status.text = "Scanning Wi-Fi networks the Pi can see…"
        def work():
            try:
                nets = self.nf.pi_scan_wifi(self.host) or []
                self.wifi_list.set_items(
                    [(f"{n['signal']:3d}%  {n['security']:10s}  {n['ssid']}", n["ssid"])
                     for n in nets[:20]])
            except Exception as e:      # noqa: BLE001
                self._err = str(e)
        threading.Thread(target=work, daemon=True).start()

    def _connect_wifi(self):
        import threading
        ssid = self.wifi_list.value
        if not ssid:
            self.status.text = "Pick a network from the list first."
            return
        self.status.text = f"Connecting the Pi to {ssid}…"
        psk = self.pw.text
        def work():
            try:
                res = self.nf.pi_connect_wifi(self.host, ssid, psk)
                ok = res.get("ok")
                self.status.text = ("Connected. " if ok else "Failed. ") + res.get("output", "")[:80]
            except Exception as e:      # noqa: BLE001
                self.status.text = f"Wi-Fi connect error: {e}"
            self.state = "ready"
        threading.Thread(target=work, daemon=True).start()

    def update(self):
        if self._err:
            self.status.text = f"Error: {self._err}"
            self.status.col = BAD
            self.btn_primary.text = "Retry"
            self.btn_primary.on_click = self._retry
            self.btn_primary.visible = True
            self.btn_primary.primary = False
            return

        if self.state == "scanning":
            if self._worker and not self._worker.is_alive() and self.disc is not None:
                self.host = self.disc.get("host")
                if not self.host:
                    self.status.text = ("Couldn't reach the Pi. Power it on and make sure it is on "
                                        "your Wi-Fi or broadcasting 'PI_DJI_LINK-*'.")
                    self.status.col = WARN
                    self.btn_primary.text = "Retry"; self.btn_primary.on_click = self._retry
                    self.btn_primary.visible = True
                    return
                via = self.disc.get("via")
                if via == "ap":
                    self.status.text = f"Joined the Pi's network '{self.disc.get('joined_ap')}'."
                else:
                    self.status.text = f"Found the Pi at {self.host}."
                self.status.col = GOOD
                if self.force_wifi or self.disc.get("needs_internet_prompt"):
                    self.state = "wifi"
                    self.wifi_list.visible = True
                    self.pw.visible = True
                    if self.force_wifi:
                        self.status.text += " Pick an uplink Wi-Fi for the Pi."
                    else:
                        self.status.text += " It has no internet — pick a Wi-Fi for it (or skip)."
                    self.btn_primary.text = "Connect Wi-Fi"; self.btn_primary.on_click = self._connect_wifi
                    self.btn_primary.primary = False; self.btn_primary.visible = True
                    self.btn_back.text = "Skip"
                    self.btn_back.on_click = lambda: self._goto_ready()
                    self._scan_wifi()
                else:
                    self._goto_ready()

        elif self.state == "ready":
            self.wifi_list.visible = False
            self.pw.visible = False
            self.status.col = GOOD
            self.status.text = ("Pi ready. Now: 1) turn on the RC  2) plug the RC into the Pi  "
                                "3) turn on the drone and let it link. Then start flying.")
            self.btn_primary.text = "Done" if self.force_wifi else "Start flying"
            self.btn_primary.primary = True
            self.btn_primary.on_click = lambda: self.finish((self.host, self.port))
            self.btn_primary.visible = True
            self.btn_back.text = "Back"; self.btn_back.on_click = lambda: self.finish(None)

    def _goto_ready(self):
        self.state = "ready"

    def _retry(self):
        self._err = None
        self.status.col = ACCENT
        self.btn_primary.visible = False
        self._start_scan()


class SerialScreen(Screen):
    """Ask for a serial port. result: port string, or None on back."""
    def __init__(self, surf, clock, default=""):
        super().__init__(surf, clock, "Connect via serial")
        w, h = surf.get_size()
        Label  # noqa
        self.info = Label((40, 90, w - 80, 26),
                          "Enter the RC/drone serial port (e.g. COM3 on Windows, /dev/ttyACM0 on Linux).",
                          size=18, col=MUTED)
        self.inp = TextInput((40, 140, 360, 44), "serial port")
        self.inp.text = default
        self.inp.focused = True
        self.widgets = [
            self.info, self.inp,
            Button((40, 210, 200, 46), "Connect",
                   lambda: self.finish(self.inp.text.strip() or None), primary=True),
            Button((260, 210, 140, 46), "Back", lambda: self.finish(None)),
        ]

    def on_event(self, ev):
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_RETURN:
                self.finish(self.inp.text.strip() or None)
            elif ev.key == pygame.K_ESCAPE:
                self.finish(None)


# ============================================================ orchestrator

def preflight(surf, clock, netfind, log_tail, default_serial="", saved_host=None):
    """Run menu -> (discovery | serial | sim) and return a connection spec dict:
        {"mode": "pi",     "host": h, "port": p}
        {"mode": "serial", "port": "COM3"}
        {"mode": "sim"}
        {"mode": "quit"}
    Loops back to the menu on Back, so the user can change their mind.

    saved_host: the Pi address used earlier this session. Passed to discovery so a
    re-connect tries the known host first (one port-check) instead of a full LAN
    sweep + AP-join dance every time.
    """
    while True:
        choice = MenuScreen(surf, clock).run()
        if choice in ("quit", None):
            return {"mode": "quit"}
        if choice == "sim":
            return {"mode": "sim"}
        if choice == "serial":
            port = SerialScreen(surf, clock, default_serial).run()
            if port == "quit":
                return {"mode": "quit"}
            if port:
                return {"mode": "serial", "port": port}
            continue     # back to menu
        if choice == "wifi":
            res = DiscoveryScreen(surf, clock, netfind, log_tail,
                                  saved_host=saved_host, force_wifi=True).run()
            if res == "quit":
                return {"mode": "quit"}
            continue     # Wi-Fi setup is not a flight connection by itself.
        if choice == "connect":
            res = DiscoveryScreen(surf, clock, netfind, log_tail, saved_host=saved_host).run()
            if res == "quit":
                return {"mode": "quit"}
            if isinstance(res, tuple):
                return {"mode": "pi", "host": res[0], "port": res[1]}
            continue     # back to menu
