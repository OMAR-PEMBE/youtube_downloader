#!/usr/bin/env bash
set -euo pipefail

export DOWNLOAD_ROOT="${DOWNLOAD_ROOT:-/tmp/download_jobs}"
mkdir -p "$DOWNLOAD_ROOT"

python manage.py migrate --noinput

celery -A youtube_downloader worker \
    --loglevel=info \
    --concurrency="${CELERY_CONCURRENCY:-1}" &
worker_pid=$!

celery -A youtube_downloader beat \
    --loglevel=info \
    --schedule=/tmp/celerybeat-schedule &
beat_pid=$!

gunicorn youtube_downloader.wsgi:application \
    --bind "0.0.0.0:${PORT:-10000}" \
    --workers="${WEB_CONCURRENCY:-1}" \
    --worker-class=gthread \
    --threads="${WEB_THREADS:-4}" \
    --timeout=600 \
    --access-logfile=- \
    --error-logfile=- &
web_pid=$!

shutdown() {
    kill "$web_pid" "$worker_pid" "$beat_pid" 2>/dev/null || true
    wait "$web_pid" "$worker_pid" "$beat_pid" 2>/dev/null || true
}

trap shutdown EXIT INT TERM
set +e
wait -n "$web_pid" "$worker_pid" "$beat_pid"
exit_code=$?
set -e
exit "$exit_code"
