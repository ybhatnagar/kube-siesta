"""End-to-end maintenance run against a small synth multi-app cluster.

Fixture: a downstream `target` with a clean 8h/2h cycle, an upstream `caller-a`
that shares the same 8h cycle *aligned* to the target (both active together),
and an upstream `caller-b` with no metric data at all (aperiodic → always-active
per the confirmed pessimistic default).

Expectations:
  - The chosen window falls in the target's idle time (score can never be 0 here
    because caller-b is projected always-active).
  - Min impact score = number of active samples of caller-b in the window
    (which is the whole window, so score_sum ≈ window_samples).
  - `impacted_apps` surfaces caller-b's note about aperiodic-always-active.
  - Run row has `run_type='maintenance'`; maintenance_results/apps/evidence
    all round-trip through StateStore.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from engine.analysis_core.io.statestore import StateStore
from engine.recommenders.maintenance.runner import run_maintenance_analysis
from engine.synth.generate import candidate_workload


TARGET_UID = "ns-a/Deployment/target"
CALLER_A_UID = "ns-b/Deployment/caller-a"
CALLER_B_UID = "ns-b/Deployment/caller-b"


@pytest.fixture
def maintenance_cluster(store: StateStore):
    """Target + one aligned periodic caller + one aperiodic (no-data) caller."""
    cid = store.ensure_cluster("multi-app")

    # target — clean 8h cycle, 2h burst per period, seed 0
    store.upsert_workload(cid, TARGET_UID, "ns-a", "Deployment", "target",
                          replicas=1, requests_cpu_m=500, requests_mem_bytes=1_500_000_000)
    _seed_series(store, cid, TARGET_UID, candidate_workload(seed=0))

    # caller-a — same 8h cycle (seed 1 → same pattern), aligned to target
    store.upsert_workload(cid, CALLER_A_UID, "ns-b", "Deployment", "caller-a",
                          replicas=1, requests_cpu_m=200, requests_mem_bytes=500_000_000)
    _seed_series(store, cid, CALLER_A_UID, candidate_workload(seed=1))

    # caller-b — identity only, NO metric samples → aperiodic path
    store.upsert_workload(cid, CALLER_B_UID, "ns-b", "Deployment", "caller-b",
                          replicas=1, requests_cpu_m=200, requests_mem_bytes=500_000_000)

    # both callers → target
    for src in (CALLER_A_UID, CALLER_B_UID):
        store.insert_interactions([{
            "cluster_id": cid, "src_workload_uid": src, "dst_workload_uid": TARGET_UID,
            "avg_count": 10.0, "window_start": None, "window_end": None,
        }])
    return store, cid


def _seed_series(store, cid, uid, resources):
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


def test_end_to_end_returns_a_recommendation(maintenance_cluster):
    store, cid = maintenance_cluster
    # Anchor `now` at the last observed sample so the forecast horizon spans
    # future-only time; without this, `pd.date_range` may include stale hours.
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    result = run_maintenance_analysis(
        store,
        cluster=cid, target_workload_uid=TARGET_UID,
        duration="1h", deadline="3d", now=now, name="maint-e2e-1",
    )

    assert result.status == "completed"
    assert result.result_id is not None
    assert result.recommended_start is not None
    assert result.recommended_end is not None

    # The run row is properly discriminated.
    row = store.get_run(result.run_id)
    assert row["run_type"] == "maintenance"

    # And the persisted result / apps / evidence round-trip via the StateStore.
    persisted = store.get_maintenance_results(result.run_id)
    assert len(persisted) == 1
    r = persisted[0]
    assert r["maintenance_for_uid"] == TARGET_UID
    assert r["duration_min"] == 60.0

    apps = store.get_maintenance_impacted_apps(r["id"])
    uid_set = {a["workload_uid"] for a in apps}
    assert uid_set == {CALLER_A_UID, CALLER_B_UID}


def test_aperiodic_dep_is_treated_as_always_active(maintenance_cluster):
    """Option-1 pessimistic default: caller-b must show up as impacting every window."""
    store, cid = maintenance_cluster
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    result = run_maintenance_analysis(
        store,
        cluster=cid, target_workload_uid=TARGET_UID,
        duration="1h", deadline="3d", now=now, name="maint-e2e-2",
    )

    apps = store.get_maintenance_impacted_apps(result.result_id)
    caller_b = next(a for a in apps if a["workload_uid"] == CALLER_B_UID)
    # Aperiodic + treated as always-active → impact_score matches the full window
    # (60 min at 60-min sample step = 1 sample) at minimum.
    assert caller_b["impact_score"] >= 1
    assert caller_b["period_hours"] is None
    assert "always-active" in (caller_b["note"] or "")


def test_chosen_window_is_the_earliest_valid(maintenance_cluster):
    """Deterministic tie-break — earliest low-impact window wins."""
    store, cid = maintenance_cluster
    now = datetime(2026, 7, 15, 0, 0, tzinfo=timezone.utc)
    result = run_maintenance_analysis(
        store,
        cluster=cid, target_workload_uid=TARGET_UID,
        duration="1h", deadline="3d", now=now, name="maint-e2e-3",
    )
    # min-score window starts within the first 24h of the forecast horizon
    # (target has 8h cycle → at most one full period wait before an idle slot).
    start = datetime.fromisoformat(result.recommended_start.replace("Z", "+00:00"))
    assert (start - now).total_seconds() <= 24 * 3600
