#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -e .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example; model endpoints can be edited before startup."
fi
if [ "${1:-}" = "--init-env" ]; then exit 0; fi
project="${SAGE_COMPOSE_PROJECT:-sage-icpp-demo-local}"
docker load -i artifacts/sage-icpp-demo-20260926-cpu-full.tar
docker compose -p "$project" -f compose.local.yaml up -d --pull never --no-build --wait
address=$(docker compose -p "$project" -f compose.local.yaml port demo 18400)
echo "Open http://$address/ui/"
