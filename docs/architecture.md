# Architecture

Kube Siesta is three independent processes that only share a database — no
direct calls between them. Both recommender features run on one shared
analysis pipeline in the engine.

```
  Prometheus ──►  Collector (Go)  ──►  ┌─────────────┐  ──►  Engine (Python)  ──►  REST API
  (or any            pulls metrics     │  state DB   │       ┌───────────────┐
   metrics source)   + interactions    │ (SQLite /   │       │  analysis     │       recommendation
                     into the DB       │  Postgres)  │       │  core         │       cards + "why"
                                        └─────────────┘      ├──job──┬──maint│
                                                             └────────┴──────┘
```

The **database schema is the only cross-module contract**. The engine reads
from the DB and never calls the collector; the collector writes to the DB and
never calls the engine. This gives you two useful properties:

- Re-run the analysis after a restart without re-fetching metrics.
- Swap the metrics source (Prometheus → Thanos → custom PromQL) without
  touching the engine.

## The three modules

### Collector (`collector/`, Go)

Pulls per-workload CPU/memory metrics from Prometheus (or any source you add)
and writes:

- The metric **samples** themselves (into `metric_samples`).
- A lightweight **workload identity** record (namespace / kind / name /
  requests) so the engine can label and cost each recommendation.

Ships as a small distroless image (~26 MB, static Go, no cgo). Supports SQLite
for dev and Postgres via pgx for production, behind one interface.

Adding a new metrics source is one implementation of the `MetricsConnector`
interface — copy the Prometheus connector under
`internal/connectors/prometheus/`, register it in an `init()`, and it becomes
available as `--source <name>`.

### Engine (`engine/`, Python)

Reads metrics from the DB, decides which workloads should become
Jobs / CronJobs / KEDA / Knative (**job** recommender) **and** the
lowest-impact downtime window for a target workload (**maintenance**
recommender), writes the results back, and serves them over `/api/v1`.

Structured as a **shared analysis core** used by two thin recommender heads.
See [`../engine/README.md`](../engine/README.md) for the full source layout.

### UI (`ui/`, static)

An optional static SPA — plain HTML/CSS/JavaScript, no build step, no
framework. Talks to `/api/v1` with `fetch`. The four-step wizard has a
segmented **Mode toggle** at the top that switches between the two recommender
flows without leaving the wizard.

Because it's framework-free, the "image" is just three static files served
by nginx. The collector + engine still run headless without it.

## The shared analysis core

Every workload passes through the same five stages before either head decides
what to do with it:

1. **Prepare** — pull the metric history, resample to a regular frequency
   (default 1h), fill small gaps by interpolation.
2. **Periodicity** — detect the dominant repeating cycle using FFT +
   autocorrelation. If CPU and memory disagree on the period beyond tolerance,
   reject the workload.
3. **Seasonality** — STL decomposition confirms the repeating signal is real
   (must beat pure-trend RMSE by ≥ 30% by default). Filters resources that
   fail the seasonality gate.
4. **Active / idle** — mark each sample "active" when usage rises above a
   rolling-median band (median × (1 ± band_pct), default ±10%), "idle"
   otherwise. Extract contiguous active windows.
5. **Aggregate** — union of active masks across resources. This is the workload's
   **timeline**.

The core is exposed via two entry points:

- **`analysis_core.signal.analyze_signal(series, cfg)`** — job-shaped:
  additionally applies the job-specific candidate-rejection filters (jump %,
  ratio, union, overlap) and returns `None` for workloads that don't qualify.
- **`analysis_core.timeline.detect_timeline(series, cfg)`** — maintenance-shaped:
  returns whenever a periodic active/idle mask can be recovered at all. The
  maintenance head needs every workload's timeline (docs/07 §1 step 5:
  "maintenance keeps every workload's timeline, it doesn't discard
  non-candidates").

The **interaction-graph utilities** (`shares_period`, `windows_align`,
`adhoc_overlap`, `has_adhoc_inbound`) are shared too — the job head walks
**downstream** edges for peer expansion; the maintenance head walks **upstream**
edges for dependency traversal.

## The job head

Runs after the shared core:

```
filter  →  cost  →  target  →  confidence  →  builder
```

- **filter** — the workload's spike must be big (peak-load jump ≥ 50% by
  default) and it must be idle most of the time (active/idle ratio < 0.5).
