#!/usr/bin/env bash
# Serve the LOCAL sandbox bare repo (~/dev/cto-sandbox.git) to the in-cluster
# coder over `git daemon`. Provisions the repo from the seed on first run.
#
#   coder/start-sandbox-daemon.sh            # foreground (Ctrl-C to stop)
#   coder/start-sandbox-daemon.sh &          # background
#
# The coder pod clones git://<kind-gateway>:9418/cto-sandbox.git and pushes review
# branches back (receive-pack). You review/merge with plain git in the repo dir.
#
# SECURITY: only repos under $BASE that carry a `git-daemon-export-ok` marker are
# served (NOT --export-all), so nothing else under your home is exposed. The
# daemon allows anonymous push to that one repo — it's a local dev sandbox.
set -euo pipefail
BASE="${SANDBOX_BASE:-$HOME/dev}"
REPO_DIR="$BASE/cto-sandbox.git"
SEED="$(cd "$(dirname "$0")/sandbox-seed" && pwd)"

if [ ! -e "$REPO_DIR/HEAD" ]; then
  echo "📦 provisioning $REPO_DIR from the seed ..."
  SANDBOX_PATH="$REPO_DIR" "$SEED/provision-local-sandbox.sh"
fi
# Mark it exportable + writable over the daemon (idempotent).
touch "$REPO_DIR/git-daemon-export-ok"
git -C "$REPO_DIR" config daemon.receivepack true

echo "🌐 git daemon serving $REPO_DIR on 0.0.0.0:9418 (base-path=$BASE)"
echo "   pod URL: git://<kind-gateway, e.g. 172.18.0.1>:9418/cto-sandbox.git"
exec git daemon --reuseaddr --listen=0.0.0.0 --port=9418 \
  --base-path="$BASE" --enable=receive-pack --verbose
