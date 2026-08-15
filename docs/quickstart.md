# Quickstart — 5 minutes, no cluster

Kube Siesta ships a synthetic-cluster generator so you can go from zero to
recommendations without a Kubernetes cluster or Prometheus. This walkthrough
uses SQLite as the state store and runs everything on your laptop.

## Prerequisites

- **Python ≥ 3.9** (for the engine + API)
- **Go ≥ 1.23** (only if you want to build the collector — this quickstart uses
  the synthetic generator instead of real metrics)

## 1. Install the engine

```bash
cd engine
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/pytest -q                       # 87 tests, 1 skipped — sanity check
```

`.[dev]` pulls in `pytest` + `httpx` for testing. Add `.[postgres]` if you want
the psycopg driver for production; SQLite works out of the box.

## 2. Generate a synthetic cluster + seed the DB

```bash
./.venv/bin/python -m engine.cli synth \
    --format csv --out ./fixtures \
    --seed-db --db-dsn ./demo.db
```

This writes CSV fixtures under `./fixtures/` and seeds `./demo.db` with a
5-workload cluster: three periodic candidates (a clean 8h burst, an aligned
peer, and a workload with an upward trend), a steady non-candidate, and a
"busy" workload that spikes too often to be worth shifting.

## 3. Run the **job** recommender

```bash
./.venv/bin/python -m engine.cli run --cluster synth --db-dsn ./demo.db
# → {"run_id": 1, "name": "brave-otter-4821", "status": "completed",
#    "recommendations": 3, "data_as_of": "…", "stale": false}
```

Three cards come out — the periodic ones. The steady and always-busy workloads
are correctly ignored.

## 4. Run the **maintenance** recommender

```bash
./.venv/bin/python -m engine.cli run --type maintenance \
    --cluster synth --app vmw-costing/Deployment/vmw-costing1 \
    --duration 30m --deadline 3d --db-dsn ./demo.db
# → {"run_id": 2, "name": "clever-falcon-9124",
#    "recommended_start": "…", "recommended_end": "…", "max_score": 1,
#    "status": "completed", ...}
```

Kube Siesta walks the interaction graph for `vmw-costing1`, projects each
workload's rhythm forward to your 3-day deadline, scores each future minute by
active-app count, and returns the earliest 30-min window with the smallest sum.

## 5. Serve the API + UI

```bash
KUBESIESTA_UI_DIR=../ui ./.venv/bin/python -m engine.cli serve \
    --db-dsn ./demo.db --port 8000
```

Open **http://localhost:8000/**. Walk the wizard:

1. **Connect cluster** — the `synth` cluster is already in the DB.
2. **Select workloads** — either **All namespaces** or drill into `vmw-costing`.
3. **Data sources & run** — click *Configure & run*. In **Job candidates** mode
   this is the analysis config; flip the top-of-page **Maintenance windows**
   toggle to switch to the maintenance flow.
4. **Recommendations** — the same cards you got from the CLI, plus the *Why?*
   and *Similar* / *Impacted apps* modals.

## 6. Fetch cards over the API

```bash
# job cards
curl -s http://127.0.0.1:8000/api/v1/runs/1/recommendations | python3 -m json.tool

# maintenance card + evidence
curl -s http://127.0.0.1:8000/api/v1/runs/2/recommendations \
    | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/v1/runs/2/recommendations/rec_1/evidence \
    | python3 -m json.tool
```

Full REST reference: [`api.md`](api.md).

## Where to next

- **Point it at real Prometheus data** → [`deployment.md`](deployment.md) walks
  through the collector setup and shows how to run everything in Kubernetes.
- **Understand the algorithm** → [`architecture.md`](architecture.md) explains
  the shared analysis core and how the two heads consume it.
- **Deep-dive on maintenance** → [`maintenance-windows.md`](maintenance-windows.md)
  covers the forecaster interface and the aperiodic-pessimism trade-off.

## Troubleshooting

- **No candidates found.** The synth generator seeds 5 workloads, 3 of which
  are candidates. If you get zero, `pytest` will fail too — check the engine
  install.
- **`Address already in use` on port 8000.** Pick another port with
  `--port 8001` or free the port.
- **Postgres integration test skipped.** That's expected. Point
  `KUBESIESTA_TEST_POSTGRES_DSN` at a Postgres with the schema applied and
  it'll run.
