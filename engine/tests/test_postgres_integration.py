"""Postgres integration test — the SQLite suite can't exercise the JSONB read paths
(psycopg decodes JSONB to Python objects, so the SQLite `json.loads` habit breaks).

Gated: set KUBESIESTA_TEST_POSTGRES_DSN to a Postgres whose schema is already applied
(`collector db migrate`), e.g. a port-forward to the in-cluster Postgres:

    kubectl -n kubesiesta port-forward svc/<release>-kubesiesta-postgres 5433:5432 &
    KUBESIESTA_TEST_POSTGRES_DSN='postgres://kubesiesta:kubesiesta@127.0.0.1:5433/kubesiesta?sslmode=disable' \
        pytest tests/test_postgres_integration.py -q
"""
import os

import pytest

DSN = os.environ.get("KUBESIESTA_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set KUBESIESTA_TEST_POSTGRES_DSN to run")


def test_full_flow_on_postgres():
    from engine.api import dto
    from engine.analysis_core.io.statestore import StateStore
    from engine.runner import run_analysis
    from engine.synth import seed_cluster, synthetic_cluster

    store = StateStore("postgres", DSN)
    try:
        cid = seed_cluster(store, synthetic_cluster(name="pgtest"))
        result = run_analysis(store, cluster=cid, scope="all")
        assert result.status == "completed"
        assert result.recommendations == 3

        # These read paths go through JSONB columns and would 500 without the
        # dialect-aware decode: run scope/config, settings thresholds, evidence series.
        cards = dto.cards_dto(store, result.run_id)
        assert cards and len(cards["recommendations"]) == 3
        assert isinstance(store.get_settings()["thresholds"], dict)

        rec_id = dto.parse_rec_id(cards["recommendations"][0]["id"])
        ev = dto.evidence_dto(store, result.run_id, rec_id)
        assert ev["series"] and ev["metrics"]["period_hours"]
    finally:
        # Cascades to workloads / metrics / runs / recommendations.
        store._exec("DELETE FROM clusters WHERE name = ?", ("pgtest",))
        store.commit()
        store.close()
