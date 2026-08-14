#!/usr/bin/env bash
# LOCAL sandbox provisioning (no GitHub): create a bare git repo on disk from this
# seed and tag its baseline. Use this for the host-run coder or a hostPath mount.
# (In the default in-cluster PVC setup you do NOT need this — the manifest's
# initContainer seeds the PVC automatically.)
#
#   SANDBOX_PATH=~/dev/cto-sandbox.git coder/sandbox-seed/provision-local-sandbox.sh
set -euo pipefail
DEST="${SANDBOX_PATH:-$HOME/dev/cto-sandbox.git}"
SEED="$(cd "$(dirname "$0")" && pwd)"

if [ -e "$DEST/HEAD" ]; then
  echo "sandbox already exists at $DEST — leaving as-is"; exit 0
fi

git init --bare -b main -q "$DEST"
TMP="$(mktemp -d)"
cp -r "$SEED"/. "$TMP"/
rm -f "$TMP"/provision-sandbox.sh "$TMP"/provision-local-sandbox.sh
cd "$TMP"
git init -q -b main && git add -A
git -c user.email=coder@nano.bank -c user.name="nano-bank coder" \
    commit -qm "baseline: helper_service with two intentional gaps"
git tag baseline
git remote add origin "$DEST"
git push -q -u origin main
git push -q origin baseline
echo "provisioned local sandbox at $DEST (main + baseline tag)"
