#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/var/www/spy"
cd "$APP_DIR"

if [ -d .git ]; then
  git pull --ff-only
fi

if [ ! -f .env ]; then
  echo "Missing $APP_DIR/.env" >&2
  exit 1
fi

compose() {
  export COMPOSE_BAKE=false
  docker compose -f docker-compose.prod.yml "$@"
}

compose up -d postgres redis
compose build web celery-worker celery-beat telegram-bot facebook-session-manager
compose run --rm web python manage.py migrate --noinput
compose run --rm web python manage.py collectstatic --noinput
compose up -d web celery-worker celery-beat

telegram_token="$(grep -E '^TELEGRAM_BOT_TOKEN=.+' .env | cut -d= -f2- || true)"
if [ -n "$telegram_token" ]; then
  compose --profile bot up -d telegram-bot
else
  compose stop telegram-bot >/dev/null 2>&1 || true
fi

session_manager_url="$(grep -E '^FACEBOOK_SESSION_MANAGER_URL=.+' .env | cut -d= -f2- || true)"
if [ -n "$session_manager_url" ]; then
  compose --profile session up -d facebook-session-manager
