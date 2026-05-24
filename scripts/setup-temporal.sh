#!/usr/bin/env bash
# Register pravi's custom search attributes on the local Temporal namespace.
# Idempotent: already-existing attributes are skipped.
#
# Usage:  ./scripts/setup-temporal.sh
# Run once after `docker compose up -d`.
set -euo pipefail

CONTAINER="${CONTAINER:-pravi-temporal}"
NAMESPACE="${NAMESPACE:-default}"
# The Temporal server only binds to its container network IP, not 127.0.0.1.
# Use the docker-compose service DNS to reach it from inside the container.
ADDRESS="${ADDRESS:-temporal:7233}"

# These four attributes are referenced from src/pravi/temporal_utils.py.
ATTRS=(
  "RepoName:Keyword"
  "Domain:Keyword"
  "TicketId:Keyword"
  "PraviStatus:Keyword"
)

echo "registering search attributes on namespace=${NAMESPACE} (container=${CONTAINER})"

for spec in "${ATTRS[@]}"; do
  name="${spec%%:*}"
  type="${spec##*:}"

  output=$(docker exec "$CONTAINER" temporal --address "$ADDRESS" \
    operator search-attribute create \
    --namespace "$NAMESPACE" --name "$name" --type "$type" 2>&1 || true)

  if echo "$output" | grep -q -i -e "already exists" -e "AlreadyExists"; then
    echo "  • $name ($type) — already registered"
  elif echo "$output" | grep -q -i -e "have been added" -e "successfully"; then
    echo "  ✓ $name ($type) — registered"
  else
    echo "  ✗ $name ($type) — unexpected output:" >&2
    echo "$output" >&2
    exit 1
  fi
done

echo
echo "done. verify with:"
echo "  docker exec ${CONTAINER} temporal --address ${ADDRESS} operator search-attribute list --namespace ${NAMESPACE}"
