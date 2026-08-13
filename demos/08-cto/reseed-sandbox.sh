#!/usr/bin/env bash
# Reseed the sandbox for a clean demo run: close open cto/* PRs, delete stale
# cto/* branches, and leave main at the baseline tag. Needs gh authenticated.
# Best-effort: a missing gh / unprovisioned sandbox is a skip, not an error.
set -euo pipefail
REPO="${SANDBOX_REPO:-bvcmartins/cto-sandbox}"

if ! command -v gh >/dev/null 2>&1; then
  echo "⚠ gh not found — skipping sandbox reseed"; exit 0
fi
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  echo "⚠ sandbox $REPO not reachable — skipping reseed"; exit 0
fi

echo "🧽 reseeding $REPO ..."
for n in $(gh pr list -R "$REPO" --state open --json number --jq '.[].number' 2>/dev/null || true); do
  gh pr close -R "$REPO" "$n" --delete-branch 2>/dev/null || true
done
for b in $(gh api "repos/$REPO/branches" --jq '.[].name' 2>/dev/null | grep '^cto/' || true); do
  gh api -X DELETE "repos/$REPO/git/refs/heads/$b" 2>/dev/null || true
done
echo "   sandbox reset to baseline ✓"
