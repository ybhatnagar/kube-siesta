-- 0002_maintenance.sql — polymorphic runs + maintenance-result tables (PostgreSQL).
-- Mirrors 0002 in the sqlite dialect. See docs/07 §4 for the design.
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
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL REFERENCES analysis_runs(id) ON DELETE CASCADE,
    maintenance_for_uid  TEXT NOT NULL,
    workload_kind        TEXT,
    workload_name        TEXT,
    namespace            TEXT,
    recommended_start    TIMESTAMPTZ,
    recommended_end      TIMESTAMPTZ,
    duration_min         DOUBLE PRECISION,
    deadline             TIMESTAMPTZ,
    impact_score         DOUBLE PRECISION,
    confidence           TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    summary_text         TEXT
);
CREATE INDEX idx_maintenance_results_run ON maintenance_results (run_id);

CREATE TABLE maintenance_impacted_apps (
    id                     BIGSERIAL PRIMARY KEY,
    maintenance_result_id  BIGINT NOT NULL REFERENCES maintenance_results(id) ON DELETE CASCADE,
    workload_uid           TEXT NOT NULL,
    workload_kind          TEXT,
    workload_name          TEXT,
    namespace              TEXT,
    period_hours           DOUBLE PRECISION,
    active_fraction        DOUBLE PRECISION,
    impact_score           DOUBLE PRECISION,
    note                   TEXT
);
CREATE INDEX idx_maintenance_impacted_result ON maintenance_impacted_apps (maintenance_result_id);

CREATE TABLE maintenance_evidence (
    id                     BIGSERIAL PRIMARY KEY,
    maintenance_result_id  BIGINT NOT NULL REFERENCES maintenance_results(id) ON DELETE CASCADE,
    workload_uid           TEXT NOT NULL,
    resource               TEXT NOT NULL,
    forecast_series        JSONB,
    active_windows         JSONB
);
CREATE INDEX idx_maintenance_evidence_result ON maintenance_evidence (maintenance_result_id);
