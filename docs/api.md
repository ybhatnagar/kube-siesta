# REST API reference

Base path: **`/api/v1`** (served by `engine serve`, or the deployed engine
Deployment via the UI's `/api` proxy).

Requests and responses are JSON. Errors follow FastAPI's default shape:
`{"detail": "..."}` with the appropriate HTTP status. Every endpoint is
read-only from the target cluster's perspective — nothing on the target
Kubernetes cluster is ever mutated.

---

## Health

### `GET /healthz`

```json
{ "status": "ok" }
```

---

## Runs

### `POST /runs` — start an analysis

Job (default):

```json
{
  "cluster": "prod",           // or cluster_id: 1
  "scope": "all",              // or { "workload_uids": [...] }
  "config": {                  // optional; overrides settings
    "resources": ["cpu", "memory"],
    "window": "7d",
    "resample_freq": "1h",
    "thresholds": { "jump_min": 50, "ratio_max": 0.5 }
  },
  "collectData": false,        // optional; triggers collection first
  "ttl": "24h"                 // optional; result retention
}
```

Maintenance:

```json
{
  "cluster": "prod",
  "run_type": "maintenance",
  "config": { "resources": ["cpu", "memory"], "resample_freq": "2min" },
  "maintenance": {
    "target_workload_uid": "my-namespace/Deployment/payments",
    "duration": "30m",         // supports "30m", "2h", "1d", or minutes as a number
    "deadline": "3d"           // supports "3d", "48h", or ISO-8601
  }
}
```

Response:

```json
{ "run_id": "12", "name": "jolly-tapir-2924", "status": "completed" }
```

### `GET /runs`

Run history. Each entry surfaces `run_type` (`"job"` or `"maintenance"`).

```json
{
  "runs": [
    { "id": "12", "name": "jolly-tapir-2924", "run_type": "maintenance",
      "status": "completed", "stale": false,
      "data_as_of": "2026-07-26T15:22:27Z",
      "created_at": "2026-07-26T15:30:00Z",
      "completed_at": "2026-07-26T15:30:03Z" },
    ...
  ]
}
```

### `GET /runs/{id}` — status

```json
{
  "id": "12", "run_type": "maintenance", "status": "completed",
  "data_as_of": "2026-07-26T15:22:27Z", "stale": false, "progress": 100
}
```

`stale = true` means the data behind the run is older than `metric_ttl_hours`
in settings. `progress` is 100 on completion.

---

## Recommendations

### `GET /runs/{id}/recommendations`

The recommendation **cards**. Shape depends on the run's `run_type`.

**Job cards:**

```json
{
  "run": {
    "id": "12", "name": "brave-otter-4821", "run_type": "job",
    "cluster": "prod", "data_as_of": "...", "stale": false, "window": "7d"
  },
  "recommendations": [
    {
      "id": "rec_5",
      "workload": { "kind": "Deployment", "name": "periodic-batch", "namespace": "demo-workloads" },
      "from": "Pod",
      "to_target": "CronJob",
      "cadence": "every 8h",
      "run_time": "10:00 UTC",
      "duration": "2h",
      "savings": { "amount": 32.0, "currency": "USD", "period": "month" },
      "confidence": "high",
      "summary": "CPU spikes ~900% for 2h every 8h; idle otherwise. memory aligns (overlap 100%)."
    }
  ]
}
```

**Maintenance cards:**

```json
{
  "run": {
    "id": "12", "name": "jolly-tapir-2924", "run_type": "maintenance",
    "cluster": "prod", "data_as_of": "...", "stale": false,
    "duration_minutes": 30, "deadline": "2026-07-29T00:00:00Z",
    "target_workload_uid": "my-namespace/Deployment/payments"
  },
  "recommendations": [
    {
      "id": "rec_1",
      "workload": { "kind": "Deployment", "name": "payments", "namespace": "my-namespace",
                    "workload_uid": "my-namespace/Deployment/payments" },
      "recommended_start": "2026-07-28T03:00:00Z",
      "recommended_end":   "2026-07-28T03:30:00Z",
      "duration_minutes": 30,
      "deadline": "2026-07-29T00:00:00Z",
      "impact_score": 1.0,
      "confidence": "high",
      "summary": "All workloads projected idle — best-case slot.",
      "impacted_apps_count": 2,
      "impacted_apps_preview": [
        { "kind": "Deployment", "name": "checkout", "namespace": "orders" },
        { "kind": "Deployment", "name": "reporter", "namespace": "reporting" }
      ]
    }
  ]
}
```

### `GET /runs/{id}/recommendations/{recId}/evidence`

The **"why"**. `?series=false` returns the text/metrics only (drops the chart
series arrays).

**Job evidence:**

```json
{
  "recommendation_id": "rec_5",
  "summary": "CPU spikes ~900% for 2h every 8h; idle otherwise. memory aligns (overlap 100%).",
  "metrics": {
    "jump_pct": 900.0, "active_idle_ratio": 0.25,
    "period_hours": 8.0, "active_duration_min": 120.0,
    "overlap_pct": 100.0, "confidence": "high"
  },
  "peers": [
    { "workload": "benchmark2", "shared_seasonality": true,
      "savings": { "amount": 12.5, "currency": "USD", "period": "month" },
      "to_target": "CronJob", "note": "peaks at the same time (overlap 100%)" }
  ],
  "series": [
    { "resource": "cpu", "unit": "cores",
      "points": [ { "t": "2026-07-01T00:00:00Z", "v": 0.2 }, ... ],
      "overlay": {
        "trend": 0.2, "eps_min": 0.18, "eps_max": 0.22,
        "active_windows": [ { "start": "...", "end": "..." }, ... ]
      } }
  ]
}
```

**Maintenance evidence:**

```json
{
  "recommendation_id": "rec_1",
  "summary": "All workloads projected idle — best-case slot.",
  "metrics": {
    "impact_score": 1.0, "confidence": "high", "duration_minutes": 30,
    "recommended_start": "...", "recommended_end": "...", "deadline": "..."
  },
  "impacted_apps": [
    {
      "workload": { "kind": "Deployment", "name": "checkout", "namespace": "orders" },
      "workload_uid": "orders/Deployment/checkout",
      "period_hours": 8.0,
      "active_fraction": 0.25,
      "impact_score": 0.0,
      "note": "detected 8.0h cycle"
    },
    {
      "workload": { "kind": "Deployment", "name": "reporter", "namespace": "reporting" },
      "workload_uid": "reporting/Deployment/reporter",
      "period_hours": null,
      "active_fraction": 1.0,
      "impact_score": 30.0,
      "note": "no periodic signal; assumed always-active"
    }
  ],
  "series": [
    { "workload_uid": "my-namespace/Deployment/payments", "resource": "union",
      "points": [ { "ts": "...", "value": false }, ... ],
      "active_windows": [] }
  ]
}
```

---

## Clusters

### `GET /clusters`

```json
{ "clusters": [ { "id": "1", "name": "prod", "api_url": "https://…",
                  "auth_method": "kubeconfig", "status": "reachable",
                  "created_at": "...", "last_connected_at": "..." } ] }
```

`status` can be `"reachable"`, `"unreachable"`, or `"unknown"` (never probed).

### `POST /clusters` — register a cluster

```json
{
  "name": "prod",
  "api_url": "https://k8s.prod:6443",
  "auth_method": "token",             // "kubeconfig" | "token" | "client_cert" | "basic"
  "credential_ref": "prod-sa-token",  // k8s Secret name in the engine's namespace
  "ca_cert": null                     // optional PEM
}
```

### `GET /clusters/{id}` / `DELETE /clusters/{id}`

Standard CRUD.

### `POST /clusters:test` — probe form-body fields (no save)

Test-before-save. Requires at least `api_url` or `credential_ref`.

```json
// request
{ "api_url": "https://k8s.prod:6443", "auth_method": "token", "credential_ref": "prod-sa-token" }
```

```json
// response
{ "reachable": true, "server": "https://k8s.prod:6443", "server_version": "v1.36.1" }
```

Unreachable:

```json
{ "reachable": false, "server": "https://…", "detail": "<urlopen error ...>" }
```

Reached but unauthorized still counts as **reachable** (with a note):

```json
{ "reachable": true, "server": "...",
  "detail": "reached API server (HTTP 401); credentials may lack read access" }
```

### `POST /clusters/{id}:test` — probe a saved cluster

Same shape as above; also persists `status` and (on success) bumps
`last_connected_at`.

### `GET /clusters/{id}/namespaces`, `.../workloads`

Browse discovered workloads from the local cache. `?refresh=true` needs a
Kubernetes client to list live — currently returns `501`.

---

## Data sources

Metric sources per cluster.

- `GET/POST /clusters/{id}/sources`, `PUT/DELETE /sources/{id}` — CRUD
- `POST /sources/{id}:test` — probe. For `type: "prometheus"` it hits
  `<endpoint>/api/v1/query?query=vector(1)`; sets health to `"healthy"` or
  `"unreachable"`.

---

## Settings

### `GET /settings`

```json
{
  "metric_ttl_hours": 24,
  "discovery_ttl_min": 10,
  "result_ttl_hours": 24,
  "default_resources": "cpu,memory",
  "default_window": "7d",
  "thresholds": { "seasonality_gain": 0.30, "band": 0.10, "jump_min": 50,
                  "ratio_max": 0.5, "min_period": 3 }
}
```

### `PUT /settings`

Same shape; any subset of keys.

---

## Collections

### `POST /collections` — trigger collection now

Calls the collector's trigger service (`KUBESIESTA_COLLECTOR_URL`). Returns a
collection id you can poll.

```json
{ "collection_id": "45", "status": "running" }
```

### `GET /collections/{id}`

```json
{ "id": "45", "status": "success", "progress": 100,
  "data_as_of": "...", "rows_written": 12844, "error": null }
```

Status values: `"pending" | "running" | "success" | "failed" | "partial"`.
Failure surfaces `error` and does **not** raise — the caller (usually the
engine) decides whether to fall back to stored data.

`POST /runs {"collectData": true, ...}` combines the two: trigger collection,
wait for it, then run analysis. If collection fails, the run proceeds on
stored data with `stale: true` in the run summary.

---

## Errors

- `400 Bad Request` — unparseable duration, deadline too close to now,
  unknown `run_type`, missing `maintenance` body when `run_type=maintenance`.
- `404 Not Found` — unknown run / recommendation / cluster / source.
- `409 Conflict` — cluster name already exists.
- `501 Not Implemented` — live discovery refresh
  (`?refresh=true` on discovery endpoints).
- `503 Service Unavailable` — collector trigger service unreachable.

---

## Environment variables (engine side)

- `KUBESIESTA_DB_DRIVER` (`sqlite` | `postgres`)
- `KUBESIESTA_DB_DSN`
- `KUBESIESTA_UI_DIR` — optional, mounts the static UI from the same origin
- `KUBESIESTA_CORS_ORIGINS` — comma-separated (default `*` for dev)
- `KUBESIESTA_COLLECTOR_URL` — collector trigger service URL
- `KUBESIESTA_TEST_POSTGRES_DSN` — gates the Postgres integration test in the
  test suite
