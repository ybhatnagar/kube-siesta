"""Upstream dependency traversal — reverse edges over `interactions`."""
from __future__ import annotations

from engine.recommenders.maintenance.deps import all_workload_uids, upstream_deps


def _seed_edge(store, cid, src, dst):
    store.insert_interactions([{
        "cluster_id": cid, "src_workload_uid": src, "dst_workload_uid": dst,
        "avg_count": 1.0, "window_start": None, "window_end": None,
    }])


def test_direct_upstream_only(store):
    cid = store.ensure_cluster("c1")
    _seed_edge(store, cid, "ns/D/caller", "ns/D/target")
    assert upstream_deps(store, cid, "ns/D/target") == ["ns/D/caller"]


def test_transitive_upstream(store):
    cid = store.ensure_cluster("c1")
    _seed_edge(store, cid, "ns/D/leaf", "ns/D/mid")
    _seed_edge(store, cid, "ns/D/mid", "ns/D/target")
    deps = upstream_deps(store, cid, "ns/D/target")
    assert set(deps) == {"ns/D/mid", "ns/D/leaf"}
    # BFS order: direct caller before its transitive parent.
    assert deps.index("ns/D/mid") < deps.index("ns/D/leaf")


def test_cycle_is_safe(store):
    cid = store.ensure_cluster("c1")
    _seed_edge(store, cid, "ns/D/a", "ns/D/b")
    _seed_edge(store, cid, "ns/D/b", "ns/D/a")   # cycle!
    _seed_edge(store, cid, "ns/D/a", "ns/D/target")
    deps = upstream_deps(store, cid, "ns/D/target")
    assert set(deps) == {"ns/D/a", "ns/D/b"}


def test_target_never_appears_as_own_dep(store):
    cid = store.ensure_cluster("c1")
    _seed_edge(store, cid, "ns/D/target", "ns/D/target")  # self-loop shouldn't count
    _seed_edge(store, cid, "ns/D/other", "ns/D/target")
    deps = upstream_deps(store, cid, "ns/D/target")
    assert "ns/D/target" not in deps
    assert deps == ["ns/D/other"]


def test_all_workload_uids_puts_target_first(store):
    order = all_workload_uids("t", ["a", "b"])
    assert order == ["t", "a", "b"]
    # dedup preserved even if a caller duplicates the target
    assert all_workload_uids("t", ["a", "t", "b", "a"]) == ["t", "a", "b"]
