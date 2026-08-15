"""API-triggered collection wiring — the collector service is stubbed (monkeypatched),
so these run without a live collector. The real end-to-end path is exercised separately
against a running `collector serve` + Prometheus."""
from fastapi.testclient import TestClient

from engine.api.app import create_app
from engine.analysis_core.io.statestore import StateStore
from engine.runner import run_analysis


def test_collectdata_triggers_and_links(seeded, monkeypatch):
    store, cid = seeded

    created = {}

    def fake_trigger(cluster_id, scope, resources, window, **kw):
        # Simulate the collector creating a terminal collection_runs row.
        col_id = store._insert_id(
            "INSERT INTO collection_runs (cluster_id, status, rows_written) VALUES (?, 'success', 42)",
            (cluster_id,))
        store.commit()
        created["id"] = col_id
        return {"collection_id": col_id, "status": "running"}

    # _maybe_collect imports trigger_collection from engine.collector at call time.
    monkeypatch.setattr("engine.collector.trigger_collection", fake_trigger)

    result = run_analysis(store, cluster=cid, collect_data=True)
    assert result.status == "completed"

    run = store.get_run(result.run_id)
    assert run["collection_run_id"] == created["id"]  # the analysis is linked to its collection


def test_post_collections_endpoint(seeded, db_path, monkeypatch):
    # app.py binds trigger_collection at import, so patch it on the app module.
    monkeypatch.setattr("engine.api.app.trigger_collection",
                        lambda cluster_id, scope, resources, window, **kw: {"collection_id": 7, "status": "running"})
    client = TestClient(create_app(get_store=lambda: StateStore("sqlite", db_path)))
    resp = client.post("/api/v1/collections", json={"cluster": "demo", "scope": "all"})
    assert resp.status_code == 200
    assert resp.json() == {"collection_id": "7", "status": "running"}


def test_post_collections_503_when_collector_down(seeded, db_path, monkeypatch):
    from engine.collector import CollectorUnavailable

    def boom(*a, **k):
        raise CollectorUnavailable("connection refused")

    monkeypatch.setattr("engine.api.app.trigger_collection", boom)
    client = TestClient(create_app(get_store=lambda: StateStore("sqlite", db_path)))
    resp = client.post("/api/v1/collections", json={"cluster": "demo", "scope": "all"})
    assert resp.status_code == 503
