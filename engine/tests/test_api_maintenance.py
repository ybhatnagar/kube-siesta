"""API contract for the maintenance run type.

Covers: POST /runs with run_type=maintenance, GET /runs surfacing run_type,
GET /runs/{id}/recommendations returning the maintenance card DTO, the
evidence endpoint returning the impacted apps + forecast series, and
validation on the request body (missing config, bad duration, unknown type).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from engine.analysis_core.io.statestore import StateStore
from engine.api.app import create_app
from engine.synth.generate import candidate_workload


TARGET_UID = "ns-a/Deployment/target"
CALLER_UID = "ns-b/Deployment/caller"


def _client(db_path):
    app = create_app(get_store=lambda: StateStore("sqlite", db_path))
    return TestClient(app)


def _seed_maintenance_cluster(store: StateStore) -> int:
    cid = store.ensure_cluster("multi-app")

    def seed_workload(uid, ns, name, resources):
        store.upsert_workload(cid, uid, ns, "Deployment", name,
                              replicas=1, requests_cpu_m=200, requests_mem_bytes=500_000_000)
        if resources is None:
            return
        rows = []
        for res, points in resources.items():
            for ts, val in points:
                rows.append({
                    "cluster_id": cid, "workload_uid": uid, "resource": res,
                    "ts": ts, "value": val,
                    "unit": "cores" if res == "cpu" else "bytes",
                    "is_rate": res == "cpu",
                })
        store.insert_metric_samples(rows)

    seed_workload(TARGET_UID, "ns-a", "target", candidate_workload(seed=0))
    seed_workload(CALLER_UID, "ns-b", "caller", None)  # no data → aperiodic
    store.insert_interactions([{
        "cluster_id": cid, "src_workload_uid": CALLER_UID, "dst_workload_uid": TARGET_UID,
        "avg_count": 5.0, "window_start": None, "window_end": None,
    }])
    return cid


def _post_maintenance_run(client, **overrides):
    body = {
        "cluster": "multi-app",
        "run_type": "maintenance",
        "maintenance": {
            "target_workload_uid": TARGET_UID,
            "duration": "1h",
            "deadline": "3d",
        },
    }
    body.update(overrides)
    return client.post("/api/v1/runs", json=body)


def test_post_run_and_fetch_maintenance_cards(store, db_path):
    _seed_maintenance_cluster(store)
    client = _client(db_path)

    resp = _post_maintenance_run(client)
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "completed"
    run_id = run["run_id"]

    # /runs/{id} surfaces run_type on the polymorphic run row.
    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert status["run_type"] == "maintenance"

    # Recommendations endpoint dispatches on run_type.
    cards = client.get(f"/api/v1/runs/{run_id}/recommendations").json()
    assert cards["run"]["run_type"] == "maintenance"
    assert cards["run"]["target_workload_uid"] == TARGET_UID
    assert len(cards["recommendations"]) == 1

    card = cards["recommendations"][0]
    assert set(card.keys()) >= {
        "id", "workload", "recommended_start", "recommended_end", "duration_minutes",
        "deadline", "impact_score", "confidence", "summary",
        "impacted_apps_count", "impacted_apps_preview",
    }
    assert card["workload"]["name"] == "target"
    assert card["duration_minutes"] == 60.0
    assert card["impacted_apps_count"] == 1
    assert card["impacted_apps_preview"][0]["name"] == "caller"


def test_maintenance_evidence_dto(store, db_path):
    _seed_maintenance_cluster(store)
    client = _client(db_path)

    run = _post_maintenance_run(client).json()
    run_id = run["run_id"]
    cards = client.get(f"/api/v1/runs/{run_id}/recommendations").json()
    rec_id = cards["recommendations"][0]["id"]

    ev = client.get(f"/api/v1/runs/{run_id}/recommendations/{rec_id}/evidence").json()
    assert ev["recommendation_id"] == rec_id
    assert ev["metrics"]["confidence"] in ("high", "medium", "low")
    assert ev["metrics"]["duration_minutes"] == 60.0

    # Aperiodic caller surfaces with the option-1 note.
    apps = ev["impacted_apps"]
    assert len(apps) == 1
    caller = apps[0]
    assert caller["workload_uid"] == CALLER_UID
    assert caller["period_hours"] is None
    assert "always-active" in (caller["note"] or "")

    # Series present + downsampled per workload.
    assert ev["series"], "expected forecast series for target + deps"
    workloads_in_series = {s["workload_uid"] for s in ev["series"]}
    assert TARGET_UID in workloads_in_series
    assert CALLER_UID in workloads_in_series

    # ?series=false drops the forecast payload.
    ev2 = client.get(
        f"/api/v1/runs/{run_id}/recommendations/{rec_id}/evidence?series=false"
    ).json()
    assert "series" not in ev2
    assert ev2["impacted_apps"]  # still returned


def test_maintenance_body_required(store, db_path):
    _seed_maintenance_cluster(store)
    client = _client(db_path)

    resp = client.post("/api/v1/runs", json={"cluster": "multi-app", "run_type": "maintenance"})
    assert resp.status_code == 400
    assert "maintenance" in resp.json()["detail"].lower()


def test_unknown_run_type_rejected(store, db_path):
    _seed_maintenance_cluster(store)
    client = _client(db_path)

    resp = client.post("/api/v1/runs", json={"cluster": "multi-app", "run_type": "bogus"})
    assert resp.status_code == 400


def test_bad_duration_returns_400(store, db_path):
    _seed_maintenance_cluster(store)
    client = _client(db_path)

    resp = _post_maintenance_run(client, maintenance={
        "target_workload_uid": TARGET_UID, "duration": "nonsense", "deadline": "3d",
    })
    assert resp.status_code == 400


def test_evidence_cross_run_is_404(store, db_path):
    """A rec_id from one run must not resolve under a different run's URL."""
    _seed_maintenance_cluster(store)
    client = _client(db_path)

    # Two independent maintenance runs.
    r1 = _post_maintenance_run(client).json()
    r2 = _post_maintenance_run(client).json()
    rec1 = client.get(f"/api/v1/runs/{r1['run_id']}/recommendations").json()["recommendations"][0]["id"]

    # rec_id belongs to r1; asking under r2 → 404.
    resp = client.get(f"/api/v1/runs/{r2['run_id']}/recommendations/{rec1}/evidence")
    assert resp.status_code == 404


def test_job_run_still_returns_job_dto(seeded, db_path):
    """Regression: existing job POST /runs (no run_type field) works and returns the job card."""
    client = _client(db_path)

    resp = client.post("/api/v1/runs", json={"cluster": "demo", "scope": "all"})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    cards = client.get(f"/api/v1/runs/{run_id}/recommendations").json()
    assert cards["run"]["run_type"] == "job"
    # Job cards keep the job-shaped fields (savings, to_target, cadence, ...).
    card = cards["recommendations"][0]
    assert "savings" in card and "to_target" in card and "cadence" in card
