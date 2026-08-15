"""Upstream dependency traversal on the interactions graph.

The maintenance target's *upstream* callers are the workloads impacted by taking
it down — every edge `src -> target` means `src` calls `target`. This walks the
reverse edges from the target uid transitively, deduping and cycle-safe.

Docs/07 §1 step 1 — "traverse all paths arriving on the target node → its
upstream callers are the impacted dependents."
"""
from __future__ import annotations

from typing import Iterable


def upstream_deps(store, cluster_id: int, target_uid: str, max_depth: int = 8) -> list[str]:
    """Return every transitive caller of `target_uid` (excluding the target itself).

    BFS over `store.get_incoming_interactions` — safe under cycles thanks to the
    visited set. `max_depth` guards against pathological graphs; 8 hops is far
    beyond real microservice depths.
    """
    visited = {target_uid}
    order: list[str] = []
    frontier = [target_uid]
    depth = 0
    while frontier and depth < max_depth:
        next_frontier: list[str] = []
        for uid in frontier:
            for edge in store.get_incoming_interactions(cluster_id, uid):
                src = edge["src_workload_uid"]
                if src in visited:
                    continue
                visited.add(src)
                order.append(src)
                next_frontier.append(src)
        frontier = next_frontier
        depth += 1
    return order


def all_workload_uids(target_uid: str, deps: Iterable[str]) -> list[str]:
    """Target first, then unique deps in traversal order."""
    seen = {target_uid}
    out = [target_uid]
    for uid in deps:
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out
