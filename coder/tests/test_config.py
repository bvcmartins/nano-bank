import os

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


def test_ollama_key_read_from_a_mounted_file(tmp_path):
    # Finding 1: the deployed posture supplies the key as a FILE, not an env var, so it
    # never enters the process environment — closing the /proc/1/environ read route that
    # sandbox_env() (which only scrubs the *subprocess* env) cannot. The env var stays
    # supported for local dev, and takes precedence when both are set.
    keyfile = tmp_path / "api-key"
    keyfile.write_text("sk-mounted-secret\n")
    s = Settings.from_env({"OLLAMA_API_KEY_PATH": str(keyfile)})
    assert s.ollama_api_key == "sk-mounted-secret"       # trailing newline stripped
    # The key reached settings without ever being an environment variable of this
    # process — the property the /proc route depended on being violated.
    assert "OLLAMA_API_KEY" not in os.environ
    assert "sk-mounted-secret" not in os.environ.get("OLLAMA_API_KEY_PATH", "")


def test_ollama_env_key_wins_over_file(tmp_path):
    keyfile = tmp_path / "api-key"
    keyfile.write_text("from-file")
    s = Settings.from_env({"OLLAMA_API_KEY": "from-env", "OLLAMA_API_KEY_PATH": str(keyfile)})
    assert s.ollama_api_key == "from-env"
