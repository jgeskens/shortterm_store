#!/bin/sh
set -e

mkdir -p "$STORAGE_ROOT/.tmp"
python manage.py migrate --noinput
exec gunicorn shortterm_store.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 300 \
    --access-logfile -
