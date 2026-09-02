from decimal import Decimal
from agent.config import Settings


def test_defaults_when_env_empty():
    s = Settings.from_env({})
    assert s.ollama_base_url == "https://ollama.com/v1"
    assert s.manager_model == "glm-5.2"
    assert s.manager_fallback_model == "glm-4.7"
    assert s.qdrant_collection == "nano_manager_memory"
    assert s.confirm_ttl_s == 300
    assert s.act_max_per_tx == Decimal("1000")
    assert s.db["dbname"] == "nano_bank_db"


def test_env_overrides():
    s = Settings.from_env({
        "MANAGER_MODEL": "glm-9",
        "ACT_MAX_PER_TX": "50.5",
        "CONFIRM_TTL_S": "90",
        "DB_HOST": "host.containers.internal",
    })
    assert s.manager_model == "glm-9"
    assert s.act_max_per_tx == Decimal("50.5")
    assert s.confirm_ttl_s == 90
    assert s.db["host"] == "host.containers.internal"


def test_from_env_reads_crm_and_coo_settings():
    s = Settings.from_env(
        {
            "CRM_BASE_URL": "http://crm.test",
            "CRM_TENANT_SLUG": "acme",
            "CRM_AGENT_ID": "agent-1",
            "CRM_AGENT_SECRET": "agent-secret",
            "CRM_LOOKUP_TOKEN": "lookup-token",
            "COO_BASE_URL": "http://coo.test",
        }
    )
    assert s.crm_base_url == "http://crm.test"
    assert s.crm_tenant_slug == "acme"
    assert s.crm_agent_id == "agent-1"
    assert s.crm_agent_secret == "agent-secret"
    assert s.crm_lookup_token == "lookup-token"
    assert s.coo_base_url == "http://coo.test"
