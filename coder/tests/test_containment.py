from coder import coding_agent as ca


def test_sandbox_env_drops_credentials(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-secret")
    monkeypatch.setenv("SERVICE_CLIENT_SECRET", "svc")
    monkeypatch.setenv("DB_PASSWORD", "pw")
    monkeypatch.setenv("SAFE_VAR", "ok")
    env = ca.sandbox_env()
    assert "OLLAMA_API_KEY" not in env
    assert "SERVICE_CLIENT_SECRET" not in env
    assert "DB_PASSWORD" not in env
    assert env.get("SAFE_VAR") == "ok"


def test_bash_tool_cannot_read_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-should-not-leak")
    ca.set_workspace(tmp_path)
    out = ca.bash.invoke({"command": "echo KEY=[$OLLAMA_API_KEY]"})
    assert "sk-should-not-leak" not in out
    assert "KEY=[]" in out


def test_run_python_tool_cannot_read_a_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "sk-should-not-leak")
    ca.set_workspace(tmp_path)
    out = ca.run_python.invoke(
        {"code": "import os; print('LEAK' if os.environ.get('OLLAMA_API_KEY') else 'clean')"})
    assert "sk-should-not-leak" not in out
    assert "clean" in out


def test_policy_path_stays_outside_the_checkout(tmp_path):
    # Finding 10: load_policy() reads this file straight into the system prompt, and the
    # model's write_file is confined to WORKSPACE — so set_workspace must NOT point the
    # policy file into the checkout, or the model could edit its own prompt.
    ca.set_workspace(tmp_path)
    assert ca.DEFAULT_POLICY_PATH != tmp_path / "learned_policy.json"
    assert tmp_path.resolve() not in ca.DEFAULT_POLICY_PATH.parents
