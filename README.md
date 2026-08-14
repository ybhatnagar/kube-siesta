# Kube Siesta

Two open-source recommenders on one shared analysis pipeline:

1. **Job candidates** — looks at how your Kubernetes workloads actually use CPU and
   memory over time, finds the ones that sit idle most of the day and only spike on a
   regular schedule, and recommends turning them into **Jobs / CronJobs** (or
   scale-to-zero **KEDA / Knative**) — with a suggested run time, cadence, and estimated
   **monthly $ savings**.
2. **Maintenance windows** — given a workload that has to go down for `duration L`
   before a `deadline D`, recommends the future time window that causes the **least
   collective impact** — factoring in every upstream service that calls the target
   (directly or transitively) and would therefore be affected.

Both features are advisory. The tool only reads metrics and interactions — **it never
touches your cluster**.

**Job example:**
```
vmw-costing1   Pod → CronJob   every 8h at 10:00 UTC, runs ~2h   saves ~$32/mo   (high confidence)
  "CPU spikes ~900% for 2h every 8h; idle otherwise. memory aligns (overlap 100%)."
```

**Maintenance example:**
```
payments   downtime window 03:00–03:30 UTC on 2026-08-02   impact 0   (high confidence)
  "All workloads projected idle — best-case slot. 0 upstream callers active."
```

## How it works

Three independent pieces that only share a database — no direct calls between them.
The engine hosts **both features** on one shared analysis core, so the collector and
DB are unchanged whether you're finding job candidates or maintenance windows:

```
  Prometheus ──►  Collector (Go)  ──►  ┌─────────────┐  ──►  Engine (Python)  ──►  REST API
  (or any            pulls metrics     │  state DB   │       ┌───────────────┐
   metrics source)   + interactions    │ (SQLite /   │       │  analysis     │       recommendation
                     into the DB       │  Postgres)  │       │  core         │       cards + "why"
                                        └─────────────┘      ├──job──┬──maint│
                                                             └────────┴──────┘
```

- **Collector** (`collector/`, Go) — pulls per-workload metrics and dependency edges
  into the database. New sources are added by implementing one interface.
- **Engine** (`engine/`, Python) — reads the metrics, runs the analysis, writes
  recommendations, and serves them over `/api/v1`. Ships a synthetic-data generator
  so you can try the whole thing with no cluster.
- **UI** (`ui/`) — an optional static web front-end (plain HTML/CSS/JS, no build step)
  with a **Job ↔ Maintenance mode toggle** for the 4-step wizard. Everything still
  works headless from the CLI/API without it.

The database is the only contract between the pieces, so you can re-run the analysis
after a restart without re-fetching, and swap the metrics source without touching the
engine.

## The analysis, in plain terms

For each workload, across its metrics (CPU, memory, network, disk), the **shared
analysis core** does:

1. **Prepare** — pull the metric history, resample to hourly buckets, fill small gaps.
2. **Find the period** — detect the dominant repeating cycle (e.g. "every 8h") using
   autocorrelation + FFT. If CPU and memory disagree on the period, reject the workload.
3. **Confirm it's real** — STL seasonal decomposition; the repeating signal must
   explain enough of the variance (not just noise).
4. **Split active vs idle** — mark each hour "active" when usage rises above a
   rolling-median band, "idle" otherwise, and pull out the active windows.
5. **Aggregate resources** — union of active masks across CPU + memory + …

The **job head** then adds:

6. **Filter** — keep it only if the spike is large (active is ≥ 50% higher than idle)
   and the workload is idle most of the time (active-to-idle ratio below 0.5).
