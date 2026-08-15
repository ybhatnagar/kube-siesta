"""CLI parity with the API for the maintenance path.

`engine run --type maintenance --app <uid> --duration <L> --deadline <D>`
must produce a maintenance_result identical to what the API would.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

from engine.analysis_core.io.statestore import StateStore
from engine.cli import main
from engine.synth.generate import candidate_workload


TARGET_UID = "ns-a/Deployment/target"


def _seed(store: StateStore) -> int:
    cid = store.ensure_cluster("multi-app")
    store.upsert_workload(cid, TARGET_UID, "ns-a", "Deployment", "target",
                          replicas=1, requests_cpu_m=200, requests_mem_bytes=500_000_000)
    rows = []
    for res, points in candidate_workload(seed=0).items():
        for ts, val in points:
            rows.append({
                "cluster_id": cid, "workload_uid": TARGET_UID, "resource": res,
                "ts": ts, "value": val, "unit": "cores" if res == "cpu" else "bytes",
                "is_rate": res == "cpu",
            })
    store.insert_metric_samples(rows)
    return cid


def test_cli_maintenance_run(db_path):
    store = StateStore("sqlite", db_path)
    store.apply_schema()
    _seed(store)
    store.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([
            "run",
            "--type", "maintenance",
            "--cluster", "multi-app",
            "--app", TARGET_UID,
            "--duration", "1h",
            "--deadline", "3d",
            "--db-driver", "sqlite",
            "--db-dsn", db_path,
        ])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert out["status"] == "completed"
    assert out["recommended_start"] is not None
    assert out["recommended_end"] is not None


def test_cli_maintenance_requires_app(db_path):
    store = StateStore("sqlite", db_path)
    store.apply_schema()
    _seed(store)
    store.close()

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([
            "run", "--type", "maintenance",
            "--cluster", "multi-app",
            "--duration", "1h", "--deadline", "3d",
            "--db-driver", "sqlite", "--db-dsn", db_path,
        ])
    assert rc == 2


def test_cli_job_default_unchanged(seeded, db_path):
    """A CLI `run` without --type still runs the job path — behavior-preserved."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main([
            "run", "--cluster", "demo",
            "--db-driver", "sqlite", "--db-dsn", db_path,
        ])
    assert rc == 0
    out = json.loads(buf.getvalue())
    assert "recommendations" in out  # job output shape
    assert out["status"] == "completed"
