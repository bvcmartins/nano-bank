from decimal import Decimal
from agent.config import Settings


def test_defaults_when_env_empty():
    s = Settings.from_env({})
    assert s.ollama_base_url == "https://ollama.com/v1"
    assert s.manager_model == "glm-5.3"
    assert s.qdrant_collection == "nano_manager_memory"
    assert s.confirm_ttl_s == 300
    assert s.act_max_per_tx == Decimal("1000")
    assert s.db["dbname"] == "nano_bank_db"


def test_env_overrides():
    s = Settings.from_env({
        "MANAGER_MODEL": "kimi-k3",
        "ACT_MAX_PER_TX": "50.5",
        "CONFIRM_TTL_S": "90",
        "DB_HOST": "host.containers.internal",
    })
    assert s.manager_model == "kimi-k3"
    assert s.act_max_per_tx == Decimal("50.5")
    assert s.confirm_ttl_s == 90
    assert s.db["host"] == "host.containers.internal"


def test_loan_max_principal_default_and_override():
    s = Settings.from_env({})
    assert s.loan_max_principal == Decimal("100000")
    s2 = Settings.from_env({"LOAN_MAX_PRINCIPAL": "50000"})
    assert s2.loan_max_principal == Decimal("50000")
