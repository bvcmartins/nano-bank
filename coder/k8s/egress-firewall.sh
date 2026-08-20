#!/usr/bin/env bash
# Deny kind pods from reaching THIS host and the LAN, while keeping pod-to-pod and
# internet egress working. This is what actually contains the coder's network:
# kindnet does NOT enforce Kubernetes NetworkPolicy, so we enforce at the host with
# iptables in Docker's DOCKER-USER chain (the supported hook for bridge traffic).
#
# Effect for every kind pod (incl. the coder):
#   pod -> other kind pods (same subnet)  : ALLOWED  (cluster networking keeps working)
#   pod -> this host (the kind gateway)   : DROPPED  (no localhost services, no daemon)
#   pod -> LAN (10/8, 172.16/12, 192.168) : DROPPED  (no other machines)
#   pod -> internet (e.g. ollama.com)     : ALLOWED  (nothing sensitive to exfiltrate)
#
# Requires sudo (iptables). Idempotent. Reverse with:  egress-firewall.sh --remove
#
#   sudo coder/k8s/egress-firewall.sh            # install
#   sudo coder/k8s/egress-firewall.sh --remove   # uninstall
#   sudo coder/k8s/egress-firewall.sh --status   # show the rules
set -euo pipefail

CHAIN="DOCKER-USER"
# Discover the kind network's subnet + gateway (the host address pods would use).
SUBNET="$(docker network inspect kind -f '{{(index .IPAM.Config 0).Subnet}}' 2>/dev/null || echo 172.18.0.0/16)"
GW="$(docker network inspect kind -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || echo 172.18.0.1)"
LAN_RANGES=(10.0.0.0/8 172.16.0.0/12 192.168.0.0/16)
TAG="cto-coder-egress"

rules() {
  # Emitted in evaluation order (top-down). Deny host first, then allow pod->pod,
  # then deny the LAN; anything else (internet) falls through DOCKER-USER = allowed.
  echo "-s $SUBNET -d $GW -j DROP -m comment --comment $TAG"            # pod -> this host
  echo "-s $SUBNET -d $SUBNET -j RETURN -m comment --comment $TAG"      # pod -> pod (allow)
  for lan in "${LAN_RANGES[@]}"; do
    echo "-s $SUBNET -d $lan -j DROP -m comment --comment $TAG"         # pod -> LAN
  done
}

do_status() {
  iptables -S "$CHAIN" | grep "$TAG" || echo "(no $TAG rules installed)"
}

# Exit non-zero if the containment rules are absent — for a health check. DOCKER-USER
# rules do NOT survive a Docker restart or host reboot, so containment can lapse
# silently while the pod still looks deployed; a caller (deploy.sh, a systemd unit)
# can gate on this.
do_verify() {
  if iptables -S "$CHAIN" 2>/dev/null | grep -q "$TAG"; then
    echo "✓ $TAG egress rules present"
  else
    echo "✗ $TAG egress rules ABSENT — the coder is NOT contained (re-run without --verify)" >&2
    exit 1
  fi
}

do_remove() {
  # Delete any rule carrying our tag (loop until none remain).
  while iptables -S "$CHAIN" | grep -q "$TAG"; do
    line="$(iptables -S "$CHAIN" | grep "$TAG" | head -1)"
    # shellcheck disable=SC2086
    iptables -D "$CHAIN" ${line#-A $CHAIN }
  done
  echo "removed $TAG rules"
}

do_install() {
  do_remove >/dev/null 2>&1 || true       # clean slate so ordering is deterministic
  # Insert at the TOP of DOCKER-USER in reverse, so final order matches rules().
  mapfile -t R < <(rules)
  for ((i=${#R[@]}-1; i>=0; i--)); do
    # shellcheck disable=SC2086
    iptables -I "$CHAIN" 1 ${R[$i]}
  done
  echo "installed $TAG egress rules for kind subnet $SUBNET (host $GW + LAN denied):"
  do_status
}

case "${1:-}" in
  --remove) do_remove ;;
  --status) do_status ;;
  --verify) do_verify ;;
  *)        do_install ;;
esac
