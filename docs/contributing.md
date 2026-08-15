# Contributing

Bug reports and PRs welcome. This document covers the mechanics; for
architectural context see [`architecture.md`](architecture.md).

## Ways to contribute

- **Bug reports.** Open an issue with a minimal repro. If it's a
  recommendation bug, include the run's DTO
  (`GET /runs/{id}/recommendations`) or the run configuration — the run's
  `data_as_of` timestamp is usually enough to correlate.
- **Feature ideas.** Open an issue first so we can align on shape. Kube
  Siesta is deliberately narrow — read-only advisor for job-migration
  candidates and maintenance windows. Anything mutating (auto-apply, GitOps
  push, etc.) is out of scope on purpose.
- **New Forecaster.** Implement the `Forecaster` protocol in
  `engine/engine/recommenders/maintenance/forecaster.py` and add tests. See
  [`maintenance-windows.md`](maintenance-windows.md) for the interface.
- **New metric source.** Copy the Prometheus connector under
  `collector/internal/connectors/prometheus/` and register it. The engine
  doesn't need to change — everything talks through the DB schema.
- **Docs.** Anything in this `docs/` folder is fair game. If a phrase in the
  README, an error message, or an API detail is unclear, a doc PR is a great
  first contribution.

## Development setup

**Engine (Python):**

```bash
cd engine
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"      # add [postgres] for the psycopg driver
./.venv/bin/pytest -q                     # 87 tests, 1 skipped
```

**Collector (Go):**

```bash
cd collector
go build -o bin/collector ./cmd/collector
go test ./...
```

**UI:** no build step. Any static file server works; the engine can also serve
it directly with `KUBESIESTA_UI_DIR=../ui ./.venv/bin/python -m engine.cli serve`.

## Running the full loop locally

Fastest end-to-end (no Kubernetes cluster needed):

```bash
cd engine
./.venv/bin/python -m engine.cli synth --seed-db --db-dsn ./demo.db --out /tmp/fx
./.venv/bin/python -m engine.cli run --cluster synth --db-dsn ./demo.db
KUBESIESTA_UI_DIR=../ui ./.venv/bin/python -m engine.cli serve --db-dsn ./demo.db --port 8000
# open http://localhost:8000/
```

## Tests must stay green

- **Engine:** `cd engine && ./.venv/bin/pytest` — 87 tests, 1 skipped
  (the Postgres integration test is opt-in via `KUBESIESTA_TEST_POSTGRES_DSN`).
- **Collector:** `cd collector && go test ./...` — expects 2 packages ok.
- **Helm chart:** `helm lint deploy/helm/kubesiesta` if you touched anything
  under `deploy/`.

If your change is behavior-preserving (a refactor, a rename, a docs update),
zero test edits should be needed. If you're changing behavior:

- Add per-stage unit tests for any function-level change.
- Add an end-to-end test if you're touching the runner / API / DB contract.
- Reuse the synthetic-cluster generator for deterministic fixtures — the
  `engine/engine/synth/` module has helpers for common workload shapes.

## Contracts to preserve

Two cross-module contracts that a PR must not break silently:

1. **The state-DB schema.** Migrations live in
   `collector/internal/store/migrations/{sqlite,postgres}/`. The engine reads
   the SQLite files directly to bootstrap dev DBs, so the two dialects have to
   describe the same logical schema. Add a new numbered migration (`0003_*.sql`)
   for schema changes — don't edit existing ones.
2. **The `/api/v1` DTOs.** Documented in [`api.md`](api.md). Keep them stable
   across the job and maintenance heads. If you need a new field, add it —
   consumers should tolerate unknown fields. Don't rename or remove existing
   fields without a deprecation cycle.

## Commit + PR style

- **One logical change per PR.** Multiple unrelated changes make review
  painful.
- **Commit message:** short subject (≤ 72 chars), blank line, then optional
  body explaining the *why* (the diff explains the *what*).
- **PRs should include:** a short description of the change, any behavior
  before/after that isn't obvious from the diff, and a note if tests were
  added / updated.
- **Before pushing:** rebase onto the current `main` — merge commits create
  noise, and this repo's history is small enough that a linear history is
  achievable.

## Style

- **Python:** match the existing code — small pure functions, dataclasses at
  the seams, no over-abstraction. Type hints are welcome but not required for
  private helpers. No black/ruff config yet; match neighboring files.
- **Go:** `go fmt` + `go vet`. Errors get returned, not logged-and-swallowed.
- **JavaScript:** the UI is single-file, no build step, no framework. Keep it
  that way unless there's a specific reason to change.
- **Comments:** only when the *why* isn't obvious from the code. Don't
  comment what the code already says.
- **Docs:** if you change behavior that a user would notice, update the
  relevant doc under `docs/`.

## Release process

Not formalized yet. When we tag `v0.2.0`:

1. Bump `version` in `engine/pyproject.toml`, `collector/go.mod` if needed,
   `deploy/helm/kubesiesta/Chart.yaml`.
2. Update the "Status" section of the top-level README if anything crossed
   the "built / not built yet" line.
3. Rebuild the three images with the new tag; push to whichever registry
   we're using.
4. Tag the commit: `git tag v0.2.0 && git push --tags`.
5. Write release notes referencing the PRs that landed.

## Reporting security issues

Please **do not** open a public issue for security-relevant bugs. Instead,
open a private security advisory on GitHub (Security → Report a vulnerability),
or email the repo owner directly.

Read-only advisor means the blast radius of most bugs is limited — but the
cluster-connectivity probe touches real credentials and the API can accept
`credential_ref` values that resolve to real Secrets, so bugs there matter.
