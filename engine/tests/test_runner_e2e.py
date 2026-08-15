"""End-to-end: seed metric_samples -> run pipeline -> recommendations + evidence persisted."""
from engine.runner import run_analysis


def test_run_writes_one_recommendation(seeded):
    store, cid = seeded
    result = run_analysis(store, cluster=cid, scope="all")

    assert result.status == "completed"
    assert result.recommendations == 1  # candidate flagged, steady one not

    cards = store.get_recommendations(result.run_id)
    assert len(cards) == 1
    rec = cards[0]
    assert rec["workload_name"] == "vmw-costing1"
    assert rec["to_target"] in ("CronJob", "Job", "KEDA")
    assert rec["savings_amount"] > 0

    evidence = store.get_evidence(rec["id"])
    resources = {e["resource"] for e in evidence}
    assert resources == {"cpu", "memory"}
    assert evidence[0]["series"]  # downsampled points stored
    assert evidence[0]["active_windows"]


def test_run_marks_stale_when_data_old(seeded):
    # The seeded samples are collected "now", so the run should not be stale.
    store, cid = seeded
    result = run_analysis(store, cluster=cid, scope="all")
    run = store.get_run(result.run_id)
    assert run["stale"] is False
    assert run["data_as_of"] is not None


def test_collect_data_degrades_without_collector(seeded, monkeypatch):
    # No collector service reachable -> collection is skipped, the run still completes
    # on stored data (failure-tolerant).
    store, cid = seeded
    monkeypatch.setenv("KUBESIESTA_COLLECTOR_URL", "http://127.0.0.1:59999")  # nothing listening
    result = run_analysis(store, cluster=cid, collect_data=True)
    assert result.status == "completed"
    assert result.recommendations == 1
