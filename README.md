# 🔴 Remote Laser Pointer

Point at things on someone else's screen — remotely. Perfect for helping
people during Discord / Zoom screen-shares, or for collaborative
debugging.

## How it works

```
┌─────────────┐     WebSocket      ┌────────────┐     WebSocket      ┌────────────────┐
│  Controller │ ──────────────────▶│   Server   │◀──────────────────  │    Overlay      │
│  (browser)  │   sends x,y       │  (relay)   │   receives x,y     │  (target PC)    │
│  helper     │                    │            │                    │  shows laser dot │
└─────────────┘                    └────────────┘                    └────────────────┘
```

## Quick Start

### 1. Install dependencies (one time)

```bash
pip install -r requirements.txt
```

### Standalone Windows executable

To create a single executable that does not require Python or a separate
`overlay.py` file on the recipient's computer:

```powershell
python -m pip install pyinstaller
pyinstaller --clean --noconfirm --onefile --name RemoteLaserPointer `
  --add-data "controller.html;." `
  --hidden-import overlay `
  --collect-submodules pynput `
  --collect-submodules websockets `
  server.py
```

Give the resulting `dist\RemoteLaserPointer.exe` to the host. The host runs
that file; the helper only opens the displayed browser link. Windows may ask
the host to allow the application through the firewall for ports `8080` and
`8765`.

### 2. Person A (sharing screen) — single command

```bash
python server.py
```

This does **everything**:
- Starts the relay server
- Starts the transparent overlay on your screen
- **Copies a shareable link to your clipboard**

You'll see:

```
  ============================================
  Remote Laser Pointer
  ============================================

  >>> Share this link with your helper <<<

      http://192.168.1.42:8080

  (Copied to clipboard!)
  ============================================
```

**Paste that link to your helper** (via Discord chat, etc.)

### 3. Person B (helper) — just open the link

Open the link in any browser → click & drag on the canvas → a laser dot
appears on Person A's screen!

Since Person A is screen-sharing, everyone in the call sees the pointer.

## Controls

### Controller (browser — Person B)

| Action | Effect |
|--------|--------|
| **Click & drag** | Pointer visible while held |
| **Space** | Toggle pointer lock (stays on without holding click) |
| **Esc** | Disconnect |

### Overlay (Person A's screen)

| Action | Effect |
|--------|--------|
| **Ctrl + Shift + mouse move** | Send YOUR pointer to others |
| **Ctrl + Alt + Q** | Quit the overlay |

## CLI Options

```bash
# All options (defaults shown)
python server.py --ws-port 8765 --http-port 8080 --room room1 --name Host

# Run server only, without the overlay
python server.py --no-overlay

# Run overlay separately (if needed)
python overlay.py --server localhost:8765 --room room1 --name Alice
```

## For Remote Use (not same LAN)

If Person A and B aren't on the same network, expose the server with:

- [ngrok](https://ngrok.com/): `ngrok http 8080` + `ngrok tcp 8765`
- [Tailscale](https://tailscale.com/): just use the Tailscale IP

## Requirements

- **Python 3.8+**
- **Windows** (overlay uses Win32 transparent-window APIs)
- A modern browser (controller — works on any OS)
