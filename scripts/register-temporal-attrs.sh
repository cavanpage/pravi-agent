#!/usr/bin/env sh
# Register pravi's Temporal search attributes.
#
# Runs INSIDE a temporalio/admin-tools container as part of docker-compose
# init. Communicates with the temporal server via its service DNS.
#
# Also runnable standalone against any reachable Temporal address:
#   TEMPORAL_ADDRESS=localhost:7233 ./scripts/register-temporal-attrs.sh
#
# Idempotent: attributes that already exist are silently skipped.

set -eu

ADDRESS="${TEMPORAL_ADDRESS:-temporal:7233}"
NAMESPACE="${TEMPORAL_NAMESPACE:-default}"

# Keep in sync with src/pravi/temporal_utils.py.
ATTRS="RepoName:Keyword Domain:Keyword TicketId:Keyword PraviStatus:Keyword"

echo "registering search attributes on namespace=${NAMESPACE} address=${ADDRESS}"

for spec in $ATTRS; do
  name="${spec%%:*}"
  type="${spec##*:}"
  # `|| true` — repeat runs after a fresh volume are the common case; not an error.
  if temporal --address "$ADDRESS" operator search-attribute create \
       --namespace "$NAMESPACE" --name "$name" --type "$type" 2>&1 | grep -qE "(created|already exists)"; then
    echo "  ok: $name ($type)"
  else
    # Still print the error for visibility but don't fail the container —
    # a genuinely-broken temporal is caught by the workers failing to start.
    temporal --address "$ADDRESS" operator search-attribute create \
       --namespace "$NAMESPACE" --name "$name" --type "$type" || true
  fi
done

echo "done"
