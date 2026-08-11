"""Test doubles: a scriptable chat model and canned ops tools so harness + agent
tests run with no live LLM / MCP."""
from __future__ import annotations
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool


class FakeChatModel(BaseChatModel):
    """Plays a scripted list of turns. Each turn is either
    {"tool": name, "args": {...}} (emit a tool call) or {"text": "..."} (final)."""
    script: list
    i: int = 0

    def __init__(self, script, **kw):
        super().__init__(script=script, **kw)

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kw):
        from langchain_core.outputs import ChatGeneration, ChatResult
        turn = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        if "tool" in turn:
            msg = AIMessage(content="", tool_calls=[{
                "name": turn["tool"], "args": turn.get("args", {}),
                "id": f"call{self.i}"}])
        else:
            msg = AIMessage(content=turn["text"])
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kw):
        return self


def fake_ops_tools() -> list:
    @tool
    def float_position() -> dict:
        """Canned float."""
        return {"total_float": "700.00", "by_system": {"interac": "700.00"}}

    @tool
    def rails(window: str = "24h") -> dict:
        """Canned rails."""
        return {"window": window, "by_rail": {"interac": {"total_count": 7}}}

    return [float_position, rails]


def fake_platform_tools() -> list:
    @tool
    def estate_health() -> dict:
        """Canned estate health."""
        return {"deployments": [{"name": "coo", "desired": 1, "ready": 1,
                                 "healthy": True}],
                "rollup": {"total": 1, "healthy": 1, "degraded": 0}}

    @tool
    def service_health() -> dict:
        """Canned service health."""
        return {"healthy": ["bank-api"], "unhealthy": [], "failing_checks": []}

    @tool
    def execute_rollout_restart(cluster: str, deployment: str) -> dict:
        """Canned restart lever."""
        return {"outcome": "executed", "effect": {"restarted_at": "t"}}

    @tool
    def execute_rollback(cluster: str, deployment: str) -> dict:
        """Canned rollback lever."""
        return {"outcome": "executed",
                "effect": {"rolled_back_to": 5}}

    return [estate_health, service_health, execute_rollout_restart,
            execute_rollback]
