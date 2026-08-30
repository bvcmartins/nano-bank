from __future__ import annotations
import uuid
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import InMemorySaver

from .config import Settings
from . import model_factory as mf
from .crm import CRM_LLM_TOOL_NAMES
from .mcp_server import LLM_TOOL_NAMES

ALL_ALLOWED_TOOL_NAMES = LLM_TOOL_NAMES | CRM_LLM_TOOL_NAMES

MANAGER_PROMPT = (
    "You are a careful personal banking manager for ONE client. Answer only from the "
    "client's real data (use your tools to look it up); never fabricate balances or "
    "transactions, and say plainly when you do not know. You may move money only when the "
    "client explicitly instructs it, and only via the propose_* tools — proposing does NOT "
    "move money; the client must CONFIRM the exact proposed action before it executes. Never "
    "claim a transfer is done from a proposal alone. Do not act proactively."
)


def agent_tools(all_tools):
    return [t for t in all_tools if getattr(t, "name", None) in ALL_ALLOWED_TOOL_NAMES]


def _mcp_session(settings: Settings, customer_id: str, token: Optional[str],
                 crm_token: Optional[str] = None):
    """Per-request MCP client bound to a customer via trusted headers. The CRM
    server is included only when a CRM token is actually available — a
    customer with no CRM access yet (or CRM_BASE_URL left unconfigured, per
    Settings.from_env's fail-soft default there) simply gets no CRM tools this
    turn, same shape as nano's own optional X-Nano-Token."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    servers = {
        "nano": {
            "url": settings.mcp_url,
            "transport": "streamable_http",
            "headers": {"X-Nano-Customer": customer_id, **({"X-Nano-Token": token} if token else {})},
        }
    }
    if crm_token:
        servers["crm"] = {
            "url": settings.crm_base_url.rstrip("/") + "/api/agent/mcp",
            "transport": "streamable_http",
            "headers": {"authorization": f"Bearer {crm_token}"},
        }
    return MultiServerMCPClient(servers)


async def assist(settings: Settings, customer_id: str, token: Optional[str],
                 message: str, thread_id: Optional[str] = None) -> dict:
    thread_id = thread_id or f"{customer_id}-{uuid.uuid4().hex[:6]}"
    client = _mcp_session(settings, customer_id, token)
    all_tools = await client.get_tools()
    tools = agent_tools(all_tools)

    # server-side snapshot + recall (code, not the LLM) -> a context system message
    async def _call(name, **kw):
        for t in all_tools:
            if t.name == name:
                return await t.ainvoke(kw)
        return None
    snapshot = await _call("get_accounts")
    recalled = await _call("recall", query=message, k=4)
    context = SystemMessage(f"<client_snapshot>\n{snapshot}\n</client_snapshot>\n"
                            f"<durable_memory>\n{recalled}\n</durable_memory>")

    agent = create_react_agent(mf.llm("fast"), tools, prompt=MANAGER_PROMPT,
                               checkpointer=InMemorySaver())
    out = await agent.ainvoke(
        {"messages": [context, HumanMessage(message)]},
        config={"configurable": {"thread_id": thread_id}, "recursion_limit": 40})

    answer, pending = "(no answer)", None
    for m in reversed(out["messages"]):
        if isinstance(m, AIMessage) and (m.content or "").strip():
            answer = m.content
            break
    for m in out["messages"]:
        tc = getattr(m, "content", None)
        if isinstance(tc, str) and '"id"' in tc and "expires_at" in tc:
            import json
            try:
                obj = json.loads(tc)
                if isinstance(obj, dict) and obj.get("id") and not obj.get("denied"):
                    pending = obj
            except Exception:  # noqa: BLE001
                pass

    await _call("remember", fact=f"User asked: {message}", kind="user")
    await _call("remember", fact=f"Manager answered: {answer[:400]}", kind="assistant")
    res = {"answer": answer, "thread_id": thread_id}
    if pending:
        res["pending_action"] = pending
    return res
