"""FastAPI app serving the /api/v1 surface. Shares the engine `runner`.

Covers clusters + cached discovery, data sources, settings, analysis runs (with
recommendation cards + lazy evidence), and collection status. Two capabilities are
stubbed because they need components that aren't built yet: live cluster/namespace
discovery (needs a Kubernetes client) and on-demand collection (needs the collector
trigger service). Those endpoints return a clear 501 rather than pretending.
"""
from __future__ import annotations

import os
import sqlite3
import urllib.request
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..analysis_core.io.statestore import StateStore
from ..collector import CollectorUnavailable, trigger_collection
from ..kube import probe as kube_probe
from ..runner import run_analysis
from . import dto

StoreFactory = Callable[[], StateStore]


# --- request bodies --------------------------------------------------------

class MaintenanceBody(BaseModel):
    """Extra fields required when `run_type == 'maintenance'`.

    `target_workload_uid` — the workload the user wants to take down.
    `duration` — how long the downtime needs to be ('30m', '2h', '90').
    `deadline` — the latest the window may end, relative ('3d') or ISO-8601.
    """
    target_workload_uid: str
    duration: str
    deadline: str


class RunRequest(BaseModel):
    cluster_id: Optional[int] = None
    cluster: Optional[str] = None
    scope: Any = "all"
    config: Optional[dict] = None
    collectData: bool = False
    ttl: Optional[str] = None
    run_type: str = "job"
    maintenance: Optional[MaintenanceBody] = None


class ClusterCreate(BaseModel):
    name: str
    api_url: Optional[str] = None
    auth_method: Optional[str] = None
    credential_ref: Optional[str] = None
    ca_cert: Optional[str] = None


class ClusterTest(BaseModel):
    """Body for a live connectivity probe that does NOT persist a cluster (test-before-save)."""
    api_url: Optional[str] = None
    auth_method: Optional[str] = None
    credential_ref: Optional[str] = None
    ca_cert: Optional[str] = None


class SourceCreate(BaseModel):
    type: str
    name: str
    endpoint: Optional[str] = None
    auth_config: Optional[dict] = None
    settings: Optional[dict] = None
    enabled: bool = True


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    endpoint: Optional[str] = None
    enabled: Optional[bool] = None
    auth_config: Optional[dict] = None
    settings: Optional[dict] = None


class SettingsUpdate(BaseModel):
    metric_ttl_hours: Optional[int] = None
    discovery_ttl_min: Optional[int] = None
    result_ttl_hours: Optional[int] = None
    default_resources: Optional[str] = None
    default_window: Optional[str] = None
    thresholds: Optional[dict] = None


class CollectionRequest(BaseModel):
    cluster_id: Optional[int] = None
    cluster: Optional[str] = None
    scope: Any = "all"
    resources: Optional[list] = None
    window: Optional[str] = None


def _default_store_factory() -> StateStore:
    return StateStore(
        driver=os.environ.get("KUBESIESTA_DB_DRIVER", "sqlite"),
        dsn=os.environ.get("KUBESIESTA_DB_DSN", "./kubesiesta.db"),
    )


def _parse_ttl_hours(ttl: Optional[str]) -> int:
    if not ttl:
        return 24
    s = str(ttl).strip().lower()
    try:
        if s.endswith("h"):
            return int(float(s[:-1]))
        if s.endswith("d"):
            return int(float(s[:-1]) * 24)
        return int(float(s))
    except ValueError:
        return 24


def _probe_prometheus(endpoint: Optional[str]) -> str:
    if not endpoint:
        return "unknown"
    url = endpoint.rstrip("/") + "/api/v1/query?query=vector(1)"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (user-configured endpoint)
            return "healthy" if resp.status == 200 else "unreachable"
    except Exception:
        return "unreachable"


