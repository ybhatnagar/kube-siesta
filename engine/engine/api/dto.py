"""DTO assembly for the REST surface. Pure dict builders over store rows."""
from __future__ import annotations

from typing import Optional

from ..analysis_core.io.statestore import StateStore, _iso, _parse_dt

_UNITS = {"cpu": "cores", "memory": "bytes", "net_tx": "bytes/s", "net_rx": "bytes/s", "ephemeral_storage": "bytes"}


def parse_rec_id(rec_id: str) -> int:
    """Accept either "rec_5" (DTO form) or "5"."""
    s = str(rec_id)
    return int(s[4:]) if s.startswith("rec_") else int(s)


def cluster_dto(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "api_url": row.get("api_url"),
        "auth_method": row.get("auth_method"),
        "status": row.get("status"),
        "created_at": _iso(_parse_dt(row.get("created_at"))),
        "last_connected_at": _iso(_parse_dt(row.get("last_connected_at"))),
    }


def data_source_dto(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "cluster_id": row.get("cluster_id"),
        "type": row["type"],
        "name": row["name"],
        "endpoint": row.get("endpoint"),
        "enabled": bool(row.get("enabled")),
        "health": row.get("health"),
        "last_checked_at": _iso(_parse_dt(row.get("last_checked_at"))),
    }


def settings_dto(row: Optional[dict]) -> dict:
    row = row or {}
    return {
        "metric_ttl_hours": row.get("metric_ttl_hours"),
        "discovery_ttl_min": row.get("discovery_ttl_min"),
        "result_ttl_hours": row.get("result_ttl_hours"),
        "default_resources": row.get("default_resources"),
        "default_window": row.get("default_window"),
        "thresholds": row.get("thresholds"),
    }


def run_summary_dto(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "cluster_id": row.get("cluster_id"),
        "run_type": row.get("run_type", "job"),
        "status": row["status"],
        "stale": bool(row.get("stale")),
        "data_as_of": _iso(_parse_dt(row.get("data_as_of"))),
        "created_at": _iso(_parse_dt(row.get("created_at"))),
        "completed_at": _iso(_parse_dt(row.get("completed_at"))),
    }


def collection_dto(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "status": row["status"],
        "progress": 100 if row["status"] in ("success", "failed", "partial") else 0,
        "data_as_of": _iso(_parse_dt(row.get("data_as_of"))),
        "rows_written": row.get("rows_written"),
        "error": row.get("error"),
    }


def workload_dto(row: dict) -> dict:
    return {
        "kind": row["kind"],
        "name": row["name"],
        "namespace": row["namespace"],
        "workload_uid": row.get("workload_uid"),
        "replicas": row.get("replicas"),
        "requests_cpu_m": row.get("requests_cpu_m"),
        "requests_mem_bytes": row.get("requests_mem_bytes"),
    }


def run_status_dto(store: StateStore, run_id: int) -> Optional[dict]:
    run = store.get_run(run_id)
    if not run:
        return None
    return {
        "id": str(run["id"]),
        "run_type": run.get("run_type", "job"),
        "status": run["status"],
        "data_as_of": _iso(_parse_dt(run.get("data_as_of"))),
        "stale": bool(run["stale"]),
        "progress": 100 if run["status"] in ("completed", "failed") else 0,
        "error": run.get("error"),
    }


def cards_dto(store: StateStore, run_id: int) -> Optional[dict]:
    """Dispatch on run_type: job cards vs maintenance cards.

    The URL surface is shared across features — clients call
    `GET /runs/{id}/recommendations` and the shape of the returned card is
    determined by the run's own `run_type`. Old job-only clients keep working
    (job DTO is byte-identical).
    """
    run = store.get_run(run_id)
    if not run:
        return None
    if run.get("run_type") == "maintenance":
        return _maintenance_cards_dto(store, run)
    return _job_cards_dto(store, run)


def _job_cards_dto(store: StateStore, run: dict) -> dict:
    config = run.get("config") or {}
    recs = store.get_recommendations(run["id"])
    return {
        "run": {
            "id": str(run["id"]),
            "name": run["name"],
            "run_type": "job",
            "cluster": run.get("cluster_name"),
            "data_as_of": _iso(_parse_dt(run.get("data_as_of"))),
            "stale": bool(run["stale"]),
            "window": config.get("window"),
        },
        "recommendations": [_card(r) for r in recs],
    }


def _maintenance_cards_dto(store: StateStore, run: dict) -> dict:
    config = run.get("config") or {}
    maint_cfg = config.get("maintenance") or {}
    results = store.get_maintenance_results(run["id"])
    return {
        "run": {
            "id": str(run["id"]),
            "name": run["name"],
            "run_type": "maintenance",
            "cluster": run.get("cluster_name"),
            "data_as_of": _iso(_parse_dt(run.get("data_as_of"))),
            "stale": bool(run["stale"]),
            "duration_minutes": maint_cfg.get("duration_minutes"),
            "deadline": maint_cfg.get("deadline"),
            "target_workload_uid": maint_cfg.get("target_workload_uid"),
        },
        "recommendations": [_maintenance_card(store, r) for r in results],
    }


