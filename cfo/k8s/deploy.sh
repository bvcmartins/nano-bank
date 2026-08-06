#!/usr/bin/env bash
# Deploy the CFO stack (finance MCP + CFO agent) into the kind nano-bank cluster.
# Mirrors coo/k8s/deploy.sh. Assumes the platform prereqs are already up:
#   - bank-api + postgres (k8s/deploy.sh)  — finance MCP reads the GL and stores
#                                            period snapshots in the same Postgres
#   - agent-qdrant (agent/k8s/qdrant.yaml) — CFO durable memory (best-effort)
#   - nano-agent-secrets                   — provides OLLAMA_API_KEY (minted here
#                                            if absent)
#
# Note on data: CFO reviews read period-end GL snapshots. Until at least one
# period is closed (the CFO's close_period tool, or demos/06-cfo/run-demo.sh's
# seed step) the reports are empty. Period-over-period metrics (RAROC) need two
# consecutive closed months.
set -euo pipefail
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank

echo "🐳 Building + loading images..."
docker build -t nano-finance-mcp:dev finance
# cfo image bundles the shared csuite package, so build from the repo root.
docker build -f cfo/Dockerfile -t nano-cfo:dev .
kind load docker-image nano-finance-mcp:dev nano-cfo:dev --name nano-bank

if ! kubectl --context "$CTX" -n nano-bank get secret nano-agent-secrets >/dev/null 2>&1; then
  echo "🔐 Minting nano-agent-secrets (OLLAMA_API_KEY from .env)..."
  [ -f .env ] || { echo "❌ .env missing (need OLLAMA_API_KEY=…)"; exit 1; }
  OLLAMA_API_KEY=$(grep -E '^OLLAMA_API_KEY=' .env | cut -d= -f2-)
  [ -n "$OLLAMA_API_KEY" ] || { echo "❌ OLLAMA_API_KEY empty in .env"; exit 1; }
  kubectl --context "$CTX" create secret generic nano-agent-secrets -n nano-bank \
    --from-literal=OLLAMA_API_KEY="$OLLAMA_API_KEY" \
    --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -
else
  echo "🔐 nano-agent-secrets already present — leaving it untouched."
fi

echo "📦 Applying manifests..."
kubectl --context "$CTX" apply -f finance/k8s/finance-mcp.yaml
kubectl --context "$CTX" apply -f cfo/k8s/cfo.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/finance-mcp --timeout=180s
kubectl --context "$CTX" -n nano-bank rollout status deploy/cfo         --timeout=240s

echo "✅ CFO stack up. Health:"
POD=$(kubectl --context "$CTX" get pod -n nano-bank -l app=cfo -o jsonpath='{.items[0].metadata.name}')
kubectl --context "$CTX" exec -n nano-bank "$POD" -- \
  python -c 'import urllib.request,json; print(json.dumps(json.load(urllib.request.urlopen("http://localhost:8089/health"))))'
