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

# LOCAL sandbox mode (default): the bare repo lives IN-CLUSTER on a PVC, seeded by
# the manifest's initContainer — no GitHub, no token, and the coder needs NO host
# access at all. (For github mode, create the coder-gh-token secret first; see
# coder/README.md.)
echo "🚀 applying coder manifest ..."
kubectl --context "$CTX" apply -f coder/k8s/coder.yaml
kubectl --context "$CTX" -n nano-bank rollout status deploy/coder --timeout=120s

echo
echo "🔒 CONTAINMENT: kindnet does not enforce NetworkPolicy. Deny the coder (and all"
echo "   kind pods) from reaching this host + the LAN with the host firewall:"
echo "     sudo coder/k8s/egress-firewall.sh"
echo "   Review/merge a delegated change (host-initiated):"
echo "     kubectl -n nano-bank exec deploy/coder -- git -C /sandbox diff main..<branch>"
echo "     kubectl -n nano-bank exec deploy/coder -- git -C /sandbox merge --ff-only <branch>"
