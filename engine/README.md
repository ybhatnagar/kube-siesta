# Engine (Python)

Reads metrics from the shared state database, decides:

- which workloads should become **Jobs / CronJobs / KEDA / Knative** (the **job**
  recommender), and
- the **lowest-impact downtime window** for a target workload before a deadline
  (the **maintenance** recommender),

writes the results back, and serves them over a small REST API. It also ships a
synthetic-data generator so you can exercise both flows with no cluster.

The engine is structured as a **shared analysis core** used by two thin recommender
heads. The core's stages are plain functions over NumPy arrays — no database access
inside them — so each is unit-tested on synthetic data.

## Install & test
```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"     # add [postgres] for the Postgres driver
./.venv/bin/pytest -q                   # 87 tests, 1 skipped (Postgres integration; opt-in)
```

## Command line

```bash
# Generate a synthetic cluster, write CSV/JSON fixtures, and (optionally) seed a DB
./.venv/bin/python -m engine.cli synth --format csv --out ./fixtures --seed-db --db-dsn ./demo.db

# Job flow — analyze whatever metrics are already in the DB and write recommendations
./.venv/bin/python -m engine.cli run --cluster synth --db-dsn ./demo.db \
    --window 7d --resources cpu,memory --ttl 24h

# Maintenance flow — recommend a low-impact downtime window for one workload
./.venv/bin/python -m engine.cli run --type maintenance \
    --cluster synth --app vmw-costing/Deployment/vmw-costing1 \
    --duration 30m --deadline 3d --db-dsn ./demo.db

# Serve the REST API
./.venv/bin/python -m engine.cli serve --db-dsn ./demo.db --host 127.0.0.1 --port 8000

# Create the SQLite schema by itself (dev convenience)
./.venv/bin/python -m engine.cli init-db --db-dsn ./demo.db
```

`engine run` and the API's `POST /runs` share the same code path. (`kubesiesta-engine` is
the installed console-script equivalent of `python -m engine.cli`.)

## REST API (`/api/v1`)

Runs and recommendations:
- `POST /runs` — `{cluster|cluster_id, scope, config?, ttl?, run_type?, maintenance?}`
  → `{run_id, name, status}`. `run_type` defaults to `"job"`; for `"maintenance"`
  also provide `maintenance:{target_workload_uid, duration, deadline}`.
- `GET /runs` — run history (each entry carries `run_type`).
- `GET /runs/{id}` — status plus freshness (`data_as_of`, `stale`, `run_type`).
- `GET /runs/{id}/recommendations` — the recommendation **cards**. Shape depends on
  the run's `run_type`:
  - **Job**: workload, from → target, cadence, run time, duration, savings,
    confidence, one-line summary.
  - **Maintenance**: workload, `recommended_start`/`recommended_end`, duration,
    `impact_score`, confidence, one-line summary, `impacted_apps_count` +
    `impacted_apps_preview`.
- `GET /runs/{id}/recommendations/{recId}/evidence` — the **"why"**:
  - **Job**: the numbers (jump %, active/idle ratio, period, overlap), the
    downsampled chart series with its overlay (trend line, band, active windows),
    and peer suggestions.
  - **Maintenance**: the numbers (impact score, confidence, duration, window,
    deadline), the full impacted-app list (each with period, active fraction,
    window overlap, and a note flagging aperiodic-treated-as-always-active
    callers), and per-workload forecast series for the "why" chart.
  - Add `?series=false` for a text-and-numbers-only response.

Configuration and discovery:
- `GET/POST/DELETE /clusters`, `GET /clusters/{id}` — manage connected clusters
  (`POST /clusters/{id}:test` is reserved for a live probe, not wired yet → `501`).
- `GET /clusters/{id}/namespaces`, `.../namespaces/{ns}/workloads` — browse
  discovered workloads from the cache (`?refresh=true` needs a Kubernetes client → `501`).
- `GET/POST /clusters/{id}/sources`, `PUT/DELETE /sources/{id}`,
  `POST /sources/{id}:test` — manage and probe metric sources.
- `GET/PUT /settings` — thresholds and retention windows.
- `POST /collections` triggers a collection via the collector's trigger service (set
  `KUBESIESTA_COLLECTOR_URL`); `GET /collections/{id}` reports its status. `POST /runs
  {collectData:true}` collects fresh, then analyzes (falling back to stored data if
  the collector is unreachable).

Examples:
```bash
# Job run
curl -s -XPOST localhost:8000/api/v1/runs -H 'content-type: application/json' \
     -d '{"cluster":"synth","scope":"all"}'
curl -s localhost:8000/api/v1/runs/1/recommendations | python3 -m json.tool

# Maintenance run
curl -s -XPOST localhost:8000/api/v1/runs -H 'content-type: application/json' -d '{
  "cluster":"synth",
  "run_type":"maintenance",
  "maintenance":{
    "target_workload_uid":"vmw-costing/Deployment/vmw-costing1",
    "duration":"30m",
    "deadline":"3d"
  }
}'
curl -s localhost:8000/api/v1/runs/2/recommendations/rec_1/evidence | python3 -m json.tool
```

