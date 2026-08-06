"""Durable, per-agent semantic memory over Qdrant (fastembed/CPU embeddings).
Agent-agnostic: a `namespace` (e.g. "coo") scopes an agent's notes so several
C-suite agents can share one Qdrant. Generalizes agent/memory.py's QdrantMemory
(which scoped by customer_id). Best-effort via SafeMemory."""
from __future__ import annotations
import time
import uuid
from typing import Optional

from qdrant_client import QdrantClient, models


def _embedder():
    from fastembed import TextEmbedding
    return TextEmbedding()  # small default CPU model


class HarnessMemory:
    def __init__(self, client: QdrantClient, collection: str, embed, *, namespace: str):
        self.client = client
        self.collection = collection
        self.namespace = namespace
        self._embed = embed
        self._dim = len(next(iter(embed.embed(["dim probe"]))))
        if not client.collection_exists(collection):
            client.create_collection(
                collection,
                vectors_config=models.VectorParams(size=self._dim, distance=models.Distance.COSINE))

    @classmethod
    def in_memory(cls, collection: str = "coo_memory", namespace: str = "coo") -> "HarnessMemory":
        return cls(QdrantClient(":memory:"), collection, _embedder(), namespace=namespace)

    @classmethod
    def from_settings(cls, settings) -> "HarnessMemory":
        return cls(QdrantClient(url=settings.qdrant_url), settings.memory_collection,
                   _embedder(), namespace=settings.memory_namespace)

    def _vec(self, text: str):
        return list(next(iter(self._embed.embed([text]))))

    def _filter(self, kind: Optional[str] = "observation"):
        must = [models.FieldCondition(
            key="namespace", match=models.MatchValue(value=self.namespace))]
        if kind is not None:
            # recall surfaces agent observations only. Context-compaction dumps
            # are written with kind="context" for recoverability, but must never
            # be replayed as recalled facts (they'd re-ground stale figures in the
            # verifier and pollute the prompt), so they're filtered out here.
            must.append(models.FieldCondition(
                key="kind", match=models.MatchValue(value=kind)))
        return models.Filter(must=must)

    def record(self, fact: str, *, kind: str = "observation",
               thread_id: Optional[str] = None) -> str:
        pid = uuid.uuid4().hex
        self.client.upsert(self.collection, points=[models.PointStruct(
            id=pid, vector=self._vec(fact),
            payload={"namespace": self.namespace, "kind": kind, "fact": fact,
                     "thread_id": thread_id, "ts": time.time()})])
        return pid

    def recall(self, query: str, k: int = 3) -> list[str]:
        hits = self.client.query_points(self.collection, query=self._vec(query),
                                        limit=k, query_filter=self._filter()).points
        return [h.payload["fact"] for h in hits]


class SafeMemory:
    """Best-effort wrapper: memory is an enhancement, never a dependency. If the
    inner store is None or raises, recall yields [] and record is a no-op, so the
    agent still answers from live tools."""

    def __init__(self, inner: Optional[HarnessMemory]):
        self._inner = inner

    def recall(self, query: str, k: int = 3) -> list[str]:
        if self._inner is None:
            return []
        try:
            return self._inner.recall(query, k)
        except Exception:  # noqa: BLE001
            return []

    def record(self, fact: str, *, kind: str = "observation",
               thread_id: Optional[str] = None):
        if self._inner is None:
            return None
        try:
            return self._inner.record(fact, kind=kind, thread_id=thread_id)
        except Exception:  # noqa: BLE001
            return None
