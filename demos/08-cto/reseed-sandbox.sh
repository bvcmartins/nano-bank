#!/usr/bin/env bash
# Reseed the sandbox for a clean demo run: drop stale cto/* review branches so
# beats 8-9 start from baseline. Mode-aware; best-effort (a skip, not an error).
#
#   SANDBOX_MODE=local (default) — reset the in-cluster PVC bare repo via kubectl.
#   SANDBOX_MODE=github          — close open cto/* PRs + delete branches via gh.
set -euo pipefail
MODE="${SANDBOX_MODE:-local}"

if [ "$MODE" = "github" ]; then
  REPO="${SANDBOX_REPO:-bvcmartins/cto-sandbox}"
  if ! command -v gh >/dev/null 2>&1 || ! gh repo view "$REPO" >/dev/null 2>&1; then
    echo "⚠ gh/sandbox $REPO not reachable — skipping reseed"; exit 0
  fi
  echo "🧽 reseeding $REPO (github) ..."
  for n in $(gh pr list -R "$REPO" --state open --json number --jq '.[].number' 2>/dev/null || true); do
    gh pr close -R "$REPO" "$n" --delete-branch 2>/dev/null || true
  done
  for b in $(gh api "repos/$REPO/branches" --jq '.[].name' 2>/dev/null | grep '^cto/' || true); do
    gh api -X DELETE "repos/$REPO/git/refs/heads/$b" 2>/dev/null || true
  done
  echo "   sandbox reset to baseline ✓"
  exit 0
fi

# local (default): delete cto/* branches in the in-cluster bare repo at /sandbox.
CTX="${CTX:-kind-nano-bank}"
NS="${NS:-nano-bank}"
if ! kubectl --context "$CTX" -n "$NS" get deploy/coder >/dev/null 2>&1; then
  echo "⚠ coder not deployed — skipping local reseed"; exit 0
fi
echo "🧽 reseeding the in-cluster local sandbox (/sandbox) ..."
kubectl --context "$CTX" -n "$NS" exec deploy/coder -- sh -c '
  for b in $(git -C /sandbox for-each-ref --format="%(refname:short)" refs/heads/ | grep "^cto/" || true); do
    git -C /sandbox branch -D "$b" >/dev/null 2>&1 || true
  done' 2>/dev/null || echo "⚠ local reseed skipped (coder not ready)"
echo "   sandbox reset to baseline ✓"
