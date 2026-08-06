#!/usr/bin/env bash
# Deploy the COO stack (operations MCP + COO agent) into the kind nano-bank
# cluster. Mirrors agent/k8s/deploy.sh. Assumes the platform prereqs are already
# up in the cluster:
#   - bank-api   (k8s/deploy.sh)      — the operations MCP reads it over HTTP
#   - agent-qdrant (agent/k8s/qdrant.yaml) — COO durable memory (best-effort)
#   - nano-agent-secrets              — provides OLLAMA_API_KEY + SERVICE_CLIENT_SECRET
#                                       (minted here if absent)
#
# Note on data: a COO review is grounded but reads ZERO until money has moved.
# Seeding non-zero activity needs a GL core for the Ledger port to post to (the
# separate modern-core cluster) — see scripts/deploy-all.sh. For a quick non-zero
# demo without k8s, use the host path: testing/seed-demo.sh + coo/verify-coo.sh.
set -euo pipefail
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank

echo "🐳 Building + loading images..."
docker build -t nano-operations-mcp:dev operations
# coo image bundles the shared csuite package, so build from the repo root.
docker build -f coo/Dockerfile -t nano-coo:dev .
kind load docker-image nano-operations-mcp:dev nano-coo:dev --name nano-bank

# The service secret is a shared credential with the bank's service plane; the
# operations MCP now fails loudly if it is unset (config.py), so it MUST live in
# the secret. Sourced from .env if present, else the repo's dev default (matches
# bank-api's built-in default — rotate both for a real deployment).
# `|| true`: grep exits 1 when .env has no SERVICE_CLIENT_SECRET line, and under
# `set -euo pipefail` that would abort the script — the default below is exactly
# the fallback we want in that case.
SERVICE_CLIENT_SECRET=$(grep -E '^SERVICE_CLIENT_SECRET=' .env 2>/dev/null | cut -d= -f2- || true)
: "${SERVICE_CLIENT_SECRET:=nano-bank-visa-network-secret-change-me}"

if ! kubectl --context "$CTX" -n nano-bank get secret nano-agent-secrets >/dev/null 2>&1; then
  echo "🔐 Minting nano-agent-secrets (OLLAMA_API_KEY from .env + SERVICE_CLIENT_SECRET)..."
  [ -f .env ] || { echo "❌ .env missing (need OLLAMA_API_KEY=…)"; exit 1; }
  OLLAMA_API_KEY=$(grep -E '^OLLAMA_API_KEY=' .env | cut -d= -f2-)
  [ -n "$OLLAMA_API_KEY" ] || { echo "❌ OLLAMA_API_KEY empty in .env"; exit 1; }
  kubectl --context "$CTX" create secret generic nano-agent-secrets -n nano-bank \
    --from-literal=OLLAMA_API_KEY="$OLLAMA_API_KEY" \
    --from-literal=SERVICE_CLIENT_SECRET="$SERVICE_CLIENT_SECRET" \
    --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -
else
  echo "🔐 nano-agent-secrets present — ensuring SERVICE_CLIENT_SECRET key is set..."
  # Idempotently add/refresh just the service-secret key without disturbing
  # OLLAMA_API_KEY (patch, not recreate).
  kubectl --context "$CTX" -n nano-bank patch secret nano-agent-secrets \
    --type merge -p "{\"stringData\":{\"SERVICE_CLIENT_SECRET\":\"$SERVICE_CLIENT_SECRET\"}}"
fi

echo "📦 Applying manifests..."
kubectl --context "$CTX" apply -f operations/k8s/operations-mcp.yaml
kubectl --context "$CTX" apply -f coo/k8s/coo.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/operations-mcp --timeout=180s
kubectl --context "$CTX" -n nano-bank rollout status deploy/coo            --timeout=240s

echo "✅ COO stack up. Health:"
POD=$(kubectl --context "$CTX" get pod -n nano-bank -l app=coo -o jsonpath='{.items[0].metadata.name}')
kubectl --context "$CTX" exec -n nano-bank "$POD" -- \
  python -c 'import urllib.request,json; print(json.dumps(json.load(urllib.request.urlopen("http://localhost:8093/health"))))'
