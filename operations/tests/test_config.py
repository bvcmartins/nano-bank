import pytest
from operations.config import Settings


def test_from_env_reads_service_secret():
    s = Settings.from_env(
        {
            "SERVICE_CLIENT_SECRET": "shared-secret",
            "CRM_PROVISIONING_TOKEN": "co-token",
            "NANO_BANK_API": "http://bank.test",
            "MCP_PORT": "9000",
        }
    )
    assert s.service_client_secret == "shared-secret"
    assert s.nano_bank_api == "http://bank.test"
    assert s.mcp_port == 9000


def test_from_env_fails_loud_without_service_secret():
    # No safe default: the service secret is shared with the bank, so an unset
    # value must raise rather than silently mint tokens with a well-known secret.
    with pytest.raises(RuntimeError, match="SERVICE_CLIENT_SECRET"):
        Settings.from_env({"NANO_BANK_API": "http://bank.test"})


def test_from_env_treats_empty_secret_as_unset():
    with pytest.raises(RuntimeError, match="SERVICE_CLIENT_SECRET"):
        Settings.from_env({"SERVICE_CLIENT_SECRET": ""})


def test_from_env_reads_crm_settings():
    s = Settings.from_env(
        {
            "SERVICE_CLIENT_SECRET": "shared-secret",
            "CRM_PROVISIONING_TOKEN": "co-token",
            "CRM_BASE_URL": "http://crm.test",
            "CRM_TENANT_SLUG": "acme",
        }
    )
    assert s.crm_base_url == "http://crm.test"
    assert s.crm_tenant_slug == "acme"
    assert s.crm_provisioning_token == "co-token"


def test_from_env_fails_loud_without_crm_provisioning_token():
    with pytest.raises(RuntimeError, match="CRM_PROVISIONING_TOKEN"):
        Settings.from_env({"SERVICE_CLIENT_SECRET": "shared-secret"})
