"""StateStore — the engine's DB access. Reads tiers 2–3, writes tier 4.

Backed by SQLite (dev) or Postgres (prod). Dialect differences (placeholders, JSON,
booleans, timestamps, insert-id) are isolated in small helpers. The canonical schema
lives with the collector; `apply_schema()` here is a dev/test convenience that runs
those same SQLite migrations, so the cross-module contract cannot drift.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class StateStore:
    def __init__(self, driver: str = "sqlite", dsn: str = "./kubesiesta.db"):
        self.dialect, self.conn = _connect(driver, dsn)

    def close(self) -> None:
        self.conn.close()

    # --- dialect helpers ---------------------------------------------------

    def _q(self, sql: str) -> str:
        return sql if self.dialect == "sqlite" else sql.replace("?", "%s")

    def _exec(self, sql: str, params: tuple = ()):  # returns a cursor
        cur = self.conn.cursor()
        cur.execute(self._q(sql), params)
        return cur

    def _fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = self._exec(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[dict]:
        cur = self._exec(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None

    def _insert_id(self, sql: str, params: tuple) -> int:
        """INSERT (without RETURNING) and return the new row id, cross-dialect."""
        if self.dialect == "sqlite":
            cur = self._exec(sql, params)
            return int(cur.lastrowid)
        cur = self._exec(sql + " RETURNING id", params)
        return int(cur.fetchone()["id"])

    def _bool(self, b: bool) -> Any:
        return (1 if b else 0) if self.dialect == "sqlite" else bool(b)

    def _json(self, obj: Any) -> Any:
        if obj is None:
            return None
        if self.dialect == "sqlite":
            return json.dumps(obj)
        from psycopg.types.json import Json  # lazy: only when Postgres is used
        return Json(obj)

    def _json_load(self, v: Any) -> Any:
        # SQLite stores JSON as TEXT, so parse it; Postgres JSONB is already decoded
        # to Python objects by psycopg (including JSON string scalars like "all").
        if v is None or v == "":
            return None
        if self.dialect == "sqlite" and isinstance(v, str):
            return json.loads(v)
        return v

    def commit(self) -> None:
        self.conn.commit()

    # --- schema (dev/test only) -------------------------------------------

    def apply_schema(self) -> None:
        """Create the schema in a fresh SQLite DB from the canonical migrations."""
        if self.dialect != "sqlite":
            raise RuntimeError("apply_schema is a SQLite dev/test helper; run `collector db migrate` for Postgres")
        if self._table_exists("metric_samples"):
            return
        self.conn.executescript(_sqlite_schema_sql())
        self.conn.commit()

    def _table_exists(self, name: str) -> bool:
        row = self._fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return row is not None

    # --- tier 1 config -----------------------------------------------------

    def ensure_cluster(self, name: str) -> int:
        self._exec(
            "INSERT INTO clusters (name, created_at, status) VALUES (?, ?, 'unknown') ON CONFLICT (name) DO NOTHING",
            (name, _iso(_now())),
        )
        self.commit()
        row = self._fetchone("SELECT id FROM clusters WHERE name = ?", (name,))
        return int(row["id"])

    def get_settings(self) -> Optional[dict]:
        row = self._fetchone("SELECT * FROM settings WHERE id = 1")
        if row:
            row["thresholds"] = self._json_load(row.get("thresholds"))
        return row

    # --- tier 2/3 seeding (collector normally writes these; used by tests/synth) ---

    def upsert_workload(
        self, cluster_id: int, workload_uid: str, namespace: str, kind: str, name: str,
        replicas: Optional[int] = None, requests_cpu_m: Optional[int] = None,
        requests_mem_bytes: Optional[int] = None, fetched_at: Optional[datetime] = None,
    ) -> None:
        self._exec(
            """INSERT INTO disc_workloads
               (cluster_id, workload_uid, namespace, kind, name, replicas, requests_cpu_m, requests_mem_bytes, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (cluster_id, workload_uid) DO UPDATE SET
                 namespace=excluded.namespace, kind=excluded.kind, name=excluded.name,
                 replicas=excluded.replicas, requests_cpu_m=excluded.requests_cpu_m,
                 requests_mem_bytes=excluded.requests_mem_bytes, fetched_at=excluded.fetched_at""",
            (cluster_id, workload_uid, namespace, kind, name, replicas, requests_cpu_m,
             requests_mem_bytes, _iso(fetched_at or _now())),
        )
        self.commit()

    def insert_metric_samples(self, rows: list[dict]) -> int:
        sql = self._q(
            """INSERT INTO metric_samples
               (cluster_id, workload_uid, resource, ts, value, unit, is_rate, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (cluster_id, workload_uid, resource, ts) DO UPDATE SET
                 value=excluded.value, unit=excluded.unit, is_rate=excluded.is_rate, collected_at=excluded.collected_at"""
        )
        cur = self.conn.cursor()
        params = [
            (r["cluster_id"], r["workload_uid"], r["resource"], _iso(r["ts"]) if isinstance(r["ts"], datetime) else r["ts"],
             float(r["value"]), r.get("unit"), self._bool(bool(r.get("is_rate", False))),
             _iso(r.get("collected_at") or _now()))
            for r in rows
        ]
        cur.executemany(sql, params)
        self.commit()
        return len(rows)

    def insert_interactions(self, rows: list[dict]) -> int:
        """Seed dependency edges (tier 3, interactions). The peer-expansion stage that
        reads these lands in a later milestone; this lets synth fixtures seed them now."""
        sql = self._q(
            """INSERT INTO interactions
               (cluster_id, src_workload_uid, dst_workload_uid, avg_count, window_start, window_end, collected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (cluster_id, src_workload_uid, dst_workload_uid, window_start) DO UPDATE SET
                 avg_count=excluded.avg_count, collected_at=excluded.collected_at"""
        )
        cur = self.conn.cursor()
        now = _iso(_now())
        params = [
            (r["cluster_id"], r["src_workload_uid"], r["dst_workload_uid"], r.get("avg_count"),
             _iso(r["window_start"]) if r.get("window_start") else None,
             _iso(r["window_end"]) if r.get("window_end") else None, now)
            for r in rows
        ]
        cur.executemany(sql, params)
        self.commit()
        return len(rows)

    # --- tier 2/3 reads ----------------------------------------------------

    def list_workload_uids(self, cluster_id: int, scope: Any = None) -> list[str]:
        if isinstance(scope, dict) and scope.get("workload_uids"):
            uids = scope["workload_uids"]
            marks = ",".join("?" for _ in uids)
            rows = self._fetchall(
                f"SELECT DISTINCT workload_uid FROM metric_samples WHERE cluster_id = ? AND workload_uid IN ({marks}) ORDER BY workload_uid",
                (cluster_id, *uids),
            )
        else:
            rows = self._fetchall(
                "SELECT DISTINCT workload_uid FROM metric_samples WHERE cluster_id = ? ORDER BY workload_uid",
                (cluster_id,),
            )
        return [r["workload_uid"] for r in rows]

    def load_series(self, cluster_id: int, workload_uid: str, resource: str) -> list[tuple]:
        rows = self._fetchall(
            "SELECT ts, value FROM metric_samples WHERE cluster_id = ? AND workload_uid = ? AND resource = ? ORDER BY ts",
            (cluster_id, workload_uid, resource),
        )
        return [(r["ts"], r["value"]) for r in rows]

    def get_identity(self, cluster_id: int, workload_uid: str) -> Optional[dict]:
        return self._fetchone(
            "SELECT namespace, kind, name, replicas, requests_cpu_m, requests_mem_bytes FROM disc_workloads WHERE cluster_id = ? AND workload_uid = ?",
            (cluster_id, workload_uid),
        )

    def max_collected_at(self, cluster_id: int) -> Optional[datetime]:
        row = self._fetchone("SELECT max(collected_at) AS m FROM metric_samples WHERE cluster_id = ?", (cluster_id,))
        return _parse_dt(row["m"]) if row else None

    def get_outgoing_interactions(self, cluster_id: int, src_uid: str) -> list[dict]:
        """Dependency edges FROM this workload — candidate peers for expansion."""
        return self._fetchall(
            "SELECT dst_workload_uid, avg_count FROM interactions WHERE cluster_id = ? AND src_workload_uid = ? ORDER BY avg_count DESC",
            (cluster_id, src_uid),
        )

    def get_incoming_interactions(self, cluster_id: int, dst_uid: str) -> list[dict]:
        """Dependency edges TO this workload — used to detect ad-hoc inbound traffic."""
        return self._fetchall(
            "SELECT src_workload_uid, avg_count FROM interactions WHERE cluster_id = ? AND dst_workload_uid = ? ORDER BY avg_count DESC",
            (cluster_id, dst_uid),
        )

    # --- tier 4 writes -----------------------------------------------------

    def create_analysis_run(
        self, name: str, cluster_id: int, scope: Any, config: dict,
        data_as_of: Optional[datetime], stale: bool, ttl_hours: int,
        collection_run_id: Optional[int] = None,
        run_type: str = "job",
    ) -> int:
        """Insert a new analysis_run row.

        `run_type` is the polymorphic discriminator ('job' | 'maintenance').
        Defaults to 'job' so pre-M2 callers keep working unchanged.
        """
        created = _now()
        expires = created + timedelta(hours=ttl_hours)
        return self._insert_id(
            """INSERT INTO analysis_runs
               (name, cluster_id, run_type, scope, config, collection_run_id, data_as_of, stale, status, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)""",
            (name, cluster_id, run_type, self._json(scope), self._json(config), collection_run_id, _iso(data_as_of),
             self._bool(stale), _iso(created), _iso(expires)),
        )

    def finish_analysis_run(self, run_id: int, status: str, error: Optional[str] = None) -> None:
        self._exec(
            "UPDATE analysis_runs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
            (status, _iso(_now()), error, run_id),
        )
        self.commit()

    def insert_recommendation(self, run_id: int, rec) -> int:
        rec_id = self._insert_id(
            """INSERT INTO recommendations
               (run_id, workload_kind, workload_name, namespace, from_type, to_target, cadence, run_time, duration,
                savings_amount, savings_currency, savings_period, confidence, summary_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id, rec.workload_kind, rec.workload_name, rec.namespace, rec.from_type, rec.to_target,
             rec.cadence, rec.run_time, rec.duration, rec.savings_amount, rec.savings_currency,
             rec.savings_period, rec.confidence, rec.summary_text),
        )
        for ev in rec.evidence:
            self._insert_evidence(rec_id, ev)
        for peer in rec.peers:
            self._insert_peer(rec_id, peer)
        self.commit()
        return rec_id

    def _insert_evidence(self, rec_id: int, ev) -> None:
        windows = [{"start": _iso(w.start), "end": _iso(w.end)} for w in ev.active_windows]
        self._exec(
            """INSERT INTO recommendation_evidence
               (recommendation_id, resource, jump_pct, active_idle_ratio, period_hours, active_duration_min,
                overlap_pct, trend_value, eps_min, eps_max, active_windows, series)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rec_id, ev.resource, ev.jump_pct, ev.active_idle_ratio, ev.period_hours, ev.active_duration_min,
             ev.overlap_pct, ev.trend_value, ev.eps_min, ev.eps_max, self._json(windows), self._json(ev.series)),
        )

    def _insert_peer(self, rec_id: int, peer) -> None:
        self._exec(
            """INSERT INTO recommendation_peers
               (recommendation_id, peer_workload, shared_seasonality, savings_amount, to_target, note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rec_id, peer.workload, self._bool(peer.shared_seasonality), peer.savings_amount, peer.to_target, peer.note),
        )

    # --- tier 4 writes: maintenance (M2 schema; head lands in M3) ----------

    def insert_maintenance_result(self, run_id: int, result: dict) -> int:
        """Persist a maintenance result + its impacted apps + evidence.

        The `result` dict is a shape the recommenders/maintenance head will
        assemble in M3. Kept dict-typed here so M2 can land the schema and CRUD
        without dragging in the head's dataclasses.

        Expected keys (all optional except maintenance_for_uid):
            maintenance_for_uid, workload_kind, workload_name, namespace,
            recommended_start (datetime|None), recommended_end (datetime|None),
            duration_min (float), deadline (datetime|None),
            impact_score (float), confidence ('high'|'medium'|'low'), summary_text,
            impacted_apps: [ {workload_uid, workload_kind, workload_name, namespace,
                              period_hours, active_fraction, impact_score, note} ],
            evidence: [ {workload_uid, resource, forecast_series (list), active_windows (list)} ]
        """
        result_id = self._insert_id(
            """INSERT INTO maintenance_results
               (run_id, maintenance_for_uid, workload_kind, workload_name, namespace,
                recommended_start, recommended_end, duration_min, deadline,
                impact_score, confidence, summary_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (run_id,
             result["maintenance_for_uid"],
             result.get("workload_kind"), result.get("workload_name"), result.get("namespace"),
             _iso(result.get("recommended_start")), _iso(result.get("recommended_end")),
             result.get("duration_min"), _iso(result.get("deadline")),
             result.get("impact_score"), result.get("confidence"), result.get("summary_text")),
        )
        for app in result.get("impacted_apps") or []:
            self._insert_maintenance_impacted_app(result_id, app)
        for ev in result.get("evidence") or []:
            self._insert_maintenance_evidence(result_id, ev)
        self.commit()
        return result_id

    def _insert_maintenance_impacted_app(self, result_id: int, app: dict) -> None:
        self._exec(
            """INSERT INTO maintenance_impacted_apps
               (maintenance_result_id, workload_uid, workload_kind, workload_name, namespace,
                period_hours, active_fraction, impact_score, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result_id,
             app["workload_uid"], app.get("workload_kind"), app.get("workload_name"), app.get("namespace"),
             app.get("period_hours"), app.get("active_fraction"), app.get("impact_score"), app.get("note")),
        )

    def _insert_maintenance_evidence(self, result_id: int, ev: dict) -> None:
        self._exec(
            """INSERT INTO maintenance_evidence
               (maintenance_result_id, workload_uid, resource, forecast_series, active_windows)
               VALUES (?, ?, ?, ?, ?)""",
            (result_id, ev["workload_uid"], ev["resource"],
             self._json(ev.get("forecast_series")), self._json(ev.get("active_windows"))),
        )

    # --- tier 4 reads (API) ------------------------------------------------

    def get_run(self, run_id: int) -> Optional[dict]:
        row = self._fetchone(
            """SELECT r.*, c.name AS cluster_name FROM analysis_runs r
               LEFT JOIN clusters c ON c.id = r.cluster_id WHERE r.id = ?""",
            (run_id,),
        )
        if not row:
            return None
        row["stale"] = bool(row.get("stale"))
        for k in ("scope", "config"):
            row[k] = self._json_load(row.get(k))
        return row

    def get_recommendations(self, run_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM recommendations WHERE run_id = ? ORDER BY id",
            (run_id,),
        )

    def get_recommendation(self, rec_id: int) -> Optional[dict]:
        return self._fetchone("SELECT * FROM recommendations WHERE id = ?", (rec_id,))

    def get_evidence(self, rec_id: int) -> list[dict]:
        rows = self._fetchall("SELECT * FROM recommendation_evidence WHERE recommendation_id = ? ORDER BY id", (rec_id,))
        for r in rows:
            for k in ("active_windows", "series"):
                r[k] = self._json_load(r.get(k))
        return rows

    def get_peers(self, rec_id: int) -> list[dict]:
        rows = self._fetchall("SELECT * FROM recommendation_peers WHERE recommendation_id = ? ORDER BY id", (rec_id,))
        for r in rows:
            r["shared_seasonality"] = bool(r.get("shared_seasonality"))
        return rows

    # --- tier 4 reads: maintenance (M2 schema; DTOs land in M4) ------------

    def get_maintenance_results(self, run_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM maintenance_results WHERE run_id = ? ORDER BY id",
            (run_id,),
        )

    def get_maintenance_result(self, result_id: int) -> Optional[dict]:
        return self._fetchone("SELECT * FROM maintenance_results WHERE id = ?", (result_id,))

    def get_maintenance_impacted_apps(self, result_id: int) -> list[dict]:
        return self._fetchall(
            "SELECT * FROM maintenance_impacted_apps WHERE maintenance_result_id = ? ORDER BY id",
            (result_id,),
        )

    def get_maintenance_evidence(self, result_id: int) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM maintenance_evidence WHERE maintenance_result_id = ? ORDER BY id",
            (result_id,),
        )
        for r in rows:
            for k in ("forecast_series", "active_windows"):
                r[k] = self._json_load(r.get(k))
        return rows

    def list_runs(self, cluster_id: Optional[int] = None, limit: int = 50) -> list[dict]:
        cols = "id, name, cluster_id, run_type, status, data_as_of, stale, created_at, completed_at"
        if cluster_id is None:
            rows = self._fetchall(f"SELECT {cols} FROM analysis_runs ORDER BY id DESC LIMIT ?", (limit,))
        else:
            rows = self._fetchall(
                f"SELECT {cols} FROM analysis_runs WHERE cluster_id = ? ORDER BY id DESC LIMIT ?", (cluster_id, limit))
        for r in rows:
            r["stale"] = bool(r.get("stale"))
        return rows

    # --- tier 1 config: clusters (API CRUD) --------------------------------

    def create_cluster(self, name: str, api_url=None, auth_method=None, credential_ref=None, ca_cert=None) -> dict:
        cid = self._insert_id(
            """INSERT INTO clusters (name, api_url, auth_method, credential_ref, ca_cert, created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, 'unknown')""",
            (name, api_url, auth_method, credential_ref, ca_cert, _iso(_now())),
        )
        self.commit()
        return self.get_cluster(cid)

    def list_clusters(self) -> list[dict]:
        return self._fetchall("SELECT * FROM clusters ORDER BY id")

    def get_cluster(self, cluster_id: int) -> Optional[dict]:
        return self._fetchone("SELECT * FROM clusters WHERE id = ?", (cluster_id,))

    def delete_cluster(self, cluster_id: int) -> bool:
        cur = self._exec("DELETE FROM clusters WHERE id = ?", (cluster_id,))
        self.commit()
        return cur.rowcount > 0

    def update_cluster_status(self, cluster_id: int, status: str, touch: bool = False) -> None:
        """Persist a probe outcome. `touch=True` bumps last_connected_at (call only
        when the cluster was actually reachable)."""
        if touch:
            self._exec("UPDATE clusters SET status = ?, last_connected_at = ? WHERE id = ?",
                       (status, _iso(_now()), cluster_id))
        else:
            self._exec("UPDATE clusters SET status = ? WHERE id = ?", (status, cluster_id))
        self.commit()

    def update_cluster_status(self, cluster_id: int, status: str, touch: bool = False) -> None:
        if touch:
            self._exec("UPDATE clusters SET status = ?, last_connected_at = ? WHERE id = ?",
                       (status, _iso(_now()), cluster_id))
        else:
            self._exec("UPDATE clusters SET status = ? WHERE id = ?", (status, cluster_id))
        self.commit()

    # --- tier 1 config: data sources (API CRUD) ----------------------------

    def create_data_source(self, cluster_id, type_, name, endpoint=None, auth_config=None, settings=None, enabled=True) -> dict:
        sid = self._insert_id(
            """INSERT INTO data_sources (cluster_id, type, name, endpoint, auth_config, settings, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (cluster_id, type_, name, endpoint, self._json(auth_config), self._json(settings),
             self._bool(enabled), _iso(_now())),
        )
        self.commit()
        return self.get_data_source(sid)

    def list_data_sources(self, cluster_id: Optional[int] = None) -> list[dict]:
        if cluster_id is None:
            rows = self._fetchall("SELECT * FROM data_sources ORDER BY id")
        else:
            rows = self._fetchall(
                "SELECT * FROM data_sources WHERE cluster_id = ? OR cluster_id IS NULL ORDER BY id", (cluster_id,))
        return [self._decode_source(r) for r in rows]

    def get_data_source(self, source_id: int) -> Optional[dict]:
        row = self._fetchone("SELECT * FROM data_sources WHERE id = ?", (source_id,))
        return self._decode_source(row) if row else None

    def update_data_source(self, source_id: int, **fields) -> Optional[dict]:
        sets, params = [], []
        for k in ("name", "endpoint", "type", "health"):
            if fields.get(k) is not None:
                sets.append(f"{k} = ?")
                params.append(fields[k])
        if fields.get("enabled") is not None:
            sets.append("enabled = ?")
            params.append(self._bool(fields["enabled"]))
        for k in ("auth_config", "settings"):
            if fields.get(k) is not None:
                sets.append(f"{k} = ?")
                params.append(self._json(fields[k]))
        if sets:
            params.append(source_id)
            self._exec(f"UPDATE data_sources SET {', '.join(sets)} WHERE id = ?", tuple(params))
            self.commit()
        return self.get_data_source(source_id)

    def delete_data_source(self, source_id: int) -> bool:
        cur = self._exec("DELETE FROM data_sources WHERE id = ?", (source_id,))
        self.commit()
        return cur.rowcount > 0

    def set_source_health(self, source_id: int, health: str) -> None:
        self._exec("UPDATE data_sources SET health = ?, last_checked_at = ? WHERE id = ?",
                   (health, _iso(_now()), source_id))
        self.commit()

    def _decode_source(self, row: dict) -> dict:
        for k in ("auth_config", "settings"):
            row[k] = self._json_load(row.get(k))
        row["enabled"] = bool(row.get("enabled"))
        return row

    # --- settings (API) ----------------------------------------------------

    def update_settings(self, **fields) -> dict:
        sets, params = [], []
        for k in ("metric_ttl_hours", "discovery_ttl_min", "result_ttl_hours", "default_resources", "default_window"):
            if fields.get(k) is not None:
                sets.append(f"{k} = ?")
                params.append(fields[k])
        if fields.get("thresholds") is not None:
            sets.append("thresholds = ?")
            params.append(self._json(fields["thresholds"]))
        if sets:
            self._exec(f"UPDATE settings SET {', '.join(sets)} WHERE id = 1", tuple(params))
            self.commit()
        return self.get_settings()

    # --- discovery cache reads (API) ---------------------------------------

    def list_namespaces(self, cluster_id: int) -> list[dict]:
        # Derived from the workloads we have metrics/identity for (discovery cache).
        return self._fetchall(
            "SELECT DISTINCT namespace AS name FROM disc_workloads WHERE cluster_id = ? ORDER BY namespace", (cluster_id,))

    def list_workloads(self, cluster_id: int, namespace: Optional[str] = None) -> list[dict]:
        if namespace:
            return self._fetchall(
                "SELECT * FROM disc_workloads WHERE cluster_id = ? AND namespace = ? ORDER BY name", (cluster_id, namespace))
        return self._fetchall(
            "SELECT * FROM disc_workloads WHERE cluster_id = ? ORDER BY namespace, name", (cluster_id,))

    # --- collection runs read (API) ----------------------------------------

    def get_collection_run(self, collection_id: int) -> Optional[dict]:
        row = self._fetchone("SELECT * FROM collection_runs WHERE id = ?", (collection_id,))
        if row:
            for k in ("scope", "resources", "sources_used"):
                row[k] = self._json_load(row.get(k))
        return row


# --- connection + schema resolution ---------------------------------------

def _connect(driver: str, dsn: str):
    d = (driver or "sqlite").lower()
    if d in ("sqlite", "sqlite3", ""):
        conn = sqlite3.connect(dsn, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")  # engine + collector may share the file
        return "sqlite", conn
    if d in ("postgres", "postgresql", "pgx"):
        import psycopg
        from psycopg.rows import dict_row
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        return "postgres", conn
    raise ValueError(f"unsupported db driver {driver!r} (use sqlite|postgres)")


def _sqlite_schema_sql() -> str:
    """Locate and read the canonical SQLite migrations shipped with the collector."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "collector" / "internal" / "store" / "migrations" / "sqlite"
        if cand.is_dir():
            return "\n".join(p.read_text() for p in sorted(cand.glob("*.sql")))
    raise FileNotFoundError(
        "canonical SQLite migrations not found (collector/internal/store/migrations/sqlite); "
        "run `collector db migrate` to create the schema"
    )
