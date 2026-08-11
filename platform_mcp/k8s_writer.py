"""The only MUTATING capability in platform_mcp — two narrow patches over the
write-scoped `platform-actor` context. rollout_restart bumps the restartedAt
annotation (what `kubectl rollout restart` does); rollback replaces the
deployment's pod template with a prior ReplicaSet's (what `kubectl rollout undo`
does). `loader` is injectable so tests run with no live cluster."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Callable, Optional

from .config import Settings


def _default_loader(kubeconfig_path: str, context: str):
    from kubernetes import client, config
    config.load_kube_config(config_file=kubeconfig_path, context=context)
    return client.AppsV1Api()


def _namespace_for(cluster: str) -> str:
    # Each cluster's app deployments live in the namespace of the same name.
    return "modern-core" if cluster == "modern-core" else "nano-bank"


class K8sWriter:
    def __init__(self, settings: Settings, loader: Optional[Callable] = None):
        self._s = settings
        self._loader = loader or _default_loader
        self._actor_ctx = {label: ctx for ctx, label in settings.actor_contexts}

    def _apps(self, cluster: str):
        ctx = self._actor_ctx[cluster]
        return self._loader(self._s.kubeconfig_path, ctx)

    def rollout_restart(self, cluster: str, name: str) -> dict:
        ts = datetime.now(timezone.utc).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {
            "kubectl.kubernetes.io/restartedAt": ts}}}}}
        self._apps(cluster).patch_namespaced_deployment(
            name=name, namespace=_namespace_for(cluster), body=body)
        return {"restarted_at": ts}

    def rollback(self, cluster: str, name: str, target_revision: int) -> dict:
        apps = self._apps(cluster)
        ns = _namespace_for(cluster)
        target = None
        for rs in apps.list_namespaced_replica_set(namespace=ns).items:
            ann = getattr(rs.metadata, "annotations", None) or {}
            owners = getattr(rs.metadata, "owner_references", None) or []
            owned = any(getattr(o, "kind", None) == "Deployment" and o.name == name
                        for o in owners)
            if owned and ann.get("deployment.kubernetes.io/revision") == str(target_revision):
                target = rs
                break
        if target is None:
            raise ValueError(f"no ReplicaSet at revision {target_revision} for {name}")
        # Serialize the RS pod template back onto the deployment (rollout undo).
        from kubernetes.client import ApiClient
        template = ApiClient().sanitize_for_serialization(target.spec.template)
        body = {"spec": {"template": template}}
        apps.patch_namespaced_deployment(name=name, namespace=ns, body=body)
        return {"rolled_back_to": target_revision}
