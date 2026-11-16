"""The cluster sampler: the k3s server's usage every two minutes, as rows of
platform.cluster_usage, from a CronJob beside the runners (Sustenaut/shared-infra's
infra/ci-runners/).

One sample is
- a `node` row per node: CPU and memory in use (metrics-server) and allocatable;
- a `namespace` row per namespace: what its pods use and what its running pods request;
- one `ci` row: namespace ci's quota (requests used and allowed) and the scale set's runners
  (running, pending, its maxRunners);
- a `runner` row per runner pod: what it uses, and its runner's and Docker's cgroup
  memory.peak so far. A pod's last row before it ends holds its job's peak to within a
  sample; runner_name is ci_jobs.runner_name.

It reads the cluster's own API as its service account (k8s:ci/usage), which may only get
and list nodes, pods, their metrics, namespace ci's quota and ARC's AutoscalingRunnerSets
and EphemeralRunners. The cgroup files it reads from the node's /sys/fs/cgroup, mounted
read-only: memory.peak is in no API. The cluster's API is no outside service, so its
calls go through no Caller; the sample goes to storage's ci-usage buffer through one.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..avrofile import encode_cluster_usage
from . import place

FOLDER = "inbox/cluster-usage/"
API = "https://kubernetes.default.svc"
ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
CI = "ci"
QUOTA = "ci"
ARC = "/apis/actions.github.com/v1alpha1/namespaces/ci"
SCALE_SET_LABEL = "actions.github.com/scale-set-name"
RUNNER, DIND = "runner", "dind"

_CPU = {"n": 1e-6, "u": 1e-3, "m": 1, "": 1000}
_MEMORY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "": 1}
_QUANTITY = re.compile(r"^([0-9.eE+-]+?)([a-zA-Z]*)$")


def millicores(quantity: str | None) -> int | None:
    """A Kubernetes CPU quantity (2, 250m, 123456789n) in millicores."""
    if quantity is None:
        return None
    number, unit = _QUANTITY.match(str(quantity)).groups()
    return round(float(number) * _CPU[unit])


def octets(quantity: str | None) -> int | None:
    """A Kubernetes memory quantity (512Mi, 1Gi, 123456Ki, 1e9) in bytes."""
    if quantity is None:
        return None
    number, unit = _QUANTITY.match(str(quantity)).groups()
    return round(float(number) * _MEMORY[unit])


class Kube:
    """The cluster's API, as the pod's service account."""

    def __init__(self, base: str = API, account: Path = ACCOUNT):
        self.base, self.account = base, account
        self.context = ssl.create_default_context(cafile=str(account / "ca.crt"))

    def get(self, path: str) -> Any:
        token = (self.account / "token").read_text(encoding="utf-8").strip()
        request = urllib.request.Request(f"{self.base}{path}", headers={"Authorization": f"Bearer {token}",
                                                                        "Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=30, context=self.context) as answer:  # noqa: S310 - the cluster's own API
            return json.load(answer)


def requested(pod: dict) -> tuple[int, int]:
    """What a pod requests, as the scheduler counts it: its containers and sidecars, or
    its largest init container if that is more."""
    spec = pod.get("spec", {})

    def asks(container: dict) -> tuple[int, int]:
        ask = container.get("resources", {}).get("requests", {})
        return millicores(ask.get("cpu")) or 0, octets(ask.get("memory")) or 0

    running = [asks(c) for c in spec.get("containers", [])]
    running += [asks(c) for c in spec.get("initContainers", []) if c.get("restartPolicy") == "Always"]
    cpu, memory = sum(c for c, _ in running), sum(m for _, m in running)
    for c in spec.get("initContainers", []):
        if c.get("restartPolicy") != "Always":
            init_cpu, init_memory = asks(c)
            cpu, memory = max(cpu, init_cpu), max(memory, init_memory)
    return cpu, memory


class Cgroups:
    """Containers' cgroup files on the node, found by container id."""

    def __init__(self, root: Path):
        self.root = root
        self._dirs: dict[str, Path] | None = None

    def _index(self) -> dict[str, Path]:
        if self._dirs is None:
            self._dirs = {}
            if self.root.is_dir():
                for here, dirs, _ in os.walk(self.root):
                    for name in dirs:
                        found = re.search(r"([0-9a-f]{64})", name)
                        if found:
                            self._dirs[found[1]] = Path(here) / name
        return self._dirs

    def peak(self, container_id: str | None) -> int | None:
        """A container's memory.peak, or None if it can't be read."""
        if not container_id:
            return None
        where = self._index().get(container_id.rpartition("://")[2])
        try:
            return int((where / "memory.peak").read_text().strip()) if where else None
        except (OSError, ValueError):
            return None


