from __future__ import annotations
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional


@dataclass
class Settings:
    ollama_api_key: str
    ollama_base_url: str
    manager_model: str
    qdrant_url: str
    qdrant_collection: str
    db: dict
    nano_bank_api: str
    branch_service_token: str
    act_max_per_tx: Decimal
    loan_max_principal: Decimal
    confirm_ttl_s: int
    mcp_url: str
    cxo_url: str
    branch_port: int
    console_port: int
    # External mandated-agent gateway (the branch holds the agent creds)
    nano_agent_id: str
    nano_agent_secret: str
    agent_gateway_token: str
    agent_mandate_id: str
    agent_customer_id: str
    agent_biller_account_id: str   # "Epcor Utilities" — the bill-payment destination

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Settings":
        e = os.environ if env is None else env

        def g(k, d=""):
            return e.get(k, d)

        return cls(
            ollama_api_key=g("OLLAMA_API_KEY"),
            ollama_base_url=g("OLLAMA_BASE_URL", "https://ollama.com/v1"),
            manager_model=g("MANAGER_MODEL", "glm-5.3"),
            qdrant_url=g("QDRANT_URL", "http://localhost:6335"),
            qdrant_collection=g("QDRANT_COLLECTION", "nano_manager_memory"),
            db=dict(
                host=g("DB_HOST", "::1"),
                port=int(g("DB_PORT", "5432")),
                dbname=g("DB_NAME", "nano_bank_db"),
                user=g("DB_USER", "nanobank_user"),
                password=g("DB_PASSWORD", "secure_nano_password_2024!"),
            ),
            nano_bank_api=g("NANO_BANK_API", "http://localhost:8081"),
            branch_service_token=g("BRANCH_SERVICE_TOKEN"),
            act_max_per_tx=Decimal(g("ACT_MAX_PER_TX", "1000")),
            loan_max_principal=Decimal(g("LOAN_MAX_PRINCIPAL", "100000")),
            confirm_ttl_s=int(g("CONFIRM_TTL_S", "300")),
            mcp_url=g("MCP_URL", "http://localhost:8087/mcp"),
            cxo_url=g("CXO_URL", "http://cxo:8098"),
            branch_port=int(g("BRANCH_PORT", "8086")),
            console_port=int(g("CONSOLE_PORT", "8505")),
            nano_agent_id=g("NANO_AGENT_ID"),
            nano_agent_secret=g("NANO_AGENT_SECRET"),
            agent_gateway_token=g("AGENT_GATEWAY_TOKEN"),
            agent_mandate_id=g("AGENT_MANDATE_ID"),
            agent_customer_id=g("AGENT_CUSTOMER_ID"),
            agent_biller_account_id=g("AGENT_BILLER_ACCOUNT_ID"),
        )
