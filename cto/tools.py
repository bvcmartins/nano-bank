"""The CTO's domain tools: the platform MCP (read-only k8s + service /health)."""
from __future__ import annotations
from .config import Settings


def mcp_client(settings: Settings):
    from langchain_mcp_adapters.client import MultiServerMCPClient
    return MultiServerMCPClient({
        "platform": {"url": settings.platform_mcp_url,
                     "transport": "streamable_http"}})


async def get_tools(settings: Settings) -> list:
    return await mcp_client(settings).get_tools()
