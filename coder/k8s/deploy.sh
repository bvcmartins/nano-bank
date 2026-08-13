#!/usr/bin/env bash
# Build the coder image, load it into the kind cluster, and apply the manifest.
# Prereqs: the coder-gh-token secret must already exist (see coder/README.md).
set -euo pipefail
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
cd "$(dirname "$0")/../.."          # repo root
CTX=kind-nano-bank

echo "🔨 building nano-coder:dev ..."
docker build -f coder/Dockerfile -t nano-coder:dev .

echo "📦 loading image into kind ..."
kind load docker-image nano-coder:dev --name nano-bank

echo "🔑 ensuring coder-gh-token secret exists ..."
kubectl --context "$CTX" -n nano-bank get secret coder-gh-token >/dev/null 2>&1 || {
  echo "   MISSING: create it first — see coder/README.md (provisioning)"; exit 1; }

echo "🚀 applying coder manifest ..."
kubectl --context "$CTX" apply -f coder/k8s/coder.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/coder --timeout=120s
