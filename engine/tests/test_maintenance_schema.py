"""M2 — polymorphic runs + maintenance-result CRUD.

Every M1-era test that goes through `run_analysis`/`create_analysis_run` covers
the run_type='job' default already; this file adds direct coverage for the new
'maintenance' path so the schema/StateStore contract has explicit tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _create_run(store, cluster_id, *, name, run_type):
    return store.create_analysis_run(
        name=name, cluster_id=cluster_id, scope="all", config={},
        data_as_of=None, stale=True, ttl_hours=24, run_type=run_type,
    )


def test_run_type_defaults_to_job_and_persists(store):
    cid = store.ensure_cluster("c1")
    run_id = store.create_analysis_run(
        name="run-default", cluster_id=cid, scope="all", config={"resources": ["cpu"]},
        data_as_of=None, stale=True, ttl_hours=24,
    )
    row = store.get_run(run_id)
    assert row["run_type"] == "job"

    listed = store.list_runs(cluster_id=cid)
    assert listed[0]["run_type"] == "job"


def test_explicit_run_type_maintenance(store):
    cid = store.ensure_cluster("c1")
    run_id = _create_run(store, cid, name="run-maint", run_type="maintenance")
    row = store.get_run(run_id)
    assert row["run_type"] == "maintenance"


def test_maintenance_result_roundtrip(store):
    cid = store.ensure_cluster("c1")
    run_id = _create_run(store, cid, name="run-1", run_type="maintenance")

    start = datetime(2026, 8, 1, 3, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    deadline = start + timedelta(days=3)

    result_id = store.insert_maintenance_result(run_id, {
        "maintenance_for_uid": "ns-a/Deployment/target",
        "workload_kind": "Deployment", "workload_name": "target", "namespace": "ns-a",
        "recommended_start": start, "recommended_end": end,
        "duration_min": 30.0, "deadline": deadline,
        "impact_score": 1.0, "confidence": "high",
        "summary_text": "target idle at 03:00 UTC; 1 dep also idle.",
        "impacted_apps": [
            {"workload_uid": "ns-b/Deployment/caller-1", "workload_kind": "Deployment",
             "workload_name": "caller-1", "namespace": "ns-b",
             "period_hours": 24.0, "active_fraction": 0.15, "impact_score": 0.0,
             "note": "24h cycle; idle in the chosen window"},
            {"workload_uid": "ns-b/Deployment/caller-2", "workload_kind": "Deployment",
             "workload_name": "caller-2", "namespace": "ns-b",
             "period_hours": None, "active_fraction": None, "impact_score": 1.0,
             "note": "no periodic signal; assume always-active"},
        ],
        "evidence": [
            {"workload_uid": "ns-a/Deployment/target", "resource": "cpu",
             "forecast_series": [{"ts": "2026-08-01T03:00:00Z", "value": 0.0}],
             "active_windows": []},
        ],
    })
    assert result_id > 0

    results = store.get_maintenance_results(run_id)
    assert len(results) == 1
    r = results[0]
    assert r["maintenance_for_uid"] == "ns-a/Deployment/target"
    assert r["duration_min"] == 30.0
    assert r["confidence"] == "high"
    assert r["impact_score"] == 1.0

    apps = store.get_maintenance_impacted_apps(result_id)
    assert [a["workload_uid"] for a in apps] == [
        "ns-b/Deployment/caller-1", "ns-b/Deployment/caller-2",
    ]
    assert apps[0]["active_fraction"] == 0.15
    assert apps[1]["period_hours"] is None

    evidence = store.get_maintenance_evidence(result_id)
    assert len(evidence) == 1
    assert evidence[0]["resource"] == "cpu"
    # JSON round-trips as a Python list of dicts on both SQLite and Postgres.
    series = evidence[0]["forecast_series"]
    assert isinstance(series, list) and series[0]["value"] == 0.0
    assert evidence[0]["active_windows"] == []


def test_maintenance_and_job_runs_coexist(store):
    cid = store.ensure_cluster("c1")
    job_id = _create_run(store, cid, name="job-1", run_type="job")
    maint_id = _create_run(store, cid, name="maint-1", run_type="maintenance")

    # Both surface via list_runs with the correct discriminator.
    runs = {r["id"]: r["run_type"] for r in store.list_runs(cluster_id=cid)}
    assert runs[job_id] == "job"
    assert runs[maint_id] == "maintenance"


def test_run_type_check_constraint_rejects_bogus_value(store):
    """SQLite enforces the CHECK constraint added in migration 0002."""
    import sqlite3

    cid = store.ensure_cluster("c1")
    try:
        store.create_analysis_run(
            name="bogus", cluster_id=cid, scope="all", config={},
            data_as_of=None, stale=True, ttl_hours=24, run_type="whatever",
        )
    except sqlite3.IntegrityError:
        return
    raise AssertionError("CHECK constraint on run_type did not fire")
