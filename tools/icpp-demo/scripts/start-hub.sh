#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -e .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example; edit it to configure optional models."
fi
if [ "${1:-}" = "--init-env" ]; then exit 0; fi
docker compose -p sage-icpp-demo-hub -f compose.hub.yaml pull
docker compose -p sage-icpp-demo-hub -f compose.hub.yaml up -d --pull never --no-build --wait
address=$(docker compose -p sage-icpp-demo-hub -f compose.hub.yaml port demo 18400)
echo "Open http://$address/ui/"
