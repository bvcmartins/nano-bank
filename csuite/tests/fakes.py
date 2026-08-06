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
