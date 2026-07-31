from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Settings:
    ollama_api_key: str
    ollama_base_url: str
    cfo_model: str
    finance_mcp_url: str
    api_port: int
    console_port: int

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            ollama_api_key=g("OLLAMA_API_KEY"),
            ollama_base_url=g("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            cfo_model=g("CFO_MODEL", "glm-5.2"),
            finance_mcp_url=g("FINANCE_MCP_URL", "http://localhost:8088/mcp"),
            api_port=int(g("API_PORT", "8089")),
            console_port=int(g("CONSOLE_PORT", "8506")),
        )