7. **Combine + recommend** — pick a target, estimate savings, score confidence, write
   the "why":
   - **CronJob** — a clean, repeating scheduled burst.
   - **Job** — fires only once in the window.
   - **KEDA** (scale-to-zero) — a low residual baseline or sporadic traffic in idle time.
   - **Knative** — unpredictable, request-driven spikes (e.g. a dependency calls it
     while it's otherwise idle).

   It also uses the dependency graph: if a downstream workload spikes on the same
   schedule, it's suggested alongside as a **peer**.

The **maintenance head** instead:

6. **Traverse upstream deps** — from the target workload, walk the interactions graph
   in reverse to enumerate every transitive caller. Those are the impacted apps.
7. **Forecast forward to the deadline** — a swappable `Forecaster` (seasonal-naive
   ships as the default) projects each app's active/idle mask onto the future grid
   between now and `D`. Aperiodic callers (no detected cycle or no metric data) are
   **pessimistically projected as always-active** so the window doesn't get chosen
   under an unpredictable dependency.
8. **Score every instant** — sum of active-app flags across the target + all deps.
9. **Slide a window of length L** — earliest lowest-sum window wins.

**Savings** (job) = (workload's share of a node) × node price/hour × replicas × fraction
of time idle. If the workload's resource requests are known they drive the node share;
otherwise observed usage does. Node price/capacity are configurable (defaults: 4 vCPU,
16 GiB, $0.20/hr). Every threshold above is configurable too.

## Prerequisites
- **Go ≥ 1.23** (to build the collector) and **Python ≥ 3.9** (to run the engine).
- No Kubernetes cluster is needed to try it — the engine ships a synthetic generator
  and uses SQLite by default. Postgres is supported for production.

## Quickstart

```bash
# Engine — install and test
cd engine
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest -q                       # 87 tests, 1 skipped (Postgres integration; opt-in)

# Collector — build and test
cd ../collector
go build -o bin/collector ./cmd/collector
go test ./...
./bin/collector connectors list             # -> prometheus
```

## Try it end-to-end with no cluster

The engine can generate a synthetic cluster (known candidates + non-candidates), seed
it into a local SQLite database, analyze it, and serve the results:

```bash
cd engine

# 1) generate a synthetic cluster and seed it into ./demo.db
./.venv/bin/python -m engine.cli synth --format csv --out ./fixtures --seed-db --db-dsn ./demo.db

# 2) job recommendations
./.venv/bin/python -m engine.cli run --cluster synth --db-dsn ./demo.db
#  -> {"run_id": 1, "name": "...", "status": "completed", "recommendations": 3, ...}

# 3) maintenance recommendation for one of the synth candidates
./.venv/bin/python -m engine.cli run --type maintenance \
    --cluster synth --app vmw-costing/Deployment/vmw-costing1 \
    --duration 30m --deadline 3d --db-dsn ./demo.db

# 4) serve the API and fetch either run's cards
./.venv/bin/python -m engine.cli serve --db-dsn ./demo.db --port 8000 &
curl -s http://127.0.0.1:8000/api/v1/runs/1/recommendations | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/v1/runs/2/recommendations | python3 -m json.tool
```

The job run gives three recommendations (the periodic workloads) and correctly ignores
the steady and always-busy ones. The maintenance run returns a `recommended_start`/`_end`
window with an `impact_score`, `confidence`, and the impacted-app list.

## With the web UI

The engine can serve the static UI from the same port — seed some data, then point it
at the `ui/` folder:

```bash
cd engine
./.venv/bin/python -m engine.cli synth --seed-db --db-dsn ./demo.db --out /tmp/fx
KUBESIESTA_UI_DIR=../ui ./.venv/bin/python -m engine.cli serve --db-dsn ./demo.db --port 8000
# open http://localhost:8000/ — flip between Job candidates and Maintenance windows
# using the segmented toggle at the top, then walk the 4-step wizard.
```

The UI can also be served by any static file server on its own origin (CORS is on by
default); see [`ui/README.md`](ui/README.md).

## Use it against a real Prometheus

```bash
# 1) create the schema, then pull 14 days of metrics into ./state.db
cd collector
./bin/collector db migrate --db-dsn ./state.db
./bin/collector ingest --metrics \
    --prom-url http://prometheus.monitoring:9090 \
    --namespace my-namespace --resources cpu,memory --since 14d --step 1h \
    --cluster prod --db-dsn ./state.db

# 2) analyze it with the engine (job flow)
cd ../engine
./.venv/bin/python -m engine.cli run --cluster prod --db-dsn ../collector/state.db
./.venv/bin/python -m engine.cli serve --db-dsn ../collector/state.db --port 8000
```

The default PromQL assumes kube-state-metrics labels (`namespace`, `workload`,
`workload_type`); override it for your setup — see [`collector/README.md`](collector/README.md).

## Find a maintenance window

The engine's second head recommends the **lowest-impact downtime window** for a
workload that has to go down for a given duration before a deadline. It projects the
target and every upstream caller's active/idle pattern forward, scores each instant
by the number of workloads active at that moment, and slides a window of the requested
length to find the earliest slot with the minimum sum. Aperiodic callers (no detected
cycle or no metric data) are pessimistically projected as always-active so the window
doesn't get chosen under an unpredictable dependency.

CLI:

```bash
./.venv/bin/python -m engine.cli run \
    --type maintenance --cluster prod \
    --app my-namespace/Deployment/payments \
    --duration 30m --deadline 3d \
    --db-dsn ../collector/state.db
```

Or via the API:

```bash
curl -s -XPOST http://127.0.0.1:8000/api/v1/runs -H 'content-type: application/json' -d '{
  "cluster": "prod",
  "run_type": "maintenance",
  "maintenance": {
    "target_workload_uid": "my-namespace/Deployment/payments",
    "duration": "30m",
    "deadline": "3d"
  }
}'
# -> {"run_id":"12","name":"jolly-tapir-2924","status":"completed"}
curl -s http://127.0.0.1:8000/api/v1/runs/12/recommendations | python3 -m json.tool
```

The recommendation card carries `recommended_start`/`recommended_end`, the
`impact_score` (peak concurrent active-app count over the window), `confidence`, and a
preview of the impacted upstream apps. Fetch `.../evidence` for the full impacted-app
list + per-workload forecast series used to render the "why" chart.

## REST API

Base path `/api/v1` (served by `engine serve`):

| Method | Path | What it does |
|---|---|---|
| `POST` | `/runs` | start a run: `{cluster, scope, config?, ttl?, run_type?, maintenance?}` → `{run_id, name, status}`. `run_type` defaults to `"job"`; `"maintenance"` also requires `maintenance:{target_workload_uid, duration, deadline}` |
| `GET`  | `/runs` | run history (each entry surfaces `run_type`) |
| `GET`  | `/runs/{id}` | run status, freshness (`data_as_of`, `stale`, `run_type`) |
| `GET`  | `/runs/{id}/recommendations` | recommendation cards — shape depends on `run_type` (job: from/to/cadence/savings; maintenance: recommended window/duration/impacted apps) |
| `GET`  | `/runs/{id}/recommendations/{recId}/evidence` | the "why": metrics + chart series + peer suggestions (job) or impacted apps + forecast series (maintenance). Add `?series=false` for text only |
| `GET/POST/DELETE` | `/clusters`, `/clusters/{id}` | manage connected clusters |
| `GET`  | `/clusters/{id}/namespaces`, `.../workloads` | browse discovered workloads (from cache) |
| `GET/POST/PUT/DELETE` | `/clusters/{id}/sources`, `/sources/{id}` | manage metric sources; `POST /sources/{id}:test` probes one |
| `GET/PUT` | `/settings` | default thresholds and data-retention windows |
| `GET`  | `/collections/{id}` | status of a collection run |

On-demand collection is wired: `POST /collections` and `POST /runs {collectData:true}`
call the collector's trigger service to collect right now (and fall back to stored
data if it's unreachable). Live cluster discovery (`?refresh=true`) still returns
`501` — it needs a Kubernetes client to list namespaces/workloads live, which isn't
built yet.

## Where things are stored

One database, four kinds of data (all retention windows are configurable):

- **Config** — connected clusters and metric-source settings (kept until deleted).
- **Discovery** — namespaces / workloads / pods (short-lived cache).
- **Collected** — the metric samples and dependency edges (default 1-day retention).
- **Results** — analysis runs (with a `run_type` discriminator) plus their
  recommendations, evidence, and — for maintenance — impacted apps + forecasts
  (default 1-day).

SQLite is the default (a single file, great for dev and single-node). Postgres is the
production backend — pass `--db-driver postgres --db-dsn "postgres://…"` to either tool.

## Repo layout
```
collector/   Go — pulls metrics into the DB        (see collector/README.md)
engine/      Python — analysis core + two recommender heads + REST API
             (see engine/README.md)
ui/          static web UI — 4-step wizard, Job ↔ Maintenance mode toggle
             (see ui/README.md)
deploy/      Dockerfiles + Helm chart              (see deploy/README.md)
```

## Deploy to Kubernetes

Per-module images and a Helm chart that runs the collector CronJob, engine+API, UI,
and an optional bundled Postgres:

```bash
docker build -t kubesiesta/collector:0.1.0 collector/   # ~26 MB (distroless, static Go)
docker build -t kubesiesta/engine:0.1.0    engine/      # python-slim + pandas/scipy/statsmodels
docker build -t kubesiesta/ui:0.1.0        ui/          # ~76 MB (nginx, static bundle)
helm install jr deploy/helm/kubesiesta -n kubesiesta --create-namespace \
  --set collector.promUrl=http://prometheus.monitoring:9090
```

Full instructions (external DB, ingress, minikube demo) in [deploy/README.md](deploy/README.md).

## Status

**Working today** (verified end-to-end on a local 3-node kind cluster):

- Collector's Prometheus metrics path.
- Full **shared analysis core** (prepare / periodicity / seasonality / active-idle /
  aggregate / interaction-graph utilities) across CPU, memory, network, and disk.
- **Job recommender**: cross-resource aggregation, the tiered target heuristic
  (Job / CronJob / KEDA / Knative), cost + savings, deterministic "why" + chart
  series, dependency-aware peer suggestions.
- **Maintenance recommender**: upstream dependency-DAG traversal, seasonal-naive
  forecasting to a user-supplied deadline (behind a swappable `Forecaster`
  interface), per-instant scoring, sliding-window minimization, impacted-app list.
- Synthetic-data generator (known candidates + non-candidates + interaction fixtures).
- Complete `/api/v1` surface with `run_type` dispatch and DTO polymorphism.
- Static web UI with a **Job ↔ Maintenance mode toggle** wired to both flows,
  including the maintenance "Why?" chart and "Impacted apps" list.
- On-demand (API-triggered) collection via the collector's trigger service.
- Container images + a Helm chart for Kubernetes.

**Not built yet:**
- Live discovery refresh — querying a connected cluster's Kubernetes API to list
  namespaces / workloads on demand (needs a Kubernetes client).
- The collector's interactions step — the interaction-graph schema exists and the
  engine already consumes it (both for job peers and maintenance upstream traversal),
  but the collector's step to populate it from a service mesh isn't wired yet.
- Holt-Winters / SARIMA forecasters — the seasonal-naive default ships; the
  `Forecaster` interface is designed for these to drop in without a pipeline change.

## Contributing

Bug reports and PRs welcome. Two things to keep an eye on when contributing:

- **Tests stay green.** Engine: `cd engine && ./.venv/bin/pytest`. Collector:
  `cd collector && go test ./...`. Docker changes: verify with `deploy/README.md`
  (Helm chart lint + a kind roundtrip is the easiest check).
- **Contracts.** The database schema is the only cross-module contract — see the
  `collector/internal/store/migrations/` files, which the engine reads directly to
  bootstrap SQLite. `/api/v1` DTOs are the other contract; keep them stable across
  the job and maintenance heads.

## License

MIT — see [`LICENSE`](LICENSE). Permissive: commercial use, private forks, SaaS
hosting, and academic/research use are all allowed. Downstream users must keep the
copyright notice and the license text.
