#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-open-notebooklm-amd64-images.tar}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.target.yml}"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Archive not found: $ARCHIVE" >&2
  exit 1
fi

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "Compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi

docker load -i "$ARCHIVE"
docker compose -f "$COMPOSE_FILE" up -d
docker compose -f "$COMPOSE_FILE" ps
