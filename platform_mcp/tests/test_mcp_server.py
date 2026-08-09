from platform_mcp.mcp_server import build_mcp, _stringify
from decimal import Decimal


class _FakeK8s:
    def deployments(self): return []
    def pods(self): return []
    def replicasets(self): return []
    def events(self): return []


class _FakeHealth:
    def probe(self): return []


def test_stringify_decimals_deep():
    out = _stringify({"a": Decimal("1.50"), "b": [Decimal("2")], "c": "x"})
    assert out == {"a": "1.50", "b": ["2"], "c": "x"}


def test_build_mcp_registers_expected_tools():
    import anyio
    mcp = build_mcp(_FakeK8s(), _FakeHealth())
    tools = anyio.run(mcp.list_tools)
    names = {t.name for t in tools}
    assert names == {"estate_health", "restarts", "rollouts", "versions",
                     "service_health", "platform_health", "compute"}