@dataclass
class Sampler:
    kube: Any  # Kube, or a stand-in with get(path)
    cgroups: Cgroups
    buffer: Any  # a Storage of the ci-usage tenant: put_buffer
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    report: Callable[[str], None] = print

    def rows(self) -> list[dict]:
        observed = self.clock().replace(microsecond=0)
        nodes = self.kube.get("/api/v1/nodes")["items"]
        node_use = {m["metadata"]["name"]: m["usage"] for m in self.kube.get("/apis/metrics.k8s.io/v1beta1/nodes")["items"]}
        pods = self.kube.get("/api/v1/pods")["items"]
        pod_use = {(m["metadata"]["namespace"], m["metadata"]["name"]): m
                   for m in self.kube.get("/apis/metrics.k8s.io/v1beta1/pods")["items"]}
        quota = self.kube.get(f"/api/v1/namespaces/{CI}/resourcequotas/{QUOTA}").get("status", {})
        sets = self.kube.get(f"{ARC}/autoscalingrunnersets")["items"]
        runners = self.kube.get(f"{ARC}/ephemeralrunners")["items"]

        def base(kind: str, **fields: Any) -> dict:
            return {"observed_at": observed, "kind": kind, "node": None, "namespace": None, "runner_name": None,
                    **fields}

        out = []
        for node in nodes:
            name, use = node["metadata"]["name"], node_use.get(node["metadata"]["name"], {})
            allocatable = node.get("status", {}).get("allocatable", {})
            out.append(base("node", node=name, cpu_millicores=millicores(use.get("cpu")),
                            memory_bytes=octets(use.get("memory")),
                            cpu_capacity_millicores=millicores(allocatable.get("cpu")),
                            memory_capacity_bytes=octets(allocatable.get("memory"))))
        namespaces = sorted({p["metadata"]["namespace"] for p in pods} | {ns for ns, _ in pod_use})
        for namespace in namespaces:
            running = [p for p in pods if p["metadata"]["namespace"] == namespace
                       and p.get("status", {}).get("phase") in ("Running", "Pending")]
            asks = [requested(p) for p in running]
            used = [c for (ns, _), m in pod_use.items() if ns == namespace for c in m.get("containers", [])]
            out.append(base("namespace", namespace=namespace,
                            cpu_millicores=sum(millicores(c["usage"].get("cpu")) or 0 for c in used),
                            memory_bytes=sum(octets(c["usage"].get("memory")) or 0 for c in used),
                            cpu_requests_millicores=sum(c for c, _ in asks),
                            memory_requests_bytes=sum(m for _, m in asks)))
        used, hard = quota.get("used", {}), quota.get("hard", {})
        phases = [r.get("status", {}).get("phase") for r in runners]
        out.append(base("ci", namespace=CI,
                        quota_cpu_used=millicores(used.get("requests.cpu")),
                        quota_cpu_hard=millicores(hard.get("requests.cpu")),
                        quota_memory_used=octets(used.get("requests.memory")),
                        quota_memory_hard=octets(hard.get("requests.memory")),
                        runners_running=sum(1 for p in phases if p == "Running"),
                        runners_pending=sum(1 for p in phases if p != "Running"),
                        runners_max=sum(s.get("spec", {}).get("maxRunners") or 0 for s in sets) if sets else None))
        for pod in pods:
            meta = pod["metadata"]
            if meta["namespace"] != CI or SCALE_SET_LABEL not in meta.get("labels", {}):
                continue
            ids = {c["name"]: c.get("containerID") for c in pod.get("status", {}).get("containerStatuses", [])
                   + pod.get("status", {}).get("initContainerStatuses", [])}
            use = pod_use.get((CI, meta["name"]), {}).get("containers", [])
            out.append(base("runner", node=pod.get("spec", {}).get("nodeName"), namespace=CI, runner_name=meta["name"],
                            cpu_millicores=sum(millicores(c["usage"].get("cpu")) or 0 for c in use) if use else None,
                            memory_bytes=sum(octets(c["usage"].get("memory")) or 0 for c in use) if use else None,
                            runner_memory_peak_bytes=self.cgroups.peak(ids.get(RUNNER)),
                            dind_memory_peak_bytes=self.cgroups.peak(ids.get(DIND))))
        return out

    def sample(self) -> int:
        found = self.rows()
        data = encode_cluster_usage(found)
        self.buffer.put_buffer(place(FOLDER, "usage", found[0]["observed_at"], data), data)
        self.report(f"{len(found)} usage rows written")
        return len(found)
