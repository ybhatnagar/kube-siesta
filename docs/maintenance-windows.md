# Maintenance windows — deep dive

Given a workload that has to go down for **duration L** before **deadline D**,
Kube Siesta finds the earliest future time window that causes the **least
collective impact** — not just the target's own impact, but every upstream
service that would be affected because it calls the target.

## The algorithm in nine steps

1. **Identify dependent applications** — build the interaction DAG; traverse
   every path **arriving on** the target node. Every upstream caller
   (directly or transitively) is potentially impacted.
2. **Seasonality detection** — for the target and each upstream caller, run
   STL and confirm the periodic signal explains enough variance to be worth
   projecting.
3. **Periodicity `P`** — detect the dominant cycle per resource. Reject any
   workload whose resources disagree on `P` beyond tolerance.
4. **Active/idle classification** — rolling-median band splits each series
   into active and idle samples.
5. **Aggregate across resources** — union of active masks (unlike the job
   head, no jump%/ratio candidate-rejection filter — maintenance keeps every
   app's timeline).
6. **Aggregate across dependent apps** — repeat 2–5 for each dependency.
7. **Project to deadline D** — a `Forecaster` extends each timeline into the
   future up to `D`.
8. **Score each instant** — number of apps projected active at that instant
   (target + all dependencies).
9. **Best window** — slide a window of length `L` from now → `D − L`, sum
   scores, return the min-score window (earliest tie).

Steps 2–5 are the [shared analysis core](architecture.md#the-shared-analysis-core).
Steps 1 and 6–9 are the maintenance head's own code, under
`engine/engine/recommenders/maintenance/`.

## The Forecaster interface

Step 7 — projecting each workload's active/idle mask forward — is deliberately
abstracted behind a small `Forecaster` protocol so alternate implementations
can drop in without pipeline changes:

```python
class Forecaster(Protocol):
    def fit(self, active_series: pd.Series | None,
                  period_hours: float | None) -> None: ...
    def project(self, future_index: pd.DatetimeIndex) -> pd.Series: ...
```

The shipped default is **`SeasonalNaive`**: it takes the last full period of
the observed active mask and repeats it forward, indexed by phase. Cheap,
deterministic, and correct for any workload whose future looks like its
recent past.

Alternate implementations that fit the same interface:

- **Holt-Winters** (`statsmodels.tsa.holtwinters.ExponentialSmoothing`) — better
  when there's a slow drift on top of the seasonality.
- **SARIMA / statsforecast** — heavyweight, useful when the pattern is complex.
- **A learned model** — anything you can wrap in `fit()` + `project()`.

## Aperiodic callers — the pessimism trade-off

Some upstream callers don't have a detectable period, or don't have any
metric data yet. Kube Siesta's default is to project them as **always
active**:

```python
if timeline is None:
    forecaster.fit(None, None)      # → project(future_index) returns all True
    note = "no periodic signal; assumed always-active"
```

This is a **pessimistic** default. It biases the recommendation toward quieter
slots because the aperiodic caller contributes `+1` to the score at every
instant.

**Why default to pessimism?** The alternative — treating aperiodic callers
as always idle — is much worse: you'd pick a maintenance window when an
unpredictable caller could plausibly be mid-request, and there'd be no signal
to warn you. Pessimistic is the safer default for a first release.

**When it bites.** A truly-idle-but-aperiodic dep will inflate the score of
every window. In the extreme case where every caller is aperiodic, all
windows tie and the algorithm just picks the earliest slot. If your payload
verification suggests a "high impact" score is being driven by aperiodic
callers rather than real conflicts, the DTO tells you:

- The `note` field on each `ImpactedApp` records which branch was taken
  (`"detected 8.0h cycle"` vs `"no periodic signal; assumed always-active"`).
- The summary text calls out how many aperiodic callers contributed:
  `"3 aperiodic callers projected always-active (pessimistic)."`
- The "Why?" modal footer in the UI restates the trade-off so operators
  know whether to trust the score.

**Escape hatches:**

1. **Give the caller more data.** Aperiodic often just means "not enough
   history". Wait a period or two and re-run.
2. **Manual override.** Not implemented yet — see the roadmap. The intended
   API is `impacted_apps_override` in the run body: `{ workload_uid: "…",
   treat_as: "idle" | "active" }`.
3. **Swap the forecaster.** If Holt-Winters can find a subtle trend the
   seasonal-naive missed, aperiodic → periodic and the pessimism goes away.

## Scoring and the sliding window

The scoring stage is deliberately simple: sum of `bool` masks across all
forecasted workloads at each instant.

```python
def score_instants(forecasts):
    total = forecasts[0].astype(int).copy()
    for f in forecasts[1:]:
        total = total.add(f.astype(int), fill_value=0)
    return total
```

The sliding-window minimization uses a **cumulative-sum trick** for O(n)
performance:

```python
csum = np.concatenate(([0.0], np.cumsum(scores, dtype="float64")))
window_sums = csum[W:] - csum[:-W]
best = int(np.argmin(window_sums))   # earliest tie wins
```

`np.argmin` returns the **first** occurrence of the minimum, which matches the
docs/07 requirement that ties resolve as "sooner is better". If two windows
tie on impact, the earlier one is picked so operators aren't unnecessarily
deferred.

## Confidence

Currently a coarse heuristic keyed on the peak concurrent active-app count
(`max_score`) within the chosen window:

| `max_score` | Confidence | Interpretation |
|---|---|---|
| 0 | high | Nobody projected active — best-case slot |
| 1 | high | Only one workload briefly active |
| 2 | medium | Two workloads overlap during the window |
| ≥ 3 | low | Multiple simultaneous conflicts; consider extending the deadline |

This will get refined once we have payload signal on real fleets. It's
deliberately simple for now to make manual review straightforward.

## What lands in the DTO

Per maintenance card:

- `recommended_start`, `recommended_end` — exact window (`start + duration_minutes`)
- `impact_score` — the summed score over the chosen window
- `confidence` — from the heuristic above
- `summary_text` — human-readable overview including any aperiodic-callers callout
- `impacted_apps` — one row per upstream caller (workload identity + period +
  active fraction + per-window impact + the `note` explaining which
  Forecaster branch was taken)
- `evidence` — per-workload downsampled forecast series + projected active
  windows, so the UI can draw the "Why?" chart

## Related code

- `engine/engine/recommenders/maintenance/deps.py` — upstream traversal
- `engine/engine/recommenders/maintenance/forecaster.py` — `Forecaster` +
  `SeasonalNaive`
- `engine/engine/recommenders/maintenance/multi_app.py` — build forecasts
  across target + deps
- `engine/engine/recommenders/maintenance/scoring.py` — instant scoring +
  sliding window
- `engine/engine/recommenders/maintenance/runner.py` — orchestration + DTO
  assembly + persistence
- `engine/tests/test_maintenance_*.py` — per-stage unit tests + a multi-app
  e2e that covers the aperiodic pessimism path explicitly.
