# UI

An optional web front-end for the Kube Siesta. It's a **static single-page
app** — plain HTML/CSS/JavaScript, no build step, no framework — that talks to the
engine's REST API with `fetch`. Keeping it framework-free means the "image" is just
three static files, and the collector + engine still run headless without it.

Four-step wizard: **Connect cluster → Select workloads → Data sources & run →
Recommendations.** A segmented **Mode toggle** at the top switches between the two
recommender flows without leaving the wizard:

- **Job candidates** — cards show `workload → target (Job / CronJob / KEDA / Knative)`,
  cadence, projected savings, and confidence. Actions: **Why?** (text summary +
  metrics + optional usage chart) and **Similar** (downstream peers that share the
  same seasonality).
- **Maintenance windows** — cards show the target workload, the recommended
  downtime `start – end`, duration, impacted-app count + preview chips, and impact
  confidence. Actions: **Why?** (summary + metrics + a projected-activity chart
  with the chosen-window band across the target + upstream deps) and **Impacted
  apps** (full upstream-caller list with period, projected active fraction, window
  overlap, and a note flagging aperiodic-treated-as-always-active callers).

The recommendation cards have no "apply" action — the tool only advises and never
changes your cluster.

## Run it

The UI needs the engine API running (see `../engine/README.md`). There are two ways
to serve the static files:

**A. Let the engine serve it (simplest — one process, same origin, no CORS):**
```bash
# seed some data so there's something to look at
cd ../engine
./.venv/bin/python -m engine.cli synth --seed-db --db-dsn ./demo.db --out /tmp/fx

# serve the API + UI together
KUBESIESTA_UI_DIR=../ui ./.venv/bin/python -m engine.cli serve --db-dsn ./demo.db --port 8000
# open http://localhost:8000/
```

**B. Any static file server (UI and API on different origins):**
```bash
# API (CORS is permissive by default; lock down with KUBESIESTA_CORS_ORIGINS in prod)
cd ../engine && ./.venv/bin/python -m engine.cli serve --db-dsn ./demo.db --port 8000 &

# UI on another port
cd ../ui && python3 -m http.server 3000
# open http://localhost:3000/  — set the API box at the top to http://localhost:8000/api/v1
```

The **API** box at the top of the page shows connectivity and lets you point at a
different API base (persisted in the browser; also settable with `?api=` in the URL).

## Driving the wizard

1. **Connect cluster** — add or pick a cluster.
2. **Select workloads** — expand a namespace and tick the workloads to include. In
   **Maintenance** mode, the workloads you tick here populate the target dropdown in
   the config modal (you pick one target per run).
3. **Data sources & run** — click **Configure & run**. In **Job** mode this opens the
   analysis config modal (resources, data window, min period). In **Maintenance**
   mode this opens a different modal (target dropdown, duration, deadline, resources,
   optional resample frequency). Submitting fires `POST /runs` with the right
   `run_type`.
4. **Recommendations** — the card grid and modals swap shape based on the run's
   `run_type` (which the API surfaces on `GET /runs/{id}`).

The **Mode toggle** is a display-time switch — flipping it after a run swaps step 4's
title and subtitle back to match the current mode, and the next run uses the flipped
mode. Runs of both types coexist in `GET /runs`.

## Files
```
index.html   the wizard markup, mode bar, and all modals (job + maintenance)
styles.css   the design system (clean / neutral) + the .modebar / .seg-btn styles
app.js       the app logic + a small fetch() client for /api/v1
             (renders both job and maintenance cards; dispatches on run_type)
```

## Notes
- Cluster discovery reads whatever workloads have metrics in the DB (populated by the
  collector or `engine synth --seed-db`). A freshly-added cluster shows no workloads
  until it's collected.
- "Test connection" on a cluster and the discovery-refresh path surface the engine's
  `501` responses gracefully — those need the Kubernetes client, which isn't wired
  yet.
- **Aperiodic callers.** In maintenance mode, when an upstream caller has no
  detected cycle or no metric data, the engine treats it as always-active (a
  pessimistic default that keeps the window quiet under unpredictable deps). The
  Why? modal footer flags this so you can decide whether a "high impact" score is
  a real conflict or the pessimistic default biting.
