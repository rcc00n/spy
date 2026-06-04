#!/usr/bin/env sh
set -eu

export DISPLAY="${DISPLAY:-:99}"
resolution="${FACEBOOK_SESSION_SCREEN_RESOLUTION:-1366x900x24}"

Xvfb "$DISPLAY" -screen 0 "$resolution" >/tmp/xvfb.log 2>&1 &
fluxbox >/tmp/fluxbox.log 2>&1 &
x11vnc -display "$DISPLAY" -forever -shared -nopw -listen 0.0.0.0 -rfbport 5900 >/tmp/x11vnc.log 2>&1 &
websockify --web=/usr/share/novnc 0.0.0.0:7900 localhost:5900 >/tmp/novnc.log 2>&1 &

exec python manage.py run_facebook_session_manager "$@"
