#!/usr/bin/env bash
set -euo pipefail

export DISPLAY=${DISPLAY:-:99}

display_num=${DISPLAY#:}
lock_file="/tmp/.X${display_num}-lock"

# Clean up a stale X lock from an unclean shutdown before starting a new Xvfb.
if [ -f "$lock_file" ] && ! pgrep -f "Xvfb ${DISPLAY}" >/dev/null 2>&1; then
	rm -f "$lock_file"
fi

# Virtual X server for GUI apps
Xvfb "$DISPLAY" -screen 0 1920x1080x24 -ac +extension GLX +render -noreset >/tmp/runtime/xvfb.log 2>&1 &

# Wait until the X server is actually accepting connections before starting GUI clients.
for _ in $(seq 1 30); do
	if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
		break
	fi
	sleep 1
done

if ! xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
	echo "Xvfb did not become ready on ${DISPLAY}" >&2
	tail -n 50 /tmp/runtime/xvfb.log >&2 || true
	exit 1
fi

# Lightweight window manager
openbox >/tmp/runtime/openbox.log 2>&1 &

# noVNC stack
x11vnc -display "$DISPLAY" -forever -shared -nopw -rfbport 5900 >/tmp/runtime/x11vnc.log 2>&1 &
/usr/share/novnc/utils/novnc_proxy --vnc localhost:5900 --listen 6080 >/tmp/runtime/novnc.log 2>&1 &

# GUI PyMOL with explicit XML-RPC server via startup script.
# pymol -R is unreliable in open-source 3.x (logs start but never binds socket).
pymol -r /usr/local/bin/pymol_rpc_server.py >/tmp/runtime/pymol.log 2>&1 &

# Start helper API in foreground
exec python -m services.pymol_interactive.server
