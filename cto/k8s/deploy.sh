#!/usr/bin/env bash
# Deploy the CTO stack (platform MCP + CTO agent) into the kind nano-bank
# cluster. Mirrors coo/k8s/deploy.sh. Prereqs already up in the cluster:
#   - nano-agent-secrets            — provides OLLAMA_API_KEY (minted by coo deploy)
#   - agent-qdrant                  — CTO durable memory (best-effort)
#   - nano-platform-kubeconfig      — cross-cluster read-only kubeconfig Secret,
#                                     minted by platform_mcp/k8s/make-kubeconfig.sh
set -euo pipefail
cd "$(dirname "$0")/../.."          # -> repo root
CTX=kind-nano-bank

echo "🐳 Building + loading images..."
docker build -t nano-platform-mcp:dev platform_mcp
docker build -f cto/Dockerfile -t nano-cto:dev .
kind load docker-image nano-platform-mcp:dev nano-cto:dev --name nano-bank

if ! kubectl --context "$CTX" -n nano-bank get secret nano-agent-secrets >/dev/null 2>&1; then
  echo "❌ nano-agent-secrets missing — run coo/k8s/deploy.sh first (mints OLLAMA_API_KEY)."
  exit 1
fi
if ! kubectl --context "$CTX" -n nano-bank get secret nano-platform-kubeconfig >/dev/null 2>&1; then
  echo "❌ nano-platform-kubeconfig missing — run platform_mcp/k8s/make-kubeconfig.sh first."
  exit 1
fi

echo "📦 Applying manifests..."
kubectl --context "$CTX" apply -f platform_mcp/k8s/platform-mcp.yaml
kubectl --context "$CTX" apply -f cto/k8s/cto.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/platform-mcp --timeout=180s
kubectl --context "$CTX" -n nano-bank rollout status deploy/cto          --timeout=240s

echo "✅ CTO stack up. Health:"
POD=$(kubectl --context "$CTX" get pod -n nano-bank -l app=cto -o jsonpath='{.items[0].metadata.name}')
kubectl --context "$CTX" exec -n nano-bank "$POD" -- \
  python -c 'import urllib.request,json; print(json.dumps(json.load(urllib.request.urlopen("http://localhost:8095/health"))))'
