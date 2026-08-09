"""Read-only reads over the configured kube contexts. The `loader` seam returns
(AppsV1Api, CoreV1Api) per context; the default builds real clients from the
mounted kubeconfig, and tests inject a fake (no live cluster). Every read returns
plain dicts tagged with the cluster label + namespace, in the shapes metrics.py
consumes. Read-only: only list_* calls are made."""
from __future__ import annotations
from typing import Callable, Optional

from .config import Settings


def _default_loader(kubeconfig_path: str, context: str):
    from kubernetes import client, config
    config.load_kube_config(config_file=kubeconfig_path, context=context)
    return client.AppsV1Api(), client.CoreV1Api()


def _owner_deployment(rs) -> Optional[str]:
    for ref in (getattr(rs.metadata, "owner_references", None) or []):
        if getattr(ref, "kind", None) == "Deployment":
            return ref.name
    return None


def _revision(rs) -> Optional[int]:
    ann = getattr(rs.metadata, "annotations", None) or {}
    rev = ann.get("deployment.kubernetes.io/revision")
    return int(rev) if rev is not None else None


class K8sClient:
    def __init__(self, settings: Settings,
                 loader: Optional[Callable] = None):
        self._s = settings
        self._loader = loader or _default_loader

    def _apis(self):
        for context, label in self._s.contexts:
            apps, core = self._loader(self._s.kubeconfig_path, context)
            yield label, apps, core

    def deployments(self) -> list[dict]:
        out = []
        for label, apps, _core in self._apis():
            for d in apps.list_deployment_for_all_namespaces().items:
                st = d.status
                images = [c.image for c in d.spec.template.spec.containers]
                conds = [{"type": c.type, "status": c.status, "reason": c.reason}
                         for c in (getattr(st, "conditions", None) or [])]
                out.append({
                    "cluster": label, "namespace": d.metadata.namespace,
                    "name": d.metadata.name,
                    "desired": d.spec.replicas or 0,
                    "ready": getattr(st, "ready_replicas", 0) or 0,
                    "available": getattr(st, "available_replicas", 0) or 0,
                    "updated": getattr(st, "updated_replicas", 0) or 0,
                    "unavailable": getattr(st, "unavailable_replicas", 0) or 0,
                    "images": images, "conditions": conds,
                })
        return out

    def pods(self) -> list[dict]:
        out = []
        for label, _apps, core in self._apis():
            for p in core.list_pod_for_all_namespaces().items:
                containers = []
                for cs in (getattr(p.status, "container_statuses", None) or []):
                    waiting = getattr(getattr(cs.state, "waiting", None), "reason", None)
                    containers.append({
                        "name": cs.name, "ready": bool(cs.ready),
                        "restart_count": int(cs.restart_count or 0),
                        "waiting_reason": waiting,
                    })
                out.append({"cluster": label, "namespace": p.metadata.namespace,
                            "name": p.metadata.name, "phase": p.status.phase,
                            "containers": containers})
        return out

    def replicasets(self) -> list[dict]:
        out = []
        for label, apps, _core in self._apis():
            for rs in apps.list_replica_set_for_all_namespaces().items:
                out.append({
                    "cluster": label, "namespace": rs.metadata.namespace,
                    "name": rs.metadata.name,
                    "owner_deployment": _owner_deployment(rs),
                    "revision": _revision(rs),
                    "desired": rs.spec.replicas or 0,
                    "ready": getattr(rs.status, "ready_replicas", 0) or 0,
                })
        return out

    def events(self) -> list[dict]:
        out = []
        for label, _apps, core in self._apis():
            for ev in core.list_event_for_all_namespaces().items:
                io = getattr(ev, "involved_object", None)
                out.append({
                    "cluster": label, "namespace": ev.metadata.namespace,
                    "type": ev.type, "reason": ev.reason, "message": ev.message,
                    "object": f"{getattr(io,'kind','')}/{getattr(io,'name','')}",
                })
        return out
