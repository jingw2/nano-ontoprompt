-- Synthetic MySQL schema for the runtime fixture corpus.
-- Test infrastructure only. Never used against a production database.

CREATE TABLE managed_targets (
    target_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    row_version INT NOT NULL DEFAULT 1,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB;

CREATE TABLE refresh_source_rows (
    row_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    watermark DATETIME NOT NULL,
    sequence_no INT NOT NULL,
    payload_summary VARCHAR(128) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE refresh_event_log (
    event_id VARCHAR(64) PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    resource VARCHAR(64) NOT NULL,
    cursor_value VARCHAR(64) NOT NULL,
    sequence_no INT NOT NULL,
    received_at DATETIME NOT NULL
) ENGINE=InnoDB;
