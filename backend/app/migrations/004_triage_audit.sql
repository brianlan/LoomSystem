-- Extend triage_runs with audit fields (OBS-2: record input, raw response, ranking, retry/failure).
ALTER TABLE triage_runs ADD COLUMN input_snapshot_json TEXT;
ALTER TABLE triage_runs ADD COLUMN raw_response TEXT;
ALTER TABLE triage_runs ADD COLUMN status TEXT NOT NULL DEFAULT 'success';
ALTER TABLE triage_runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1;
ALTER TABLE triage_runs ADD COLUMN error TEXT;
