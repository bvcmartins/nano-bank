#!/usr/bin/env bash
# One-time: create the cto-sandbox repo from this seed and tag its baseline.
# Needs: gh authenticated with `repo` scope. Run from the repo root, e.g.
#   SANDBOX_REPO=bvcmartins/cto-sandbox coder/sandbox-seed/provision-sandbox.sh
set -euo pipefail
REPO="${SANDBOX_REPO:-bvcmartins/cto-sandbox}"
SEED="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"
# copy everything except this provisioning script into the new repo
cp -r "$SEED"/. "$TMP"/
rm -f "$TMP/provision-sandbox.sh" "$TMP/provision-local-sandbox.sh"
find "$TMP" \( -name __pycache__ -o -name '*.pyc' -o -name .pytest_cache \) \
     -exec rm -rf {} + 2>/dev/null || true
cd "$TMP"
git init -q && git add -A
git -c user.email=coder@nano.bank -c user.name="nano-bank coder" \
    commit -qm "baseline: helper_service with two intentional gaps"
git branch -M main
git tag baseline
gh repo create "$REPO" --private --source=. --push
git push origin baseline
echo "provisioned $REPO (main + baseline tag pushed)"
