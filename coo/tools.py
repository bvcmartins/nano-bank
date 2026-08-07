"""The COO's domain tools: the operations MCP (bank-wide reads + autonomous,
self-verifying, audited operational levers)."""
from __future__ import annotations
from .config import Settings


def mcp_client(settings: Settings):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    return MultiServerMCPClient({
        "operations": {"url": settings.operations_mcp_url,
                       "transport": "streamable_http"}})


async def get_tools(settings: Settings) -> list:
    return await mcp_client(settings).get_tools()
