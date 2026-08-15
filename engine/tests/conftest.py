"""Shared fixtures: a temp SQLite state DB seeded with a known candidate + non-candidate."""
from __future__ import annotations

import pytest

from engine.analysis_core.io.statestore import StateStore
from engine.synth import seed_cluster, synthetic_cluster
from engine.synth.generate import candidate_workload, noncandidate_workload

CANDIDATE_UID = "vmw-costing/Deployment/vmw-costing1"
STEADY_UID = "vmw-costing/Deployment/steady-svc"


def seed_workload(store, cluster_id, uid, kind, name, resources, **identity):
    ns = uid.split("/")[0]
    store.upsert_workload(
        cluster_id, uid, ns, kind, name,
        replicas=identity.get("replicas", 1),
        requests_cpu_m=identity.get("requests_cpu_m"),
        requests_mem_bytes=identity.get("requests_mem_bytes"),
    )
    rows = []
    for res, points in resources.items():
        for ts, val in points:
            rows.append({
                "cluster_id": cluster_id, "workload_uid": uid, "resource": res,
                "ts": ts, "value": val,
                "unit": "cores" if res == "cpu" else "bytes",
                "is_rate": res == "cpu",
            })
    store.insert_metric_samples(rows)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "state.db")


@pytest.fixture
def store(db_path):
    s = StateStore("sqlite", db_path)
    s.apply_schema()
    yield s
    s.close()


@pytest.fixture
def seeded(store):
    cid = store.ensure_cluster("demo")
    seed_workload(store, cid, CANDIDATE_UID, "Deployment", "vmw-costing1",
                  candidate_workload(), replicas=2, requests_cpu_m=500, requests_mem_bytes=1_500_000_000)
    seed_workload(store, cid, STEADY_UID, "Deployment", "steady-svc",
                  noncandidate_workload(), replicas=3, requests_cpu_m=1000, requests_mem_bytes=2_000_000_000)
    return store, cid


@pytest.fixture
def synth_cluster():
    return synthetic_cluster()


@pytest.fixture
def seeded_cluster(store, synth_cluster):
    """The full synthetic cluster (5 workloads + 1 interaction edge) seeded into the DB."""
    cid = seed_cluster(store, synth_cluster)
    return store, cid, synth_cluster