- **cost** — savings = (workload's node share) × node price/hour × replicas ×
  idle fraction. If the workload's resource requests are set, they drive the
  node share; otherwise observed usage does.
- **target** — pick the migration target tier:
  - **CronJob** — clean, repeating scheduled burst.
  - **Job** — fires only once in the window.
  - **KEDA** (scale-to-zero) — low residual baseline or sporadic ad-hoc
    traffic in idle time.
  - **Knative** — unpredictable, request-driven spikes (a caller fires during
    the workload's idle windows).
- **confidence** — high / medium / low from seasonal strength, resource
  agreement, overlap fraction, and jump margin.
- **builder** — assemble the card + a plain-English "why" summary.

The runner uses the dependency graph two ways: it suggests downstream **peers**
that share the same period and spike at the same time, and it marks a
workload's target as request-driven (Knative) when an inbound caller fires
during the workload's idle windows.

## The maintenance head

Runs after the shared core:

```
upstream_deps  →  build_forecasts  →  score_instants  →  min_window  →  assemble_result
```

1. **`deps.upstream_deps`** — BFS on **reverse** interaction edges (cycle-safe,
   depth-capped). Every transitive caller of the target is impacted.
2. **`multi_app.build_forecasts`** — for the target + each dependency, recover
   the timeline (`analysis_core.timeline.detect_timeline`) and fit a
   `Forecaster`.
3. **`forecaster.SeasonalNaive.project`** — repeat the last full period of the
   observed active mask forward onto the future grid up to the deadline.
4. **`scoring.score_instants`** — sum of active-app flags at each future
   instant.
5. **`scoring.min_window`** — earliest-tie sliding window of length `L` with
   the smallest sum.
6. **`runner._assemble_result`** — assemble the maintenance card: recommended
   window, impact score, confidence, impacted-app list, per-workload
   forecast evidence.

**Aperiodic default: always-active.** When a caller has no metric data or no
detectable period, its forecast is `True` everywhere. This is a pessimistic
default so the recommended window doesn't get chosen under an unpredictable
dependency. The `note` field on `ImpactedApp` records which branch was taken
so the DTO surfaces it. Deep dive: [`maintenance-windows.md`](maintenance-windows.md).

The `Forecaster` protocol is deliberately small (`fit(series, period_hours)` +
`project(future_index) -> Series[bool]`) so Holt-Winters / SARIMA /
statsforecast can drop in behind it without pipeline changes.

## The state DB — four data tiers

One database, four kinds of data (all retention windows are configurable):

| Tier | Tables | Retention | Written by |
|---|---|---|---|
| **1. Config** | `clusters`, `data_sources`, `settings` | kept until deleted | API |
| **2. Discovery cache** | `disc_namespaces`, `disc_workloads`, `disc_pods` | short (default 10 min) | Collector |
| **3. Collected data** | `metric_samples`, `interactions`, `collection_runs` | default 1 day | Collector |
| **4. Runs + results** | `analysis_runs`, `recommendations`, `recommendation_evidence`, `recommendation_peers`, `maintenance_results`, `maintenance_impacted_apps`, `maintenance_evidence` | default 1 day | Engine |

`analysis_runs.run_type` is the polymorphic discriminator (`'job'` |
`'maintenance'`); results land in the appropriate typed tables. Existing job
callers keep working with no request-body changes.

SQLite is the default (a single file). Postgres is the production backend —
pass `--db-driver postgres --db-dsn "postgres://…"` to either the collector
or the engine. The schema lives with the collector
(`collector/internal/store/migrations/`); the engine's `apply_schema()` reads
those same files to bootstrap SQLite for dev, so there's one source of truth.

## Cluster credentials — never stored, always referenced

The engine's ServiceAccount has a namespaced `get secrets` role. When you add
an external cluster with a `credential_ref`, the engine reads the referenced
Kubernetes Secret at probe time to resolve the credential (kubeconfig / SA
token / client cert / basic auth). The DB stores only the reference, never
the credential itself.

## Where things live in the repo

```
collector/   Go — pulls metrics into the DB           (collector/README.md)
engine/      Python — analysis core + two heads + API (engine/README.md)
ui/          static web UI — 4-step wizard            (ui/README.md)
deploy/      Dockerfiles + Helm chart                 (deploy/README.md)
docs/        this documentation                       (docs/*)
```
