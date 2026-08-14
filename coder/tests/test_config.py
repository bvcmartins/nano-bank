from coder.config import Settings


def test_defaults():
    s = Settings.from_env({})
    assert s.models == {"reasoning": "kimi-k2.6", "fast": "kimi-k2.6"}
    assert s.model_fallback == "kimi-k2.6"
    assert s.ollama_base_url == "https://ollama.com/v1"
    assert s.sandbox_mode == "local"                 # local, no GitHub, by default
    assert s.sandbox_clone_url == "file:///sandbox"
    assert s.api_port == 8096


def test_github_mode_restores_remote_defaults():
    s = Settings.from_env({"SANDBOX_MODE": "github"})
    assert s.sandbox_repo == "bvcmartins/cto-sandbox"
    assert s.sandbox_clone_url == "https://github.com/bvcmartins/cto-sandbox.git"


def test_env_overrides():
    s = Settings.from_env({
        "CODER_REASONING_MODEL": "kimi-k3", "CODER_FAST_MODEL": "kimi-k2.6",
        "CODER_MODEL_FALLBACK": "kimi-k2.6", "OLLAMA_API_KEY": "sk",
        "SANDBOX_REPO": "me/repo", "API_PORT": "9000"})
    assert s.models["reasoning"] == "kimi-k3"
    assert s.ollama_api_key == "sk"
    assert s.sandbox_repo == "me/repo"
    assert s.api_port == 9000
