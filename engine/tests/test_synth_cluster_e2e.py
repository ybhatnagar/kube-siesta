"""End-to-end over the synthetic cluster fixture: exactly the known candidates get flagged."""
from engine.runner import run_analysis


def test_cluster_flags_exactly_known_candidates(seeded_cluster):
    store, cid, cluster = seeded_cluster
    result = run_analysis(store, cluster=cid, scope="all")

    recs = store.get_recommendations(result.run_id)
    flagged = {f'{r["namespace"]}/{r["workload_kind"]}/{r["workload_name"]}' for r in recs}

    # costing1, benchmark2, trending-batch are candidates; steady-svc + chatty-svc are not.
    assert flagged == cluster.candidate_uids
    assert result.recommendations == len(cluster.candidate_uids) == 3

    # Every flagged workload has CPU + memory evidence with downsampled series.
    for r in recs:
        ev = store.get_evidence(r["id"])
        assert {e["resource"] for e in ev} == {"cpu", "memory"}
        assert all(e["series"] for e in ev)


def test_interactions_were_seeded(seeded_cluster):
    store, cid, cluster = seeded_cluster
    rows = store._fetchall("SELECT src_workload_uid, dst_workload_uid FROM interactions WHERE cluster_id = ?", (cid,))
    assert len(rows) == len(cluster.interactions)
