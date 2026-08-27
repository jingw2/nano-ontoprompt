-- Synthetic PostgreSQL schema for the runtime fixture corpus.
-- Test infrastructure only. Never used against a production database.

CREATE TABLE managed_targets (
    target_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_version INTEGER NOT NULL DEFAULT 1,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE refresh_source_rows (
    row_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    watermark TIMESTAMPTZ NOT NULL,
    sequence_no INTEGER NOT NULL,
    payload_summary VARCHAR(128) NOT NULL
);

CREATE TABLE refresh_event_log (
    event_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    cursor_value VARCHAR(64) NOT NULL,
    sequence_no INTEGER NOT NULL,
    received_at TIMESTAMPTZ NOT NULL
);
