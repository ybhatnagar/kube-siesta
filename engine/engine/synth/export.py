"""CSV / JSON export + load for synthetic fixtures, and a DB-seed helper.

The exported metrics format is what a future CSV import connector (`file` data source)
would consume, so the same fixtures drive both DB seeding and an end-to-end demo.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from typing import Any

from .generate import SynthCluster, SynthWorkload

_UNITS = {"cpu": "cores", "memory": "bytes", "net_tx": "bytes/s", "net_rx": "bytes/s", "ephemeral_storage": "bytes"}
_RATE = {"cpu", "net_tx", "net_rx"}

METRIC_COLUMNS = ["cluster", "workload_uid", "namespace", "kind", "name", "resource", "ts", "value", "unit", "is_rate"]
WORKLOAD_COLUMNS = ["cluster", "workload_uid", "namespace", "kind", "name", "replicas",
                    "requests_cpu_m", "requests_mem_bytes", "is_candidate", "note"]
INTERACTION_COLUMNS = ["cluster", "src_uid", "dst_uid", "avg_count"]


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def unit_for(resource: str) -> str:
    return _UNITS.get(resource, "")


def is_rate(resource: str) -> bool:
    return resource in _RATE


# --- flat rows -------------------------------------------------------------

def metric_rows(cluster: SynthCluster) -> list[dict]:
    """One dict per sample — the tidy metrics table (CSV/connector shape)."""
    rows: list[dict] = []
    for w in cluster.workloads:
        for resource, points in w.resources.items():
            unit, rate = unit_for(resource), is_rate(resource)
            for ts, val in points:
                rows.append({
                    "cluster": cluster.name, "workload_uid": w.uid, "namespace": w.namespace,
                    "kind": w.kind, "name": w.name, "resource": resource,
                    "ts": _iso(ts), "value": float(val), "unit": unit, "is_rate": rate,
                })
    return rows


# --- JSON ------------------------------------------------------------------

def to_dict(cluster: SynthCluster) -> dict:
    return {
        "cluster": cluster.name,
        "candidates": sorted(cluster.candidate_uids),
        "workloads": [
            {
                "uid": w.uid, "namespace": w.namespace, "kind": w.kind, "name": w.name,
                "replicas": w.replicas, "requests_cpu_m": w.requests_cpu_m,
                "requests_mem_bytes": w.requests_mem_bytes, "is_candidate": w.is_candidate, "note": w.note,
                "resources": {res: [[_iso(ts), float(v)] for ts, v in pts] for res, pts in w.resources.items()},
            }
            for w in cluster.workloads
        ],
        "interactions": cluster.interactions,
    }


def from_dict(doc: dict) -> SynthCluster:
    workloads = [
        SynthWorkload(
            uid=w["uid"], namespace=w["namespace"], kind=w["kind"], name=w["name"],
            replicas=w["replicas"], requests_cpu_m=w["requests_cpu_m"], requests_mem_bytes=w["requests_mem_bytes"],
            resources={res: [(_parse(ts), float(v)) for ts, v in pts] for res, pts in w["resources"].items()},
            is_candidate=w["is_candidate"], note=w.get("note", ""),
        )
        for w in doc["workloads"]
    ]
    return SynthCluster(name=doc["cluster"], workloads=workloads, interactions=doc.get("interactions", []))


def export_json(cluster: SynthCluster, path: str) -> str:
    with open(path, "w") as f:
        json.dump(to_dict(cluster), f, indent=2)
    return path


def load_json(path: str) -> SynthCluster:
    with open(path) as f:
        return from_dict(json.load(f))


# --- CSV -------------------------------------------------------------------

def export_csv(cluster: SynthCluster, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    paths = {
        "metrics": os.path.join(out_dir, "metrics.csv"),
        "workloads": os.path.join(out_dir, "workloads.csv"),
        "interactions": os.path.join(out_dir, "interactions.csv"),
    }
    with open(paths["metrics"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=METRIC_COLUMNS)
        w.writeheader()
        w.writerows(metric_rows(cluster))
    with open(paths["workloads"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=WORKLOAD_COLUMNS)
        w.writeheader()
        for wl in cluster.workloads:
            w.writerow({
                "cluster": cluster.name, "workload_uid": wl.uid, "namespace": wl.namespace,
                "kind": wl.kind, "name": wl.name, "replicas": wl.replicas,
                "requests_cpu_m": wl.requests_cpu_m, "requests_mem_bytes": wl.requests_mem_bytes,
                "is_candidate": int(wl.is_candidate), "note": wl.note,
            })
    with open(paths["interactions"], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=INTERACTION_COLUMNS)
        w.writeheader()
        for e in cluster.interactions:
            w.writerow({"cluster": cluster.name, "src_uid": e["src_uid"], "dst_uid": e["dst_uid"], "avg_count": e["avg_count"]})
    return paths


def load_csv(out_dir: str) -> SynthCluster:
    with open(os.path.join(out_dir, "workloads.csv")) as f:
        meta = {r["workload_uid"]: r for r in csv.DictReader(f)}
    resources: dict[str, dict[str, list]] = {uid: {} for uid in meta}
    cluster_name = "synth"
    with open(os.path.join(out_dir, "metrics.csv")) as f:
        for r in csv.DictReader(f):
            cluster_name = r["cluster"]
            resources.setdefault(r["workload_uid"], {}).setdefault(r["resource"], []).append(
                (_parse(r["ts"]), float(r["value"]))
            )
    workloads = [
        SynthWorkload(
            uid=uid, namespace=m["namespace"], kind=m["kind"], name=m["name"],
            replicas=int(m["replicas"]), requests_cpu_m=int(m["requests_cpu_m"]),
            requests_mem_bytes=int(m["requests_mem_bytes"]), resources=resources.get(uid, {}),
            is_candidate=bool(int(m["is_candidate"])), note=m.get("note", ""),
        )
        for uid, m in meta.items()
    ]
    interactions = []
    ipath = os.path.join(out_dir, "interactions.csv")
    if os.path.exists(ipath):
        with open(ipath) as f:
            interactions = [
                {"src_uid": r["src_uid"], "dst_uid": r["dst_uid"], "avg_count": float(r["avg_count"])}
                for r in csv.DictReader(f)
            ]
    return SynthCluster(name=cluster_name, workloads=workloads, interactions=interactions)


# --- DB seeding ------------------------------------------------------------

def seed_cluster(store: Any, cluster: SynthCluster) -> int:
    """Seed tiers 2–3 from a fixture; returns the cluster id. Store is duck-typed
    (any StateStore) so this stays decoupled from the io layer."""
    cid = store.ensure_cluster(cluster.name)
    rows: list[dict] = []
    for w in cluster.workloads:
        store.upsert_workload(
            cid, w.uid, w.namespace, w.kind, w.name,
            replicas=w.replicas, requests_cpu_m=w.requests_cpu_m, requests_mem_bytes=w.requests_mem_bytes,
        )
        for resource, points in w.resources.items():
            unit, rate = unit_for(resource), is_rate(resource)
            for ts, val in points:
                rows.append({
                    "cluster_id": cid, "workload_uid": w.uid, "resource": resource,
                    "ts": ts, "value": val, "unit": unit, "is_rate": rate,
                })
    store.insert_metric_samples(rows)
    if cluster.interactions and hasattr(store, "insert_interactions"):
        store.insert_interactions([
            {"cluster_id": cid, "src_workload_uid": e["src_uid"], "dst_workload_uid": e["dst_uid"],
             "avg_count": e["avg_count"]}
            for e in cluster.interactions
        ])
    return cid