def _maintenance_card(store: StateStore, r: dict) -> dict:
    apps = store.get_maintenance_impacted_apps(r["id"])
    preview = [
        {"kind": a.get("workload_kind"), "name": a.get("workload_name"), "namespace": a.get("namespace")}
        for a in apps[:8]
    ]
    return {
        "id": f"rec_{r['id']}",
        "workload": {
            "kind": r.get("workload_kind"),
            "name": r.get("workload_name"),
            "namespace": r.get("namespace"),
            "workload_uid": r.get("maintenance_for_uid"),
        },
        "recommended_start": _iso(_parse_dt(r.get("recommended_start"))),
        "recommended_end": _iso(_parse_dt(r.get("recommended_end"))),
        "duration_minutes": r.get("duration_min"),
        "deadline": _iso(_parse_dt(r.get("deadline"))),
        "impact_score": r.get("impact_score"),
        "confidence": r.get("confidence"),
        "summary": r.get("summary_text"),
        "impacted_apps_count": len(apps),
        "impacted_apps_preview": preview,
    }


def _card(r: dict) -> dict:
    return {
        "id": f"rec_{r['id']}",
        "workload": {"kind": r["workload_kind"], "name": r["workload_name"], "namespace": r["namespace"]},
        "from": r["from_type"],
        "to_target": r["to_target"],
        "cadence": r["cadence"],
        "run_time": r["run_time"],
        "duration": r["duration"],
        "savings": {"amount": r["savings_amount"], "currency": r["savings_currency"], "period": r["savings_period"]},
        "confidence": r["confidence"],
        "summary": r["summary_text"],
    }


def evidence_dto(store: StateStore, run_id: int, rec_id: int, include_series: bool = True) -> Optional[dict]:
    """Dispatch on run_type: job evidence vs maintenance evidence.

    The URL is `/runs/{run_id}/recommendations/{rec_id}/evidence`; `rec_id`
    refers to either a `recommendations.id` (job) or a `maintenance_results.id`
    (maintenance) depending on the run.
    """
    run = store.get_run(run_id)
    if not run:
        return None
    if run.get("run_type") == "maintenance":
        return _maintenance_evidence_dto(store, run, rec_id, include_series)
    return _job_evidence_dto(store, rec_id, include_series)


def _job_evidence_dto(store: StateStore, rec_id: int, include_series: bool) -> Optional[dict]:
    rec = store.get_recommendation(rec_id)
    if not rec:
        return None
    evs = store.get_evidence(rec_id)
    peers = store.get_peers(rec_id)
    primary = evs[0] if evs else {}

    dto = {
        "recommendation_id": f"rec_{rec_id}",
        "summary": rec["summary_text"],
        "metrics": {
            "jump_pct": primary.get("jump_pct"),
            "active_idle_ratio": primary.get("active_idle_ratio"),
            "period_hours": primary.get("period_hours"),
            "active_duration_min": primary.get("active_duration_min"),
            "overlap_pct": primary.get("overlap_pct"),
            "confidence": rec["confidence"],
        },
        "peers": [
            {
                "workload": p["peer_workload"],
                "shared_seasonality": p["shared_seasonality"],
                "savings": {"amount": p["savings_amount"], "currency": "USD", "period": "month"},
                "to_target": p["to_target"],
                "note": p["note"],
            }
            for p in peers
        ],
    }
    if include_series:
        dto["series"] = [
            {
                "resource": e["resource"],
                "unit": _UNITS.get(e["resource"], ""),
                "points": e.get("series") or [],
                "overlay": {
                    "trend": e["trend_value"],
                    "eps_min": e["eps_min"],
                    "eps_max": e["eps_max"],
                    "active_windows": e.get("active_windows") or [],
                },
            }
            for e in evs
        ]
    return dto


def _maintenance_evidence_dto(store: StateStore, run: dict, result_id: int, include_series: bool) -> Optional[dict]:
    result = store.get_maintenance_result(result_id)
    if not result or result["run_id"] != run["id"]:
        return None
    apps = store.get_maintenance_impacted_apps(result_id)
    evs = store.get_maintenance_evidence(result_id) if include_series else []

    dto = {
        "recommendation_id": f"rec_{result_id}",
        "summary": result.get("summary_text"),
        "metrics": {
            "impact_score": result.get("impact_score"),
            "confidence": result.get("confidence"),
            "duration_minutes": result.get("duration_min"),
            "recommended_start": _iso(_parse_dt(result.get("recommended_start"))),
            "recommended_end": _iso(_parse_dt(result.get("recommended_end"))),
            "deadline": _iso(_parse_dt(result.get("deadline"))),
        },
        "impacted_apps": [
            {
                "workload": {
                    "kind": a.get("workload_kind"),
                    "name": a.get("workload_name"),
                    "namespace": a.get("namespace"),
                },
                "workload_uid": a["workload_uid"],
                "period_hours": a.get("period_hours"),
                "active_fraction": a.get("active_fraction"),
                "impact_score": a.get("impact_score"),
                "note": a.get("note"),
            }
            for a in apps
        ],
    }
    if include_series:
        dto["series"] = [
            {
                "workload_uid": e.get("workload_uid"),
                "resource": e.get("resource"),
                "points": e.get("forecast_series") or [],
                "active_windows": e.get("active_windows") or [],
            }
            for e in evs
        ]
    return dto
