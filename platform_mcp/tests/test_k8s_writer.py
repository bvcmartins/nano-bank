from types import SimpleNamespace as NS
from platform_mcp.config import Settings
from platform_mcp.k8s_writer import K8sWriter


def _settings():
    return Settings.from_env({"PLATFORM_ACTOR_CONTEXTS": "kind-nano-bank-actor=nano-bank"})


class _FakeApps:
    def __init__(self):
        self.patched = []   # (name, body)

    def patch_namespaced_deployment(self, name, namespace, body):
        self.patched.append((name, body))
        return NS(metadata=NS(name=name))

    def list_namespaced_replica_set(self, namespace):
        # cfo has revisions 4 and 5; the rollback target is 4. The pod template is
        # a plain dict (what sanitize_for_serialization yields for a real model).
        return NS(items=[
            NS(metadata=NS(name="cfo-old", namespace="nano-bank",
                           owner_references=[NS(kind="Deployment", name="cfo")],
                           annotations={"deployment.kubernetes.io/revision": "4"}),
               spec=NS(template={"metadata": {"labels": {"pod": "old"}},
                                 "spec": {"containers": []}})),
            NS(metadata=NS(name="cfo-new", namespace="nano-bank",
                           owner_references=[NS(kind="Deployment", name="cfo")],
                           annotations={"deployment.kubernetes.io/revision": "5"}),
               spec=NS(template={"metadata": {"labels": {"pod": "new"}},
                                 "spec": {"containers": []}})),
        ])


def _loader_factory(fake):
    def _loader(path, context):
        assert context == "kind-nano-bank-actor"
        return fake
    return _loader


def test_rollout_restart_patches_restarted_at():
    fake = _FakeApps()
    w = K8sWriter(_settings(), loader=_loader_factory(fake))
    out = w.rollout_restart("nano-bank", "coo")
    assert "restarted_at" in out
    name, body = fake.patched[0]
    assert name == "coo"
    ann = body["spec"]["template"]["metadata"]["annotations"]
    assert ann["kubectl.kubernetes.io/restartedAt"] == out["restarted_at"]


def test_rollback_patches_prior_template():
    fake = _FakeApps()
    w = K8sWriter(_settings(), loader=_loader_factory(fake))
    out = w.rollback("nano-bank", "cfo", 4)
    assert out["rolled_back_to"] == 4
    name, body = fake.patched[0]
    assert name == "cfo"
    # the deployment's template is replaced by revision 4's ("old")
    assert body["spec"]["template"]["metadata"]["labels"] == {"pod": "old"}