def create_app(get_store: Optional[StoreFactory] = None) -> FastAPI:
    get_store = get_store or _default_store_factory
    app = FastAPI(title="Kube Siesta — Engine API", version="0.1.0")

    # Allow the (separately served) static UI to call the API. Permissive by default
    # for local dev; lock down with KUBESIESTA_CORS_ORIGINS="https://ui.example,..." in prod.
    origins = [o.strip() for o in os.environ.get("KUBESIESTA_CORS_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    def with_store(fn):
        store = get_store()
        try:
            return fn(store)
        finally:
            store.close()

    # --- health -----------------------------------------------------------

    @app.get("/api/v1/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    # --- clusters ---------------------------------------------------------

    @app.post("/api/v1/clusters", status_code=201)
    def create_cluster(body: ClusterCreate) -> dict:
        def op(store):
            if store._fetchone("SELECT id FROM clusters WHERE name = ?", (body.name,)):
                raise HTTPException(status_code=409, detail=f"cluster {body.name!r} already exists")
            return dto.cluster_dto(store.create_cluster(
                body.name, api_url=body.api_url, auth_method=body.auth_method,
                credential_ref=body.credential_ref, ca_cert=body.ca_cert))
        return with_store(op)

    @app.get("/api/v1/clusters")
    def list_clusters() -> dict:
        return with_store(lambda s: {"clusters": [dto.cluster_dto(r) for r in s.list_clusters()]})

    @app.get("/api/v1/clusters/{cluster_id}")
    def get_cluster(cluster_id: int) -> dict:
        def op(store):
            row = dto.cluster_dto(store.get_cluster(cluster_id))
            if row is None:
                raise HTTPException(status_code=404, detail="cluster not found")
            return row
        return with_store(op)

    @app.delete("/api/v1/clusters/{cluster_id}")
    def delete_cluster(cluster_id: int) -> dict:
        def op(store):
            if not store.delete_cluster(cluster_id):
                raise HTTPException(status_code=404, detail="cluster not found")
            return {"deleted": True}
        return with_store(op)

    @app.post("/api/v1/clusters:test")
    def test_connection(body: ClusterTest) -> dict:
        """Live connectivity probe using the supplied fields — does NOT save a cluster.

        Requires at least an api_url or a credential_ref: with neither, the probe would
        silently fall back to the cluster the engine runs in and misleadingly report
        "reachable" for empty input.
        """
        if not (body.api_url or body.credential_ref):
            return {"reachable": False,
                    "detail": "enter an API server URL and/or a credential Secret reference to test"}
        return kube_probe(api_url=body.api_url, auth_method=body.auth_method,
                          credential_ref=body.credential_ref, ca_cert=body.ca_cert)

    @app.post("/api/v1/clusters/{cluster_id}:test")
    def test_cluster(cluster_id: int) -> dict:
        """Live connectivity probe for a saved cluster (reads its stored api_url/auth)."""
        def op(store):
            row = store.get_cluster(cluster_id)
            if row is None:
                raise HTTPException(status_code=404, detail="cluster not found")
            result = kube_probe(api_url=row.get("api_url"), auth_method=row.get("auth_method"),
                                credential_ref=row.get("credential_ref"), ca_cert=row.get("ca_cert"))
            store.update_cluster_status(cluster_id, "reachable" if result.get("reachable") else "unreachable",
                                        touch=bool(result.get("reachable")))
            return {"id": str(cluster_id), **result}
        return with_store(op)

    # --- discovery (served from the cache) --------------------------------

    @app.get("/api/v1/clusters/{cluster_id}/namespaces")
    def list_namespaces(cluster_id: int, refresh: bool = Query(False)) -> dict:
        if refresh:
            raise HTTPException(status_code=501, detail="live discovery refresh is not wired yet; returns cached data")
        return with_store(lambda s: {"namespaces": s.list_namespaces(cluster_id)})

    @app.get("/api/v1/clusters/{cluster_id}/namespaces/{namespace}/workloads")
    def list_workloads(cluster_id: int, namespace: str, refresh: bool = Query(False)) -> dict:
        if refresh:
            raise HTTPException(status_code=501, detail="live discovery refresh is not wired yet; returns cached data")
        return with_store(lambda s: {
            "workloads": [dto.workload_dto(r) for r in s.list_workloads(cluster_id, namespace)]})

    # --- data sources -----------------------------------------------------

    @app.post("/api/v1/clusters/{cluster_id}/sources", status_code=201)
    def create_source(cluster_id: int, body: SourceCreate) -> dict:
        def op(store):
            if store.get_cluster(cluster_id) is None:
                raise HTTPException(status_code=404, detail="cluster not found")
            return dto.data_source_dto(store.create_data_source(
                cluster_id, body.type, body.name, endpoint=body.endpoint,
                auth_config=body.auth_config, settings=body.settings, enabled=body.enabled))
        return with_store(op)

    @app.get("/api/v1/clusters/{cluster_id}/sources")
    def list_sources(cluster_id: int) -> dict:
        return with_store(lambda s: {"sources": [dto.data_source_dto(r) for r in s.list_data_sources(cluster_id)]})

    @app.put("/api/v1/sources/{source_id}")
    def update_source(source_id: int, body: SourceUpdate) -> dict:
        def op(store):
            if store.get_data_source(source_id) is None:
                raise HTTPException(status_code=404, detail="source not found")
            return dto.data_source_dto(store.update_data_source(source_id, **body.model_dump(exclude_none=True)))
        return with_store(op)

    @app.delete("/api/v1/sources/{source_id}")
    def delete_source(source_id: int) -> dict:
        def op(store):
            if not store.delete_data_source(source_id):
                raise HTTPException(status_code=404, detail="source not found")
            return {"deleted": True}
        return with_store(op)

    @app.post("/api/v1/sources/{source_id}:test")
    def test_source(source_id: int) -> dict:
        def op(store):
            src = store.get_data_source(source_id)
            if src is None:
                raise HTTPException(status_code=404, detail="source not found")
            health = _probe_prometheus(src.get("endpoint")) if src["type"] == "prometheus" else "unknown"
            store.set_source_health(source_id, health)
            return {"id": str(source_id), "type": src["type"], "health": health}
        return with_store(op)

    # --- settings ---------------------------------------------------------

    @app.get("/api/v1/settings")
    def get_settings() -> dict:
        return with_store(lambda s: dto.settings_dto(s.get_settings()))

    @app.put("/api/v1/settings")
    def put_settings(body: SettingsUpdate) -> dict:
        return with_store(lambda s: dto.settings_dto(s.update_settings(**body.model_dump(exclude_none=True))))

    # --- collection (status readable; trigger not wired) ------------------

    @app.post("/api/v1/collections")
    def post_collection(body: CollectionRequest) -> dict:
        def op(store):
            cluster_id = body.cluster_id if body.cluster_id is not None else store.ensure_cluster(body.cluster or "default")
            try:
                res = trigger_collection(cluster_id, body.scope, body.resources or ["cpu", "memory"], body.window or "7d")
            except CollectorUnavailable as exc:
                raise HTTPException(status_code=503, detail=f"collector service unavailable: {exc}")
            return {"collection_id": str(res["collection_id"]), "status": res.get("status", "running")}
        return with_store(op)

    @app.get("/api/v1/collections/{collection_id}")
    def get_collection(collection_id: int) -> dict:
        def op(store):
            row = dto.collection_dto(store.get_collection_run(collection_id))
            if row is None:
                raise HTTPException(status_code=404, detail="collection not found")
            return row
        return with_store(op)

    # --- runs -------------------------------------------------------------

    @app.post("/api/v1/runs")
    def post_run(req: RunRequest) -> dict:
        def op(store):
            cluster: Any = req.cluster_id if req.cluster_id is not None else (req.cluster or "default")
            kwargs: dict = {}
            if req.run_type == "maintenance":
                if req.maintenance is None:
                    raise HTTPException(
                        status_code=400,
                        detail="run_type 'maintenance' requires a `maintenance` body "
                               "with target_workload_uid, duration, deadline",
                    )
                kwargs.update(
                    target_workload_uid=req.maintenance.target_workload_uid,
                    duration=req.maintenance.duration,
                    deadline=req.maintenance.deadline,
                )
            elif req.run_type != "job":
                raise HTTPException(status_code=400, detail=f"unknown run_type: {req.run_type!r}")

            try:
                result = run_analysis(
                    store, cluster=cluster, scope=req.scope, config_overrides=req.config,
                    ttl_hours=_parse_ttl_hours(req.ttl), collect_data=req.collectData,
                    run_type=req.run_type, **kwargs,
                )
            except ValueError as exc:
                # Bad user input (unparseable duration, deadline too soon, etc.).
                raise HTTPException(status_code=400, detail=str(exc))

            return {"run_id": str(result.run_id), "name": result.name, "status": result.status}
        return with_store(op)

    @app.get("/api/v1/runs")
    def list_runs(cluster_id: Optional[int] = Query(None), limit: int = Query(50)) -> dict:
        return with_store(lambda s: {"runs": [dto.run_summary_dto(r) for r in s.list_runs(cluster_id, limit)]})

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: int) -> dict:
        def op(store):
            status = dto.run_status_dto(store, run_id)
            if status is None:
                raise HTTPException(status_code=404, detail="run not found")
            return status
        return with_store(op)

    @app.get("/api/v1/runs/{run_id}/recommendations")
    def get_recommendations(run_id: int) -> dict:
        def op(store):
            cards = dto.cards_dto(store, run_id)
            if cards is None:
                raise HTTPException(status_code=404, detail="run not found")
            return cards
        return with_store(op)

    @app.get("/api/v1/runs/{run_id}/recommendations/{rec_id}/evidence")
    def get_evidence(run_id: int, rec_id: str, series: bool = Query(True)) -> dict:
        def op(store):
            body = dto.evidence_dto(store, run_id, dto.parse_rec_id(rec_id), include_series=series)
            if body is None:
                raise HTTPException(status_code=404, detail="recommendation not found")
            return body
        return with_store(op)

    # Optionally serve the static UI from the same origin (dev/demo convenience). The UI
    # stays a standalone static bundle; set KUBESIESTA_UI_DIR to the ui/ folder to mount it.
    ui_dir = os.environ.get("KUBESIESTA_UI_DIR")
    if ui_dir and os.path.isdir(ui_dir):
        app.mount("/", StaticFiles(directory=ui_dir, html=True), name="ui")

    return app


# Module-level app for `uvicorn engine.api.app:app`.
app = create_app()