## The shared analysis core

`prepare` (resample to a regular freq, fill gaps) → `periodicity` (FFT +
autocorrelation to find the repeating cycle) → `seasonality` (STL decomposition
confirms it's real) → `active/idle` (rolling-median band splits busy vs idle samples)
→ `aggregate` (union of active masks across resources).

Two entry points are provided:

- `analysis_core.signal.analyze_signal(series, cfg)` — job-shaped: applies the
  job-specific candidate-rejection filters (jump %, ratio, union, overlap) and
  returns `None` for workloads that don't qualify.
- `analysis_core.timeline.detect_timeline(series, cfg)` — maintenance-shaped:
  returns whenever a periodic active/idle mask can be recovered at all. Docs/07 §1
  step 5 in the design notes: maintenance keeps every workload's timeline instead
  of discarding non-candidates.

The **interaction graph** utilities (`shares_period`, `windows_align`,
`adhoc_overlap`, `has_adhoc_inbound`) are shared too — the job head walks
**downstream** edges for peer expansion; the maintenance head walks **upstream**
edges for dep traversal.

## The job head

Runs after the shared core: `filter` (spike must be big and the workload mostly idle)
→ `cost` (estimate savings from node share × price × idle fraction) → `target` (pick
Job / CronJob / KEDA / Knative) → `confidence` (combine seasonal strength, resource
agreement, overlap, jump margin) → `builder` (assemble the card + a plain-English
summary). The runner uses the dependency graph two ways: it suggests downstream
**peers** that share the same period and spike at the same time, and it marks a
workload's target as request-driven (Knative) when an inbound caller fires during the
workload's idle windows.

## The maintenance head

Runs after the shared core: `deps.upstream_deps` (BFS on reverse interaction edges,
cycle-safe) → `multi_app.build_forecasts` (recover each workload's timeline; fit a
`Forecaster` per workload) → `forecaster.SeasonalNaive.project` (repeat the last full
period of the mask onto the future grid up to the deadline) → `scoring.score_instants`
(sum of active-app flags per instant) → `scoring.min_window` (earliest-tie sliding
window of length `L` with the minimum score sum) → `runner._assemble_result` (the
maintenance DTO with impacted-app list + evidence).

**Aperiodic default: always-active.** When a caller has no metric data or no
detectable period, its forecast is `True` everywhere. This is a pessimistic default
so the recommended window doesn't get chosen under an unpredictable dependency — the
`note` field on `ImpactedApp` records which branch was taken so the DTO surfaces it.

The `Forecaster` protocol is deliberately small (`fit(series, period_hours)` +
`project(future_index) -> Series[bool]`) so Holt-Winters / SARIMA / statsforecast can
drop in behind it without pipeline changes.

## Config, thresholds, cost model

Every threshold lives in `engine/analysis_core/config.py` (`EngineConfig`) and
defaults from the database's `settings` row; per-run values in the API request body
(under `config`) or the CLI flags can override them.

The "why" text is generated from deterministic templates in `engine/why/templates.py`;
there's an LLM hook (`engine/why/llm.py`), off by default and not wired.

## Databases and the schema

SQLite is the default (one file). For Postgres, pass `--db-driver postgres --db-dsn
"postgres://…"`. In production the schema is created by the collector's `db migrate`;
for local SQLite work, `engine init-db` (or the first `synth --seed-db`) creates it
from the collector's migration files, so there's a single source of truth for the
schema.

The `analysis_runs` row is polymorphic via `run_type` (default `'job'`); maintenance
results land in dedicated `maintenance_results` / `maintenance_impacted_apps` /
`maintenance_evidence` tables. Existing job callers keep working with no request-body
changes.

## Layout
```
engine/
  analysis_core/          shared front half — used by both heads
    prepare.py            resample + gap-fill
    periodicity.py        FFT + autocorrelation
    seasonality.py        STL strength
    active_idle.py        rolling-median band
    aggregate.py          cross-resource union / overlap
    filter.py             ratio helpers (usage_ratio, peak_load_jump_pct, baseline_ratio)
    signal.py             analyze_signal — job-shaped Signal (includes job filters)
    timeline.py           detect_timeline — maintenance-shaped WorkloadTimeline
    interaction_graph.py  shares_period, windows_align, has_adhoc_inbound
    config.py             EngineConfig (thresholds + cost model)
    types.py              Interval, ResourceEvidence
    io/statestore.py      SQLite (dev) / Postgres (prod) — reads tiers 2-3, writes tier 4
  recommenders/
    job/                  cost, target, builder, runner, types (WorkloadRecommendation, Peer)
    maintenance/          deps, forecaster, multi_app, scoring, runner, types
  synth/                  synthetic candidates / non-candidates + CSV/JSON export
  why/                    summary templates, chart downsampling, LLM hook (off)
  api/                    FastAPI app + response builders (dispatches on run_type)
  runner.py               top-level dispatcher (run_type -> job | maintenance head)
  cli.py                  CLI (mirrors POST /runs; --type maintenance / --app / ...)
tests/                    per-stage, end-to-end, and API tests for both heads
```
