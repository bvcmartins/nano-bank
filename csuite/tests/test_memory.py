from csuite.harness.memory import HarnessMemory, SafeMemory


def test_record_then_recall_by_semantic_query():
    m = HarnessMemory.in_memory(collection="t_coo", namespace="coo")
    m.record("interac settlement backlog spiked on 2026-07-30", kind="observation")
    m.record("card decline rate is nominal", kind="observation")
    hits = m.recall("what happened with interac settlement?", k=1)
    assert hits and "interac" in hits[0].lower()


def test_namespace_isolates_agents():
    from qdrant_client import QdrantClient
    from csuite.harness.memory import _embedder
    client = QdrantClient(":memory:")
    embed = _embedder()
    coo = HarnessMemory(client, "shared", embed, namespace="coo")
    cfo = HarnessMemory(client, "shared", embed, namespace="cfo")
    coo.record("coo note about float", kind="observation")
    cfo.record("cfo note about raroc", kind="observation")
    assert all("raroc" not in h.lower() for h in coo.recall("float", k=5))


def test_safe_memory_swallows_failures():
    class Boom:
        def recall(self, *a, **k):
            raise RuntimeError("qdrant down")

        def record(self, *a, **k):
            raise RuntimeError("qdrant down")

    safe = SafeMemory(Boom())
    assert safe.recall("x") == []      # no raise
    assert safe.record("y") is None    # no raise
    assert SafeMemory(None).recall("x") == []  # total no-op
