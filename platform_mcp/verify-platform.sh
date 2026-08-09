#!/bin/bash
# Live smoke: with the platform MCP on :8094 (port-forwarded or in-cluster) and a
# valid cross-cluster kubeconfig, call platform_health over MCP and assert real
# JSON with both clusters' deployments comes back. Run with the venv active.
set -euo pipefail
BASE="${PLATFORM_MCP_URL:-http://localhost:8094/mcp}"
python - "$BASE" <<'PY'
import sys, anyio
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

async def main(url):
    async with streamablehttp_client(url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("platform_health", {})
            print(res.content[0].text[:600])

anyio.run(main, sys.argv[1])
PY
echo "OK"
