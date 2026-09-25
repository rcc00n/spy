#!/usr/bin/env sh
set -eu
exec python /app/scripts/facebook_session_supervisor.py "$@"
