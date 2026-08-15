-- 0002_maintenance.sql — polymorphic runs + maintenance-result tables (SQLite).
-- Mirrors 0002 in the postgres dialect. See docs/07 §4 for the design.
--
-- Two things happen here:
--   1. analysis_runs gets a run_type discriminator (default 'job' so every
--      pre-existing row backfills to the job feature).
--   2. Three new tables carry the maintenance-recommender result shape:
--      maintenance_results / maintenance_impacted_apps / maintenance_evidence.
--      They live alongside recommendations/… which stay job-only.

-- ---------------------------------------------------------------------------
-- 1. run_type discriminator
-- ---------------------------------------------------------------------------
ALTER TABLE analysis_runs
    ADD COLUMN run_type TEXT NOT NULL DEFAULT 'job'
    CHECK (run_type IN ('job', 'maintenance'));

CREATE INDEX idx_analysis_runs_type ON analysis_runs (run_type);

-- ---------------------------------------------------------------------------
-- 2. maintenance result tables
-- ---------------------------------------------------------------------------
CREATE TABLE maintenance_results (
    id                   INTEGER PRIMARY KEY,
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    maintenance_for_uid  TEXT NOT NULL,          -- target workload uid
    workload_kind        TEXT,                   -- denormalized for display
    workload_name        TEXT,
    namespace            TEXT,
    recommended_start    TEXT,                   -- ISO-8601 UTC
    recommended_end      TEXT,                   -- ISO-8601 UTC
    duration_min         REAL,                   -- requested maintenance length L in minutes
    deadline             TEXT,                   -- deadline D from the request (ISO-8601 UTC)
    impact_score         REAL,                   -- min-window score (sum of active-app counts)
    confidence           TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    summary_text         TEXT
);
CREATE INDEX idx_maintenance_results_run ON maintenance_results (run_id);

CREATE TABLE maintenance_impacted_apps (
    id                     INTEGER PRIMARY KEY,
    maintenance_result_id  INTEGER NOT NULL REFERENCES maintenance_results(id) ON DELETE CASCADE,
    workload_uid           TEXT NOT NULL,
    workload_kind          TEXT,
    workload_name          TEXT,
    namespace              TEXT,
    period_hours           REAL,        -- detected period for this dep (NULL if aperiodic)
    active_fraction        REAL,        -- fraction of the forecast horizon it's projected active
    impact_score           REAL,        -- overlap of its projection with the chosen window
    note                   TEXT
);
CREATE INDEX idx_maintenance_impacted_result ON maintenance_impacted_apps (maintenance_result_id);

CREATE TABLE maintenance_evidence (
    id                     INTEGER PRIMARY KEY,
    maintenance_result_id  INTEGER NOT NULL REFERENCES maintenance_results(id) ON DELETE CASCADE,
    workload_uid           TEXT NOT NULL,        -- which workload this evidence is for (target or a dep)
    resource               TEXT NOT NULL,
    forecast_series        TEXT,                 -- JSON: downsampled forecast points
    active_windows         TEXT                  -- JSON: projected active windows [{start,end}]
);
CREATE INDEX idx_maintenance_evidence_result ON maintenance_evidence (maintenance_result_id);
