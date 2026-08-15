"""API contract: POST /runs then GET recommendations (cards DTO) + lazy evidence."""
from fastapi.testclient import TestClient

from engine.api.app import create_app
from engine.analysis_core.io.statestore import StateStore


def _client(db_path):
    app = create_app(get_store=lambda: StateStore("sqlite", db_path))
    return TestClient(app)


def test_run_and_fetch_cards(seeded, db_path):
    client = _client(db_path)

    resp = client.post("/api/v1/runs", json={"cluster": "demo", "scope": "all", "collectData": False})
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "completed"
    run_id = run["run_id"]

    # GET /runs/{id}
    status = client.get(f"/api/v1/runs/{run_id}").json()
    assert status["status"] == "completed"
    assert status["stale"] is False

    # GET /runs/{id}/recommendations — cards DTO
    cards = client.get(f"/api/v1/runs/{run_id}/recommendations").json()
    assert cards["run"]["name"] == run["name"]
    assert len(cards["recommendations"]) == 1
    card = cards["recommendations"][0]
    assert set(card.keys()) >= {"id", "workload", "from", "to_target", "cadence",
                                "run_time", "duration", "savings", "confidence", "summary"}
    assert card["workload"]["name"] == "vmw-costing1"
    assert card["savings"]["amount"] > 0
    rec_id = card["id"]

    # Evidence (lazy) — with series
    ev = client.get(f"/api/v1/runs/{run_id}/recommendations/{rec_id}/evidence").json()
    assert ev["recommendation_id"] == rec_id
    assert ev["metrics"]["period_hours"] is not None
    assert len(ev["series"]) == 2
    assert ev["series"][0]["points"]
    assert "overlay" in ev["series"][0]

    # Evidence text-only fallback (?series=false)
    ev2 = client.get(f"/api/v1/runs/{run_id}/recommendations/{rec_id}/evidence?series=false").json()
    assert "series" not in ev2
    assert ev2["metrics"]["confidence"] in ("high", "medium", "low")


def test_collectdata_degrades_without_collector(seeded, db_path, monkeypatch):
    # collectData:true with no reachable collector -> run still completes on stored data.
    monkeypatch.setenv("KUBESIESTA_COLLECTOR_URL", "http://127.0.0.1:59999")
    client = _client(db_path)
    resp = client.post("/api/v1/runs", json={"cluster": "demo", "collectData": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
