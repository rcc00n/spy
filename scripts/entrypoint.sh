#!/usr/bin/env sh
set -eu

if [ "${WAIT_FOR_POSTGRES:-0}" = "1" ]; then
  host="${POSTGRES_HOST:-postgres}"
  port="${POSTGRES_PORT:-5432}"
  echo "Waiting for PostgreSQL at ${host}:${port}..."
  until nc -z "$host" "$port"; do
    sleep 1
  done
fi

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  python manage.py migrate --noinput
fi

if [ "${COLLECT_STATIC:-0}" = "1" ]; then
  python manage.py collectstatic --noinput
fi

exec "$@"
