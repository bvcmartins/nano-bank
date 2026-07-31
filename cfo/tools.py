"""The CFO's only tools: the finance MCP server (bank-wide, read-only)."""
from __future__ import annotations
from .config import Settings

# State-changing finance tools the read-only CFO must never hold. The finance
# MCP server exposes close_period for operator/cron use, but an LLM that holds a
# tool can call it — an irreversible snapshot overwrite is not something a
# read-only analyst should be able to reach — so it is filtered out here, at the
# agent boundary, rather than mitigated by a line in the prompt. Any future
# mutating finance tool must be added to this set.
_MUTATING_TOOLS = frozenset({"close_period"})


def mcp_client(settings: Settings):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    return MultiServerMCPClient({
        "finance": {
            "url": settings.finance_mcp_url,
            "transport": "streamable_http",
        }
    })


async def get_tools(settings: Settings) -> list:
    tools = await mcp_client(settings).get_tools()
    return [t for t in tools if getattr(t, "name", None) not in _MUTATING_TOOLS]
