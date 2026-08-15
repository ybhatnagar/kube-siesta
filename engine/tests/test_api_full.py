"""The rest of the /api/v1 surface: clusters, data sources, settings, discovery,
collections, and runs history."""
from fastapi.testclient import TestClient

from engine.api.app import create_app
from engine.analysis_core.io.statestore import StateStore
from engine.runner import run_analysis


def _client(db_path):
    return TestClient(create_app(get_store=lambda: StateStore("sqlite", db_path)))


def test_cluster_crud(store, db_path):
    client = _client(db_path)

    created = client.post("/api/v1/clusters", json={"name": "prod", "api_url": "https://k8s.example"})
    assert created.status_code == 201, created.text
    cid = created.json()["id"]

    assert client.post("/api/v1/clusters", json={"name": "prod"}).status_code == 409  # duplicate

    assert len(client.get("/api/v1/clusters").json()["clusters"]) == 1
    assert client.get(f"/api/v1/clusters/{cid}").json()["name"] == "prod"
    # Live connectivity probe returns a structured result (unreachable here — the
    # endpoint doesn't resolve and the test isn't running inside a k8s pod).
    tr = client.post(f"/api/v1/clusters/{cid}:test")
    assert tr.status_code == 200 and tr.json()["reachable"] is False
    # test-before-save (no persistence) also returns a structured result
    assert client.post("/api/v1/clusters:test", json={"api_url": "https://127.0.0.1:1"}).json()["reachable"] is False

    assert client.delete(f"/api/v1/clusters/{cid}").json()["deleted"] is True
    assert client.get(f"/api/v1/clusters/{cid}").status_code == 404


def test_data_source_crud_and_test(store, db_path):
    client = _client(db_path)
    cid = client.post("/api/v1/clusters", json={"name": "c1"}).json()["id"]

    made = client.post(f"/api/v1/clusters/{cid}/sources",
                       json={"type": "prometheus", "name": "prom", "endpoint": "http://127.0.0.1:1"})
    assert made.status_code == 201, made.text
    sid = made.json()["id"]

    assert len(client.get(f"/api/v1/clusters/{cid}/sources").json()["sources"]) == 1

    updated = client.put(f"/api/v1/sources/{sid}", json={"enabled": False}).json()
    assert updated["enabled"] is False

    # Probe hits an unreachable endpoint → health reflects it (not a crash).
    assert client.post(f"/api/v1/sources/{sid}:test").json()["health"] == "unreachable"

    assert client.delete(f"/api/v1/sources/{sid}").json()["deleted"] is True
    assert client.put(f"/api/v1/sources/{sid}", json={"name": "x"}).status_code == 404


def test_settings_get_and_put(store, db_path):
    client = _client(db_path)
    assert client.get("/api/v1/settings").json()["result_ttl_hours"] == 24

    put = client.put("/api/v1/settings", json={"metric_ttl_hours": 48, "thresholds": {"jump_min": 75}})
    assert put.status_code == 200
    after = client.get("/api/v1/settings").json()
    assert after["metric_ttl_hours"] == 48
    assert after["thresholds"]["jump_min"] == 75


def test_collections_surface(store, db_path, monkeypatch):
    # The collector trigger is wired; with no collector reachable it surfaces 503.
    # Status of a missing collection -> 404.
    monkeypatch.setenv("KUBESIESTA_COLLECTOR_URL", "http://127.0.0.1:59999")
    client = _client(db_path)
    assert client.post("/api/v1/collections", json={"cluster": "c", "scope": "all"}).status_code == 503
    assert client.get("/api/v1/collections/999").status_code == 404


def test_discovery_and_runs_history(seeded_cluster, db_path):
    store, cid, cluster = seeded_cluster
    run_analysis(store, cluster=cid, scope="all")
    client = _client(db_path)

    namespaces = client.get(f"/api/v1/clusters/{cid}/namespaces").json()["namespaces"]
    assert any(n["name"] == "vmw-costing" for n in namespaces)

    workloads = client.get(f"/api/v1/clusters/{cid}/namespaces/vmw-costing/workloads").json()["workloads"]
    assert any(w["name"] == "vmw-costing1" for w in workloads)
    assert client.get(f"/api/v1/clusters/{cid}/namespaces?refresh=true").status_code == 501

    runs = client.get("/api/v1/runs").json()["runs"]
    assert len(runs) >= 1
    assert runs[0]["status"] == "completed"
