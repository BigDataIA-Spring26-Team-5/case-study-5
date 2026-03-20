-- scoring_runs_schema.sql
-- Run this manually in Snowflake before using Phase 3A scoring run tracking.
--
-- Tracks each invocation of the CS3 scoring pipeline per ticker.
-- A 'running' row indicates a concurrent scoring run is in progress.
-- A 'failed' row records the error message so GET /dimensions can surface it.

CREATE TABLE IF NOT EXISTS scoring_runs (
    run_id       VARCHAR(36)    NOT NULL PRIMARY KEY,
    ticker       VARCHAR(10)    NOT NULL,
    status       VARCHAR(20)    NOT NULL DEFAULT 'running',
    started_at   TIMESTAMP_NTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    completed_at TIMESTAMP_NTZ,
    dimensions_written INT       DEFAULT 0,
    error_message      TEXT,
    created_at   TIMESTAMP_NTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

CREATE INDEX IF NOT EXISTS idx_scoring_runs_ticker ON scoring_runs (ticker);
