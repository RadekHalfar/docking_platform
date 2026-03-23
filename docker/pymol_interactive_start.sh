#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}

# Virtual X server for GUI apps
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/runtime/xvfb.log 2>&1 &

# Lightweight window manager
openbox >/tmp/runtime/openbox.log 2>&1 &

# noVNC stack
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 >/tmp/runtime/x11vnc.log 2>&1 &
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 >/tmp/runtime/novnc.log 2>&1 &

# GUI PyMOL with XML-RPC enabled for helper API integration
pymol -R >/tmp/runtime/pymol.log 2>&1 &

# Start helper API in foreground
exec python -m services.pymol_interactive.server
