"""
Remote Laser Pointer - Server
==============================
Relays pointer coordinates between clients via WebSocket.
Also serves controller.html over HTTP so the helper can open it in a browser.

Usage:
    python server.py
    python server.py --ws-port 8765 --http-port 8080
"""
import sys

for _stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(_stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8", errors="replace")

import asyncio
import json
import os
import sys
import socket
import threading
import http.server
import argparse
from collections import defaultdict
from typing import Any

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
    import websockets

SCRIPT_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

# ── Room state ───────────────────────────────────────────────────────
rooms = defaultdict(dict)          # room_name -> {user_id: websocket}
user_names = {}                    # user_id -> display_name


# ── WebSocket handler ────────────────────────────────────────────────
async def handler(websocket):
    room = None
    user_id = None

    try:
        async for raw in websocket:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            # ── Join a room ──────────────────────────────────────────
            if msg_type == "join":
                room = data.get("room", "default")
                user_id = data.get("user_id", str(id(websocket)))
                name = data.get("name", user_id[:6])
                user_names[user_id] = name
                rooms[room][user_id] = websocket

                count = len(rooms[room])
                print(f"  + {name} joined room '{room}' ({count} online)")

                await _broadcast(room, json.dumps({
                    "type": "system",
                    "event": "join",
                    "user_id": user_id,
                    "name": name,
                    "count": count,
                    "message": f"{name} joined ({count} online)",
                }))

            # ── Pointer update ───────────────────────────────────────
            elif msg_type == "pointer" and room:
                await _broadcast(room, raw, exclude=user_id)

    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        if room and user_id:
            rooms[room].pop(user_id, None)
            user_names.pop(user_id, None)
            count = len(rooms[room])
            if count == 0:
                del rooms[room]
            else:
                await _broadcast(room, json.dumps({
                    "type": "system",
                    "event": "leave",
                    "user_id": user_id,
                    "count": count,
                    "message": f"User left ({count} online)",
                }))
            print(f"  - user left room '{room}' ({count} remaining)")


async def _broadcast(room, message, *, exclude=None):
    """Send *message* to every client in *room* except *exclude*."""
    dead = []
    for uid, ws in rooms.get(room, {}).items():
        if uid == exclude:
            continue
        try:
            await ws.send(message)
        except websockets.exceptions.ConnectionClosed:
            dead.append(uid)
    for uid in dead:
        rooms.get(room, {}).pop(uid, None)


# ── HTTP server (serves controller.html) ─────────────────────────────
def _start_http(port):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=SCRIPT_DIR, **kw)

        def do_GET(self):
            if self.path in ("/", "/controller"):
                self.path = "/controller.html"
            return super().do_GET()

        def log_message(self,format: str, *args: Any) -> None:
            pass  # suppress access logs

    srv = http.server.HTTPServer(("0.0.0.0", port), Handler)
    srv.serve_forever()


# ── Clipboard helper ─────────────────────────────────────────────────
def _copy_to_clipboard(text):
    """Copy text to the Windows clipboard silently."""
    try:
        import subprocess as sp
        p = sp.Popen(["clip"], stdin=sp.PIPE, shell=True)
        p.communicate(text.encode("utf-8"))
    except Exception:
        pass  # best-effort


# ── Launch overlay ───────────────────────────────────────────────────
def _launch_overlay(server_addr, room, name):
    """Spawn the overlay, using this executable when packaged."""
    import subprocess as sp
    overlay_script = os.path.join(SCRIPT_DIR, "overlay.py")
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--overlay"]
    elif os.path.isfile(overlay_script):
        command = [sys.executable, overlay_script]
    else:
        print("  [!] overlay.py not found — skipping overlay launch")
        return None
    return sp.Popen(
        command + ["--server", server_addr, "--room", room, "--name", name],
        creationflags=sp.CREATE_NEW_PROCESS_GROUP,
    )


# ── Main ─────────────────────────────────────────────────────────────
async def main(ws_port, http_port, room, name, no_overlay):
    # Resolve a usable LAN IP (prefer non-loopback)
    local_ip = "localhost"
    try:
        # Connect to a public DNS to discover the default outbound IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass

    share_url = f"http://{local_ip}:{http_port}"

    # ── Start HTTP + WebSocket servers ────────────────────────────
    threading.Thread(target=_start_http, args=(http_port,), daemon=True).start()

    # Copy the shareable link to clipboard
    _copy_to_clipboard(share_url)

    # Banner
    print()
    print("  ============================================")
    print("  Remote Laser Pointer")
    print("  ============================================")
    print()
    print(f"  >>> Share this link with your helper <<<")
    print()
    print(f"      {share_url}")
    print()
    print(f"  (Copied to clipboard!)")
    print()
    print(f"  Room : {room}")
    print(f"  WS   : ws://{local_ip}:{ws_port}")
    print()
    print("  ============================================")

    # ── Auto-launch overlay ───────────────────────────────────────
    overlay_proc = None
    if not no_overlay:
        overlay_proc = _launch_overlay(f"{local_ip}:{ws_port}", room, name)
        if overlay_proc:
            print("  Overlay started — laser dots will appear on your screen")
    else:
        print("  Overlay skipped (--no-overlay)")

    print("  Ctrl+C to stop")
    print()

    try:
        async with websockets.serve(handler, "0.0.0.0", ws_port):
            await asyncio.Future()
    finally:
        if overlay_proc:
            overlay_proc.terminate()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Remote Laser Pointer server")
    ap.add_argument("--server",     type=str, default=None,       help=argparse.SUPPRESS)
    ap.add_argument("--ws-port",    type=int, default=8765,       help="WebSocket port")
    ap.add_argument("--http-port",  type=int, default=8080,       help="HTTP port for controller page")
    ap.add_argument("--room",       type=str, default="room1",    help="Room name")
    ap.add_argument("--name",       type=str, default="Host",     help="Your display name")
    ap.add_argument("--no-overlay", action="store_true",          help="Don't auto-launch the overlay")
    ap.add_argument("--overlay",    action="store_true",          help=argparse.SUPPRESS)
    args = ap.parse_args()

    try:
        if args.overlay:
            from overlay import LaserOverlay
            LaserOverlay(server=args.server, room=args.room, name=args.name)
            raise SystemExit
        asyncio.run(main(args.ws_port, args.http_port, args.room, args.name, args.no_overlay))
    except KeyboardInterrupt:
        print("\n  Server stopped.")
