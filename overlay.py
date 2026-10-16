"""
Remote Laser Pointer - Overlay
================================
Transparent, always-on-top, click-through overlay that renders
laser-pointer dots received from remote controllers.

It can also *send* your own pointer: hold  Ctrl + Shift  and move the
mouse -- other participants in the same room will see your dot.

Usage:
    python overlay.py                    # opens connection dialog
    python overlay.py --server localhost:8765 --room room1 --name Alice
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

import tkinter as tk
import threading
import asyncio
import json
import ctypes
import ctypes.wintypes
import math
import time
import sys
import uuid
import argparse

# ── Auto-install dependencies ────────────────────────────────────────
def _ensure(pkg, imp=None):
    imp = imp or pkg
    try:
        return __import__(imp)
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        return __import__(imp)

websockets = _ensure("websockets")
_pynput = _ensure("pynput")
from pynput import mouse as _mouse, keyboard as _keyboard

# ── Win32 constants ──────────────────────────────────────────────────
GWL_EXSTYLE      = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED     = 0x00080000
WS_EX_TOOLWINDOW  = 0x00000080
WS_EX_NOACTIVATE  = 0x08000000

TRANSPARENT_COLOR = "#010101"
OVERLAY_TITLE     = "LP_OVERLAY_9f3a"          # unique title for FindWindowW

POINTER_COLORS = [
    "#FF3333",   # red
    "#33CCFF",   # cyan
    "#33FF33",   # green
    "#FF33FF",   # magenta
    "#FFAA00",   # orange
    "#AA33FF",   # purple
]


# ═════════════════════════════════════════════════════════════════════
class LaserOverlay:
    """Full-screen transparent overlay with WebSocket connectivity."""

    # ── init ─────────────────────────────────────────────────────────
    def __init__(self, *, server=None, room=None, name=None):
        self.user_id  = uuid.uuid4().hex[:8]
        self.my_color = POINTER_COLORS[hash(self.user_id) % len(POINTER_COLORS)]
        self.pointers = {}          # user_id -> dict
        self.running  = True
        self.ws       = None
        self.ws_loop  = None
        self.pointing = False
        self._ctrl    = False
        self._shift   = False

        self.screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        self.screen_h = ctypes.windll.user32.GetSystemMetrics(1)

        # Skip dialog if all CLI args supplied
        if server and room and name:
            self.server = server
            self.room   = room
            self.name   = name
            self._create_overlay()
        else:
            self._show_dialog(server, room, name)

    # ── connection dialog ────────────────────────────────────────────
    def _show_dialog(self, server=None, room=None, name=None):
        dlg = tk.Tk()
        dlg.title("\U0001F534 Laser Pointer — Overlay")
        dlg.configure(bg="#0f0f23")
        dlg.resizable(False, False)

        w, h = 440, 320
        x = (self.screen_w - w) // 2
        y = (self.screen_h - h) // 2
        dlg.geometry(f"{w}x{h}+{x}+{y}")

        tk.Label(dlg, text="\U0001F534 Remote Laser Pointer",
                 font=("Segoe UI", 20, "bold"), fg="#e94560",
                 bg="#0f0f23").pack(pady=(25, 2))
        tk.Label(dlg, text="Overlay — shows pointers on your screen",
                 font=("Segoe UI", 9), fg="#666",
                 bg="#0f0f23").pack(pady=(0, 18))

        form = tk.Frame(dlg, bg="#0f0f23")
        form.pack()

        fields = [
            ("Server:",    server or "localhost:8765"),
            ("Room:",      room   or "room1"),
            ("Your Name:", name   or f"User-{self.user_id[:4]}"),
        ]
        entries = []
        for label_text, default in fields:
            tk.Label(form, text=label_text, font=("Segoe UI", 10),
                     fg="#ccc", bg="#0f0f23").grid(
                row=len(entries), column=0, padx=(10, 6), pady=5, sticky="e")
            e = tk.Entry(form, width=26, font=("Segoe UI", 10),
                         bg="#1a1a3e", fg="white", insertbackground="white",
                         relief="flat", bd=5)
            e.insert(0, default)
            e.grid(row=len(entries), column=1, padx=(0, 10), pady=5)
            entries.append(e)

        def _go(*_):
            self.server = entries[0].get().strip()
            self.room   = entries[1].get().strip()
            self.name   = entries[2].get().strip()
            dlg.destroy()
            self._create_overlay()

        btn = tk.Button(dlg, text="\u25B6  Start Overlay", command=_go,
                        font=("Segoe UI", 12, "bold"), bg="#e94560", fg="white",
                        activebackground="#c1374e", relief="flat",
                        padx=25, pady=6, cursor="hand2")
        btn.pack(pady=22)
        dlg.bind("<Return>", _go)
        dlg.mainloop()

    # ── overlay window ───────────────────────────────────────────────
    def _create_overlay(self):
        self.root = tk.Tk()
        self.root.title(OVERLAY_TITLE)
        self.root.overrideredirect(True)
        self.root.geometry(f"{self.screen_w}x{self.screen_h}+0+0")
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", TRANSPARENT_COLOR)
        self.root.attributes("-alpha", 0.99)
        self.root.config(bg=TRANSPARENT_COLOR)

        self.canvas = tk.Canvas(self.root,
                                width=self.screen_w, height=self.screen_h,
                                bg=TRANSPARENT_COLOR, highlightthickness=0)
        self.canvas.pack()

        # click-through after the WM has mapped the window
        self.root.after(150, self._make_click_through)

        # background WebSocket thread
        t = threading.Thread(target=self._ws_thread, daemon=True)
        t.start()

        # keyboard / mouse hooks
        self._start_listeners()

        # render loop
        self._render()

        print("  Overlay window created. Waiting for connection …")
        self.root.mainloop()

    def _make_click_through(self):
        hwnd = ctypes.windll.user32.FindWindowW(None, OVERLAY_TITLE)
        if not hwnd:
            self.root.after(300, self._make_click_through)
            return
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            style | WS_EX_TRANSPARENT | WS_EX_LAYERED
                  | WS_EX_TOOLWINDOW  | WS_EX_NOACTIVATE,
        )
        print("  \u2705 Overlay active — click-through enabled")
        print("  Hold  Ctrl+Shift + move mouse  to send YOUR pointer")
        print("  Press  Ctrl+Alt+Q  to quit")

    # ── input listeners (for sending) ────────────────────────────────
    def _start_listeners(self):

        def _kp(key):
            if key in (_keyboard.Key.ctrl_l, _keyboard.Key.ctrl_r):
                self._ctrl = True
            elif key in (_keyboard.Key.shift, _keyboard.Key.shift_l, _keyboard.Key.shift_r):
                self._shift = True

            if self._ctrl and self._shift and not self.pointing:
                self.pointing = True

            # Ctrl+Alt+Q → quit
            if self._ctrl and getattr(key, "char", None) == "\x11":
                self.root.after(0, self._quit)

        def _kr(key):
            if key in (_keyboard.Key.ctrl_l, _keyboard.Key.ctrl_r):
                self._ctrl = False
            elif key in (_keyboard.Key.shift, _keyboard.Key.shift_l, _keyboard.Key.shift_r):
                self._shift = False
            if not (self._ctrl and self._shift) and self.pointing:
                self.pointing = False
                self._send(0, 0, False)

        def _mm(x, y):
            if self.pointing:
                self._send(x / self.screen_w, y / self.screen_h, True)

        self._kb = _keyboard.Listener(on_press=_kp, on_release=_kr)
        self._kb.daemon = True
        self._kb.start()

        self._ms = _mouse.Listener(on_move=_mm)
        self._ms.daemon = True
        self._ms.start()

    # ── WebSocket ────────────────────────────────────────────────────
    def _send(self, x, y, visible):
        if self.ws and self.ws_loop:
            asyncio.run_coroutine_threadsafe(
                self.ws.send(json.dumps({
                    "type": "pointer",
                    "user_id": self.user_id,
                    "name": self.name,
                    "x": x, "y": y,
                    "visible": visible,
                    "color": self.my_color,
                })),
                self.ws_loop,
            )

    def _ws_thread(self):
        self.ws_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.ws_loop)
        self.ws_loop.run_until_complete(self._ws_connect())

    async def _ws_connect(self):
        uri = f"ws://{self.server}"
        while self.running:
            try:
                async with websockets.connect(uri) as ws:
                    self.ws = ws
                    await ws.send(json.dumps({
                        "type": "join",
                        "room": self.room,
                        "user_id": self.user_id,
                        "name": self.name,
                    }))
                    print(f"  \U0001F7E2 Connected to {self.server}  (room: {self.room})")

                    async for raw in ws:
                        if not self.running:
                            break
                        try:
                            d = json.loads(raw)
                        except Exception:
                            continue
                        if d.get("type") == "pointer":
                            self.pointers[d["user_id"]] = {
                                "x": d["x"], "y": d["y"],
                                "visible": d["visible"],
                                "color": d.get("color", "#FF3333"),
                                "name": d.get("name", d["user_id"][:6]),
                                "time": time.time(),
                            }
                        elif d.get("type") == "system":
                            print(f"  \u2139\uFE0F  {d.get('message', '')}")

            except Exception as exc:
                self.ws = None
                if self.running:
                    print(f"  \U0001F534 Connection lost ({exc}). Retrying in 3 s …")
                    await asyncio.sleep(3)

    # ── renderer ─────────────────────────────────────────────────────
    def _render(self):
        if not self.running:
            return

        self.canvas.delete("lp")
        t = time.time()

        for uid, p in list(self.pointers.items()):
            if t - p.get("time", 0) > 5:
                p["visible"] = False
            if not p["visible"]:
                continue

            cx = int(p["x"] * self.screen_w)
            cy = int(p["y"] * self.screen_h)
            color = p.get("color", "#FF3333")
            name  = p.get("name", uid[:6])
            pulse = 1 + 0.2 * math.sin(t * 8)

            # glow rings  (stipple = cheap alpha on Windows)
            for r, stp in [(22, "gray12"), (14, "gray25"), (9, "gray50")]:
                r = int(r * pulse)
                self.canvas.create_oval(cx-r, cy-r, cx+r, cy+r,
                                        fill=color, outline="", stipple=stp,
                                        tags="lp")

            # solid core
            R = 5
            self.canvas.create_oval(cx-R, cy-R, cx+R, cy+R,
                                    fill=color, outline="white", width=2,
                                    tags="lp")
            # bright centre
            R2 = 2
            self.canvas.create_oval(cx-R2, cy-R2, cx+R2, cy+R2,
                                    fill="white", outline="", tags="lp")

            # label
            self.canvas.create_text(cx + 16, cy - 14, anchor="sw",
                                    text=name, fill=color,
                                    font=("Segoe UI", 10, "bold"),
                                    tags="lp")

        self.root.after(16, self._render)       # ≈ 60 fps

    # ── shutdown ─────────────────────────────────────────────────────
    def _quit(self):
        print("  Shutting down …")
        self.running = False
        for listener in (getattr(self, "_kb", None), getattr(self, "_ms", None)):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        try:
            self.root.destroy()
        except Exception:
            pass
        sys.exit(0)


# ── CLI ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print()
    print("  \U0001F534 Remote Laser Pointer — Overlay")
    print("  " + "=" * 44)

    ap = argparse.ArgumentParser(description="Laser-pointer overlay")
    ap.add_argument("--server", default=None, help="host:port of the relay server")
    ap.add_argument("--room",   default=None)
    ap.add_argument("--name",   default=None)
    args = ap.parse_args()

    LaserOverlay(server=args.server, room=args.room, name=args.name)
