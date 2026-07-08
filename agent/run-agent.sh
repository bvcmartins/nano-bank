#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Requires: nano-bank API on :8081 and its Kind Postgres port-forward on the host.
# Copy .env.example to .env and fill OLLAMA_API_KEY + BRANCH_SERVICE_TOKEN first.
podman compose -f compose.yaml up --build "$@"
