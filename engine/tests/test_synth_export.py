"""CSV / JSON export + load round-trips, and the `engine synth` CLI."""
import os

import numpy as np

from engine.cli import main
from engine.synth import export_csv, export_json, load_csv, load_json, metric_rows, synthetic_cluster


def test_json_roundtrip(tmp_path):
    c = synthetic_cluster()
    p = export_json(c, str(tmp_path / "cluster.json"))
    c2 = load_json(p)

    assert c2.candidate_uids == c.candidate_uids
    assert {w.uid for w in c2.workloads} == {w.uid for w in c.workloads}
    uid = sorted(c.candidate_uids)[0]
    a = np.array([v for _, v in c.by_uid(uid).resources["cpu"]])
    b = np.array([v for _, v in c2.by_uid(uid).resources["cpu"]])
    assert np.allclose(a, b)


def test_csv_roundtrip(tmp_path):
    c = synthetic_cluster()
    export_csv(c, str(tmp_path))
    c2 = load_csv(str(tmp_path))
    assert c2.candidate_uids == c.candidate_uids
    assert len(metric_rows(c2)) == len(metric_rows(c))


def test_metric_rows_shape():
    c = synthetic_cluster()
    rows = metric_rows(c)
    assert len(rows) == 5 * 2 * 336  # 5 workloads * 2 resources * 336 hours
    assert set(rows[0]) >= {"cluster", "workload_uid", "resource", "ts", "value", "unit", "is_rate"}
    assert all(r["is_rate"] is True for r in rows if r["resource"] == "cpu")
    assert all(r["is_rate"] is False for r in rows if r["resource"] == "memory")


def test_cli_synth_writes_json(tmp_path):
    out = str(tmp_path / "c.json")
    assert main(["synth", "--format", "json", "--out", out]) == 0
    assert os.path.exists(out)


def test_cli_synth_seeds_db(tmp_path):
    db = str(tmp_path / "seeded.db")
    out_dir = str(tmp_path / "csv")
    rc = main(["synth", "--format", "csv", "--out", out_dir, "--seed-db", "--db-dsn", db])
    assert rc == 0
    assert os.path.exists(os.path.join(out_dir, "metrics.csv"))
    assert os.path.exists(db)
