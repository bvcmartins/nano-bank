from types import SimpleNamespace as NS
from platform_mcp.config import Settings
from platform_mcp.k8s_client import K8sClient


def _dep_obj(name, desired, ready, available, updated, unavailable, image, reason):
    return NS(
        metadata=NS(name=name, namespace="nano-bank"),
        spec=NS(replicas=desired, template=NS(spec=NS(containers=[NS(image=image)]))),
        status=NS(ready_replicas=ready, available_replicas=available,
                  updated_replicas=updated, unavailable_replicas=unavailable,
                  conditions=[NS(type="Progressing", status="True", reason=reason)]),
    )


def _pod_obj(name, container, ready, restarts, waiting_reason):
    waiting = NS(reason=waiting_reason) if waiting_reason else None
    return NS(
        metadata=NS(name=name, namespace="nano-bank"),
        status=NS(phase="Running", container_statuses=[
            NS(name=container, ready=ready, restart_count=restarts,
               state=NS(waiting=waiting))]),
    )


def _settings():
    return Settings.from_env({"PLATFORM_CONTEXTS": "kind-nano-bank=nano-bank"})


class _FakeApps:
    def list_deployment_for_all_namespaces(self):
        return NS(items=[_dep_obj("coo", 1, 1, 1, 1, 0, "nano-coo:dev",
                                  "NewReplicaSetAvailable")])

    def list_replica_set_for_all_namespaces(self):
        return NS(items=[NS(metadata=NS(name="coo-abc", namespace="nano-bank",
                       owner_references=[NS(kind="Deployment", name="coo")],
                       annotations={"deployment.kubernetes.io/revision": "3"}),
                       spec=NS(replicas=1), status=NS(ready_replicas=1))])


class _FakeCore:
    def list_pod_for_all_namespaces(self):
        return NS(items=[_pod_obj("coo-1", "coo", True, 0, None)])

    def list_event_for_all_namespaces(self):
        return NS(items=[NS(metadata=NS(namespace="nano-bank"),
                            type="Normal", reason="Scheduled",
                            message="ok", involved_object=NS(kind="Pod", name="coo-1"))])


def _loader(path, context):
    return _FakeApps(), _FakeCore()


def test_deployments_tagged_and_flattened():
    c = K8sClient(_settings(), loader=_loader)
    deps = c.deployments()
    assert deps[0]["cluster"] == "nano-bank"
    assert deps[0]["name"] == "coo"
    assert deps[0]["desired"] == 1 and deps[0]["ready"] == 1
    assert deps[0]["images"] == ["nano-coo:dev"]
    assert deps[0]["conditions"][0]["reason"] == "NewReplicaSetAvailable"


def test_pods_extract_container_restart_and_waiting_reason():
    c = K8sClient(_settings(), loader=_loader)
    pods = c.pods()
    assert pods[0]["name"] == "coo-1"
    assert pods[0]["containers"][0]["restart_count"] == 0
    assert pods[0]["containers"][0]["waiting_reason"] is None


def test_replicasets_carry_owner_and_revision():
    c = K8sClient(_settings(), loader=_loader)
    rss = c.replicasets()
    assert rss[0]["owner_deployment"] == "coo"
    assert rss[0]["revision"] == 3
