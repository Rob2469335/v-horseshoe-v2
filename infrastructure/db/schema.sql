PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    subsystem TEXT NOT NULL,
    status TEXT NOT NULL,
    trigger_source TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    input_ref TEXT,
    output_ref TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_runs_subsystem_started_at
ON runs (subsystem, started_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    run_id TEXT,
    event_type TEXT NOT NULL,
    subsystem TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_events_occurred_at
ON events (occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_subsystem_occurred_at
ON events (subsystem, occurred_at DESC);

CREATE TABLE IF NOT EXISTS traces (
    id TEXT PRIMARY KEY,
    trace_key TEXT NOT NULL UNIQUE,
    subsystem TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_event_id TEXT,
    score REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_event_id) REFERENCES events(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_traces_subsystem_score
ON traces (subsystem, score DESC);

CREATE TABLE IF NOT EXISTS feedback (
    id TEXT PRIMARY KEY,
    trace_id TEXT,
    run_id TEXT,
    rating INTEGER,
    outcome TEXT NOT NULL,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (trace_id) REFERENCES traces(id) ON DELETE SET NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_outcome_created_at
ON feedback (outcome, created_at DESC);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    payload_json TEXT,
    lease_owner TEXT,
    leased_until TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_available_at
ON jobs (status, available_at, priority);

CREATE TABLE IF NOT EXISTS health_snapshots (
    id TEXT PRIMARY KEY,
    component TEXT NOT NULL,
    status TEXT NOT NULL,
    detail_json TEXT,
    checked_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_health_component_checked_at
ON health_snapshots (component, checked_at DESC);
