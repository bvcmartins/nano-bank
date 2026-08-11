#!/usr/bin/env bash
# One-time: mint a read-only ServiceAccount in BOTH kind clusters, assemble one
# kubeconfig authenticating as those SAs, and store it as the
# nano-platform-kubeconfig Secret in the nano-bank cluster (where the platform
# MCP runs). The MCP mounts it read-only at /etc/platform/kubeconfig and reads
# both clusters through it. Re-runnable (applies are idempotent; the Secret is
# recreated). Requires: kubectl access to kind-nano-bank + kind-modern-core.
#
# Cross-cluster reachability: kind API servers listen on the shared host docker
# network. This script rewrites each context's server URL to the control-plane
# container's docker-network IP:6443 (reachable from a pod in the OTHER cluster),
# NOT 127.0.0.1 (which would point a pod at itself).
set -euo pipefail
cd "$(dirname "$0")"
NB_CTX=kind-nano-bank
MC_CTX=kind-modern-core
OUT=$(mktemp -d)/kubeconfig
: > "$OUT"

mint() {                        # $1=context  $2=kind-node-container  $3=cluster-label
  local ctx="$1" node="$2" cluster_label="$3"
  echo "🔐 minting platform-reader in $ctx (node $node)..."
  kubectl --context "$ctx" apply -f rbac.yaml >/dev/null
  # A long-lived token for the SA (k8s >=1.24 needs an explicit request).
  local token ca ip server
  token=$(kubectl --context "$ctx" -n kube-system create token platform-reader --duration=8760h)
  ca=$(kubectl --context "$ctx" config view --raw \
        -o jsonpath="{.clusters[?(@.name==\"$ctx\")].cluster.certificate-authority-data}")
  # Reachable-from-other-cluster endpoint: the kind node container's IP on the
  # kind docker network, port 6443.
  ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$node")
  server="https://${ip}:6443"
  KUBECONFIG="$OUT" kubectl config set-cluster "$ctx" --server="$server" >/dev/null
  # write CA data directly (set-cluster --certificate-authority wants a file)
  KUBECONFIG="$OUT" kubectl config set "clusters.$ctx.certificate-authority-data" "$ca" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-credentials "platform-reader@$ctx" --token="$token" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-context "$ctx" --cluster="$ctx" --user="platform-reader@$ctx" >/dev/null

  # --- write-scoped actor SA (Phase B) ---
  # Its patch on deployments is restricted by resourceNames (per-cluster
  # allow-list); the CTO's k8s_writer authenticates as this SA via the
  # "<ctx>-actor" context.
  local actor_rbac="rbac-actor-${cluster_label}.yaml"
  echo "🔐 minting platform-actor in $ctx ($actor_rbac)..."
  kubectl --context "$ctx" apply -f "$actor_rbac" >/dev/null
  local atoken
  atoken=$(kubectl --context "$ctx" -n kube-system create token platform-actor --duration=8760h)
  KUBECONFIG="$OUT" kubectl config set-cluster "${ctx}-actor" --server="$server" >/dev/null
  KUBECONFIG="$OUT" kubectl config set "clusters.${ctx}-actor.certificate-authority-data" "$ca" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-credentials "platform-actor@$ctx" --token="$atoken" >/dev/null
  KUBECONFIG="$OUT" kubectl config set-context "${ctx}-actor" --cluster="${ctx}-actor" --user="platform-actor@$ctx" >/dev/null
}

mint "$NB_CTX" nano-bank-control-plane nano-bank
mint "$MC_CTX" modern-core-control-plane modern-core
KUBECONFIG="$OUT" kubectl config use-context "$NB_CTX" >/dev/null

echo "📦 storing Secret nano-platform-kubeconfig in $NB_CTX/nano-bank..."
kubectl --context "$NB_CTX" -n nano-bank create secret generic nano-platform-kubeconfig \
  --from-file=kubeconfig="$OUT" \
  --dry-run=client -o yaml | kubectl --context "$NB_CTX" apply -f -

echo "✅ done. Verify a read as the SA (modern-core deployments):"
KUBECONFIG="$OUT" kubectl --context "$MC_CTX" get deploy -A --request-timeout=10s | head -n 5
rm -rf "$(dirname "$OUT")"
