#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/spy"
cd "$APP_DIR"

if [ -d .git ]; then
  git pull --ff-only
fi

if [ ! -f .env ]; then
  echo "Missing $APP_DIR/.env" >&2
