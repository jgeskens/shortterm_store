#!/usr/bin/env bash
# Sync the code to the server and rebuild/restart the containers.
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-root@node3.geskens.be}"
DEPLOY_DIR="${DEPLOY_DIR:-/root/shortterm_store}"

cd "$(dirname "$0")"

rsync -az --delete \
    --exclude /.git/ \
    --exclude /.env \
    --exclude /zappa_settings.json \
    --exclude /venv/ \
    --exclude /.idea/ \
    --exclude /.ropeproject/ \
    --exclude /.python-version \
    --exclude /.superpowers/ \
    --exclude /.claude/ \
    --exclude /storage/ \
    --exclude /staticfiles/ \
    --exclude __pycache__/ \
    --exclude '*.pyc' \
    --exclude .DS_Store \
    ./ "$DEPLOY_HOST:$DEPLOY_DIR/"

ssh "$DEPLOY_HOST" "cd '$DEPLOY_DIR' && docker compose up -d --build --remove-orphans && docker image prune -f"
