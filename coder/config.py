from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Mapping, Optional


@dataclass
class Settings:
    ollama_api_key: str
    ollama_base_url: str
    models: dict          # {"reasoning": <id>, "fast": <id>}
    model_fallback: str
    sandbox_repo: str      # "owner/name"
    sandbox_clone_url: str  # https clone URL (token injected at runtime)
    workspace_root: str
    api_port: int
    request_timeout: float
    test_timeout: int
    gh_token: str
    pr_base: str

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        default_model = g("CODER_MODEL", "kimi-k2.6")
        repo = g("SANDBOX_REPO", "bvcmartins/cto-sandbox")
        gh_token = g("GH_TOKEN") or _read_file(g("GH_TOKEN_PATH", ""))
        return cls(
            ollama_api_key=g("OLLAMA_API_KEY"),
            ollama_base_url=g("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            models={
                "reasoning": g("CODER_REASONING_MODEL", default_model),
                "fast": g("CODER_FAST_MODEL", default_model),
            },
            model_fallback=g("CODER_MODEL_FALLBACK", default_model),
            sandbox_repo=repo,
            sandbox_clone_url=g("SANDBOX_CLONE_URL", f"https://github.com/{repo}.git"),
            workspace_root=g("CODER_WORKSPACE_ROOT", "/tmp/coder-workspaces"),
            api_port=int(g("API_PORT", "8096")),
            request_timeout=float(g("REQUEST_TIMEOUT", "600")),
            test_timeout=int(g("TEST_TIMEOUT", "180")),
            gh_token=gh_token,
            pr_base=g("PR_BASE", "main"),
        )


def _read_file(path: str) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""
