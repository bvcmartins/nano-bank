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

# LOCAL sandbox mode (default): the bare repo lives ON THE HOST at
# ~/dev/cto-sandbox.git, served to the pod by `git daemon` — no GitHub, no token.
# The pod reaches the host at the kind network gateway (e.g. 172.18.0.1).
HOST_GW="$(docker network inspect kind -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 172.18.0.1)"
echo "🌐 host git-daemon address for the pod: git://${HOST_GW}:9418/cto-sandbox.git"

echo "🚀 applying coder manifest ..."
kubectl --context "$CTX" apply -f coder/k8s/coder.yaml
kubectl --context "$CTX" -n nano-bank set env deploy/coder \
  "SANDBOX_CLONE_URL=git://${HOST_GW}:9418/cto-sandbox.git"
kubectl --context "$CTX" -n nano-bank rollout status deploy/coder --timeout=120s

echo
echo "➡  Make sure the host sandbox daemon is running (serves ~/dev/cto-sandbox.git):"
echo "     coder/start-sandbox-daemon.sh    # provisions the repo if missing, then runs git daemon"
