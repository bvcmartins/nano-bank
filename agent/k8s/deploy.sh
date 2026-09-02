#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."          # -> agent/
CTX=kind-nano-bank

[ -f .env ] || { echo "❌ agent/.env missing (copy .env.example, fill OLLAMA_API_KEY + BRANCH_SERVICE_TOKEN)"; exit 1; }
OLLAMA_API_KEY=$(grep -E '^OLLAMA_API_KEY=' .env | cut -d= -f2-)
BRANCH_SERVICE_TOKEN=$(grep -E '^BRANCH_SERVICE_TOKEN=' .env | cut -d= -f2-)
[ -n "$OLLAMA_API_KEY" ] || { echo "❌ OLLAMA_API_KEY empty in .env"; exit 1; }
[ -n "$BRANCH_SERVICE_TOKEN" ] || { echo "❌ BRANCH_SERVICE_TOKEN empty in .env"; exit 1; }

echo "🐳 Building + loading agent images..."
docker build -f Dockerfile.mcp     -t nano-agent-mcp:dev     .
docker build -f Dockerfile.api     -t nano-agent-api:dev     .
docker build -f Dockerfile.console -t nano-agent-console:dev .
kind load docker-image nano-agent-mcp:dev nano-agent-api:dev nano-agent-console:dev --name nano-bank

echo "🔐 Minting nano-agent-secrets from .env (generate-on-apply; nothing committed)..."
kubectl --context "$CTX" create secret generic nano-agent-secrets -n nano-bank \
  --from-literal=OLLAMA_API_KEY="$OLLAMA_API_KEY" \
  --from-literal=BRANCH_SERVICE_TOKEN="$BRANCH_SERVICE_TOKEN" \
  --dry-run=client -o yaml | kubectl --context "$CTX" apply -f -

echo "📦 Applying agent manifests..."
kubectl --context "$CTX" apply -f k8s/qdrant.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/agent-qdrant --timeout=180s
kubectl --context "$CTX" apply -f k8s/mcp.yaml
kubectl --context "$CTX" apply -f k8s/api.yaml
kubectl --context "$CTX" apply -f k8s/console.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/agent-mcp     --timeout=180s
kubectl --context "$CTX" -n nano-bank rollout status deploy/agent-api     --timeout=240s
kubectl --context "$CTX" -n nano-bank rollout status deploy/agent-console --timeout=180s

# nano-bank-ui (deployed by the repo-root k8s/deploy.sh, which typically runs
# before this script) reads BRANCH_SERVICE_TOKEN from this same secret to call
# the branch API from the chat box. Its pod env only picks up secret values at
# creation, so restart it now that the secret exists/changed — a no-op if the
# UI isn't deployed yet.
kubectl --context "$CTX" -n nano-bank rollout restart deployment/nano-bank-ui 2>/dev/null || true

echo "✅ agent stack up"
